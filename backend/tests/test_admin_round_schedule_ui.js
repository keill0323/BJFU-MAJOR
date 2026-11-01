// Admin scheduling uses Beijing wall time and an expected-time compare before writing.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const test = require('node:test')
const { parseWxml, createRenderer } = require('../../tools/preview-miniprogram.js')
const root = path.resolve(__dirname, '../..')
const base = path.join(root, 'miniprogram/pages/admin/mdetail/mdetail')
const tree = parseWxml(fs.readFileSync(base + '.wxml', 'utf8'))
const tap = dataset => ({ currentTarget: { dataset } })
const plain = value => JSON.parse(JSON.stringify(value))
const originalTime = '2026-10-18T13:00:00'
const fixtureRound = overrides => ({ id: 6, group_name: 'A', round_number: 6, team1_id: 11, team2_id: 12,
  team1_name: '北林队', team2_name: '五区队', status: 'pending', scheduled_time: originalTime,
  schedule_status: 'confirmed', team1_confirmed: true, team2_confirmed: true, ...overrides })

function setup({ rounds = [fixtureRound()], save = async () => ({}), read } = {}) {
  let page
  const detail = { match: { id: 8, name: '起源杯' }, teams: [], rounds }
  const calls = { saves: [], reads: 0, modals: [], toasts: [] }
  vm.runInNewContext(fs.readFileSync(base + '.js', 'utf8'), {
    Page: definition => { page = definition },
    require(name) {
      if (name.endsWith('/tournament.js')) return require(path.join(root, 'miniprogram/utils/tournament.js'))
      assert.equal(name, '../../../utils/api.js')
      return {
        adminMatchDetail: async () => { calls.reads++; return read ? read(detail) : detail },
        adminScheduleRound: (id, time, expected) => { calls.saves.push([id, time, expected]); return save(id, time, expected) }
      }
    },
    wx: { showToast: value => calls.toasts.push(value), showModal: value => calls.modals.push(value) }
  }, { filename: base + '.js' })
  page.data = plain(page.data)
  page.data.matchId = 8
  page.data.allRounds = plain(rounds)
  page.setData = values => Object.assign(page.data, values)
  return { page, calls, detail }
}

function edit(page, date = '2026-10-18', time = '16:00') {
  page.onRoundScheduleInput({ ...tap({ field: 'roundScheduleDate' }), detail: { value: date } })
  page.onRoundScheduleInput({ ...tap({ field: 'roundScheduleTime' }), detail: { value: time } })
}

function render(page) { return createRenderer({})({ children: tree.children, scope: page.data }) }

test('only pending two-team games expose admin time controls in every active stage', async () => {
  for (const [tab, group_name] of [['challenger', 'A'], ['legend', '上区'], ['knockout', '淘汰赛']]) {
    const rounds = [fixtureRound({ group_name }), fixtureRound({ id: 7, group_name, round_number: 2, scheduled_time: null }),
      fixtureRound({ id: 8, group_name, round_number: 3, status: 'finished' }),
      fixtureRound({ id: 9, group_name, round_number: 4, status: 'in_progress' }),
      fixtureRound({ id: 10, group_name, round_number: 5, team2_id: null })]
    const { page } = setup({ rounds })
    await page.load()
    page.switchTab(tap({ tab }))
    const html = render(page)
    assert.equal((html.match(/>调整时间<\/div>/g) || []).length, 1)
    assert.equal((html.match(/>设置时间<\/div>/g) || []).length, 1)
    assert.match(html, /开赛时间：10-18 13:00（北京时间）/)
    for (const id of [8, 9, 10, 999]) {
      page.openRoundSchedule(tap({ id }))
      assert.equal(page.data.showRoundSchedule, false)
    }
  }
})

test('editor retains the raw concurrency value, shows both teams, and sends unshifted Beijing time', async () => {
  const { page, calls } = setup()
  page.openRoundSchedule(tap({ id: 6 }))
  assert.equal(page.data.roundScheduleDate, '2026-10-18')
  assert.equal(page.data.roundScheduleTime, '13:00')
  assert.equal(page.data.roundScheduleExpectedTime, originalTime)
  assert.equal(page.data.roundScheduleTeams, '北林队 vs 五区队')
  assert.match(render(page), /管理员统一安排，无需队长确认/)
  edit(page)
  await page.saveRoundSchedule()
  assert.deepEqual(calls.saves, [[6, '2026-10-18T16:00:00', originalTime]])
  assert.equal(page.data.showRoundSchedule, false)
  assert.equal(page.data.roundScheduleSaving, false)
  assert.equal(calls.reads, 1)
})

test('legacy unconfirmed proposals are labelled as historical until an administrator confirms the arrangement', async () => {
  const { page, calls } = setup({ rounds: [fixtureRound({ schedule_status: 'pending', team2_confirmed: false })] })
  await page.load()
  page.switchTab(tap({ tab: 'challenger' }))
  assert.match(render(page), /历史提议 10-18 13:00（北京时间），待管理员确认安排/)
  assert.doesNotMatch(render(page), /开赛时间：10-18 13:00/)
  page.openRoundSchedule(tap({ id: 6 }))
  assert.match(page.data.roundScheduleCurrentText, /^历史提议/)
  assert.equal(page.data.roundScheduleExpectedTime, originalTime)
  await page.saveRoundSchedule()
  assert.deepEqual(calls.saves, [[6, originalTime, originalTime]])
})

test('first schedule sends expected null and partial picker input never writes', async () => {
  const { page, calls } = setup({ rounds: [fixtureRound({ scheduled_time: null })] })
  page.openRoundSchedule(tap({ id: 6 }))
  edit(page, '2026-10-18', '')
  await page.saveRoundSchedule()
  assert.equal(calls.saves.length, 0)
  assert.match(calls.toasts[0].title, /完整/)
  edit(page)
  await page.saveRoundSchedule()
  assert.deepEqual(calls.saves, [[6, '2026-10-18T16:00:00', null]])
})

test('clear is an explicit null update guarded by the original timestamp', async () => {
  const { page, calls } = setup()
  page.openRoundSchedule(tap({ id: 6 }))
  await page.clearRoundSchedule()
  assert.deepEqual(calls.saves, [[6, null, originalTime]])
  assert.equal(calls.toasts[0].title, '比赛时间已清除')
  assert.equal(page.data.showRoundSchedule, false)
})

test('save, clear, close, and opening another round cannot race an in-flight update', async () => {
  let finish
  const { page, calls } = setup({ save: () => new Promise(resolve => { finish = resolve }) })
  page.openRoundSchedule(tap({ id: 6 }))
  edit(page)
  const pending = page.saveRoundSchedule()
  await page.saveRoundSchedule()
  await page.clearRoundSchedule()
  page.closeRoundSchedule()
  page.openRoundSchedule(tap({ id: 999 }))
  edit(page, '2026-10-19', '17:00')
  assert.equal(calls.saves.length, 1)
  assert.equal(page.data.roundScheduleId, 6)
  assert.equal(page.data.showRoundSchedule, true)
  assert.equal(page.data.roundScheduleDate, '2026-10-18')
  finish()
  await pending
  assert.equal(page.data.roundScheduleSaving, false)
})

test('a conflict preserves the draft and requires an explicit refresh before another save', async () => {
  let attempt = 0
  const { page, calls, detail } = setup({ save: async () => { if (++attempt === 1) throw { statusCode: 409 } } })
  page.openRoundSchedule(tap({ id: 6 }))
  edit(page)
  await page.saveRoundSchedule()
  assert.equal(page.data.roundScheduleConflict, true)
  assert.equal(page.data.roundScheduleDate, '2026-10-18')
  assert.equal(page.data.roundScheduleTime, '16:00')
  assert.match(calls.modals[0].content, /刷新当前排期/)
  await page.saveRoundSchedule()
  assert.equal(calls.saves.length, 1)
  detail.rounds[0].scheduled_time = '2026-10-18T15:00:00'
  await page.refreshRoundSchedule()
  assert.equal(page.data.roundScheduleExpectedTime, '2026-10-18T15:00:00')
  assert.equal(page.data.roundScheduleCurrentText, '10-18 15:00（北京时间）')
  assert.equal(page.data.roundScheduleTime, '16:00')
  assert.equal(page.data.roundScheduleConflict, false)
  await page.saveRoundSchedule()
  assert.deepEqual(calls.saves[1], [6, '2026-10-18T16:00:00', '2026-10-18T15:00:00'])
})

test('failed save and failed conflict refresh preserve both draft and concurrency guard', async () => {
  const { page, calls } = setup({ save: async () => { throw { detail: '服务暂不可用' } }, read: async () => { throw { detail: '刷新失败' } } })
  page.openRoundSchedule(tap({ id: 6 }))
  edit(page)
  await page.saveRoundSchedule()
  assert.equal(page.data.roundScheduleSaving, false)
  assert.equal(page.data.showRoundSchedule, true)
  assert.equal(page.data.roundScheduleTime, '16:00')
  assert.equal(calls.modals[0].content, '服务暂不可用')
  page.data.roundScheduleConflict = true
  await page.refreshRoundSchedule()
  assert.equal(page.data.roundScheduleConflict, true)
  assert.equal(page.data.roundScheduleExpectedTime, originalTime)
  assert.equal(page.data.roundScheduleSaving, false)
})

test('a real missing-route 404 explains the backend upgrade and never falls back or reports success', async () => {
  const requests = []
  const apiModule = { exports: {} }
  vm.runInNewContext(fs.readFileSync(path.join(root, 'miniprogram/utils/api.js'), 'utf8'), {
    module: apiModule,
    wx: {
      getStorageSync: () => '',
      request(options) {
        requests.push({ url: options.url, method: options.method, data: plain(options.data) })
        options.success({ statusCode: 404, data: { detail: 'Not Found' } })
      }
    }, console
  })
  const { page, calls } = setup({ save: apiModule.exports.adminScheduleRound })
  page.openRoundSchedule(tap({ id: 6 }))
  edit(page)
  await page.saveRoundSchedule()
  assert.equal(requests.length, 1)
  assert.match(requests[0].url, /\/api\/matches\/rounds\/6\/admin-schedule$/)
  assert.equal(requests[0].method, 'PUT')
  assert.deepEqual(requests[0].data, { scheduled_time: '2026-10-18T16:00:00', expected_time: originalTime })
  assert.match(calls.modals[0].content, /当前服务器版本不支持管理员逐场排期/)
  assert.match(calls.modals[0].content, /更新后端/)
  assert.equal(calls.toasts.length, 0)
  assert.equal(calls.reads, 0)
  assert.equal(page.data.showRoundSchedule, true)
  assert.equal(page.data.roundScheduleTime, '16:00')
  assert.equal(page.data.roundScheduleExpectedTime, originalTime)
  assert.equal(page.data.roundScheduleSaving, false)
})

test('late save results do not affect another page after admin detail unloads', async () => {
  let finish
  const { page, calls } = setup({ save: () => new Promise(resolve => { finish = resolve }) })
  page.openRoundSchedule(tap({ id: 6 }))
  edit(page)
  const pending = page.saveRoundSchedule()
  page.onUnload()
  page.setData = () => assert.fail('unloaded page cannot render')
  finish()
  await pending
  assert.equal(calls.toasts.length, 0)
  assert.equal(calls.reads, 0)
})

test('the admin time editor keeps a native scrolling body and separate fixed controls', () => {
  function nodes(node, predicate) { return (predicate(node) ? [node] : []).concat(...(node.children || []).map(child => nodes(child, predicate))) }
  const panel = nodes(tree, node => node.attrs?.class === 'score-panel admin-round-schedule-panel')[0]
  assert.ok(panel)
  assert.equal(panel.attrs.catchtouchmove, undefined)
  const scroll = panel.children.find(node => node.tag === 'scroll-view')
  assert.equal(scroll.attrs['scroll-y'], '{{true}}')
  assert.equal(nodes(scroll, node => node.attrs?.bindtap === 'saveRoundSchedule').length, 0)
  const footer = panel.children.find(node => node.attrs?.class === 'score-btns score-footer')
  const confirm = nodes(footer, node => node.attrs?.bindtap === 'saveRoundSchedule')[0]
  assert.equal(confirm.attrs.disabled, '{{roundScheduleSaving || roundScheduleConflict}}')
})
