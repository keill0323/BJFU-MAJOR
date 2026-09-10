// These tests run real page handlers and WXML against isolated, in-memory APIs.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const test = require('node:test')
const { parseWxml, createRenderer } = require('../../tools/preview-miniprogram.js')
const root = path.resolve(__dirname, '../..')
const plain = value => JSON.parse(JSON.stringify(value))
const event = id => ({ currentTarget: { dataset: { id, name: '报名队伍' } } })
const approved = (id, extra = {}) => ({
  team_id: id, team_name: '队伍' + id, registration_status: 'approved',
  stage: 'challenger', seed: null, group_name: null, wins: 0, losses: 0, diff: 0,
  ...extra
})
const details = teams => ({ match: { id: 7, name: '测试赛事', status: 'registering' }, teams, rounds: [] })

function loadPage(methods = {}, todoMethods = {}) {
  let page
  const calls = { modals: [], toasts: [], todoInvalidations: 0 }
  const file = path.join(root, 'miniprogram/pages/admin/mdetail/mdetail.js')
  vm.runInNewContext(fs.readFileSync(file, 'utf8'), {
    Page: definition => { page = definition },
    require(name) {
      if (name.endsWith('/admin-todos.js')) return { invalidate: () => {
        calls.todoInvalidations++
        if (todoMethods.invalidate) todoMethods.invalidate()
      } }
      if (name.endsWith('/tournament.js')) return require(path.join(root, 'miniprogram/utils/tournament.js'))
      assert.equal(name, '../../../utils/api.js')
      return new Proxy(methods, { get(target, key) {
        if (key in target) return target[key]
        throw new Error('Unexpected API call: ' + String(key))
      } })
    },
    wx: { showModal: value => calls.modals.push(value), showToast: value => calls.toasts.push(value) },
    console
  }, { filename: file })
  page.data = { ...plain(page.data), matchId: 7 }
  page.setData = values => Object.assign(page.data, values)
  return { page, calls }
}

function render(page) {
  const file = path.join(root, 'miniprogram/pages/admin/mdetail/mdetail.wxml')
  const tree = parseWxml(fs.readFileSync(file, 'utf8'))
  tree.scope = page.data
  return createRenderer({})(tree)
}

test('approved unseeded teams render as waiting and are absent from challenger lists', async () => {
  const detail = details([approved(1), approved(2, { registration_status: 'pending' }), approved(3, { registration_status: 'rejected' })])
  const { page, calls } = loadPage({ adminMatchDetail: async () => detail })
  await page.load()
  assert.equal(calls.modals.length, 0)
  assert.deepEqual(plain(page.data.waitingTeams.map(t => t.team_id)), [1])
  assert.equal(page.data.challengerTeams.length, 0)
  assert.equal(page.data.teamsSwiper[0].teams.length, 0)
  assert.equal(page.data.rankingTeams.find(t => t.team_id === 1).stage_text, '待分组')
  assert.equal(page.data.rankingTeams.find(t => t.team_id === 2).stage_text, '报名待审核')
  assert.match(render(page), /待分组 · 1 支/)
  assert.match(render(page), /通过报名/)
  page.data.tab = 'challenger'
  const html = render(page)
  assert.match(html, /暂无挑战者队伍/)
  assert.doesNotMatch(html, /队伍1|队伍2|队伍3/)
})

test('seeded teams belong to the actual stages and no longer appear as waiting', async () => {
  const detail = details([approved(1, { stage: 'legend', seed: 1 }), approved(2, { seed: 5, group_name: 'A' })])
  const { page } = loadPage({ adminMatchDetail: async () => detail })
  await page.load()
  assert.equal(page.data.waitingTeams.length, 0)
  assert.equal(page.data.rosterLocked, true)
  assert.deepEqual(plain(page.data.challengerTeams.map(t => t.team_id)), [2])
  assert.deepEqual(plain(page.data.legendTeams.map(t => t.team_id)), [1])
  assert.equal(page.data.teamsSwiper[0].teams[0].stage_status, '进行中')
  assert.doesNotMatch(render(page), /waiting-card/)
})

test('backend roster lock and old detail seed, group, stage or round evidence hide and block approval', async () => {
  const variants = [
    detail => { detail.match.roster_locked = true },
    detail => { detail.teams.push(approved(2, { seed: 5 })) },
    detail => { detail.teams.push(approved(2, { group_name: '松林组' })) },
    detail => { detail.teams.push(approved(2, { stage: 'legend' })) },
    detail => { detail.rounds.push({ id: 4, group_name: 'A', status: 'pending' }) }
  ]
  for (const mutate of variants) {
    const detail = details([approved(1, { registration_status: 'pending' })])
    mutate(detail)
    let approvals = 0
    const { page, calls } = loadPage({ adminMatchDetail: async () => detail, approveTeamRegistration: async () => { approvals++ } })
    await page.load()
    assert.equal(page.data.rosterLocked, true)
    assert.match(render(page), /参赛名单已锁定/)
    assert.doesNotMatch(render(page), />通过报名</)
    await page.approveTeamReg(event(1))
    assert.equal(approvals, 0)
    assert.equal(calls.modals.length, 0)
    assert.match(calls.toasts.at(-1).title, /参赛名单已锁定/)
  }
})

test('approval confirmation rechecks the roster when grouping completes while the dialog is open', async () => {
  let approvals = 0
  const { page, calls } = loadPage({ approveTeamRegistration: async () => { approvals++ } })
  page.data.teams = [approved(1, { registration_status: 'pending' })]
  await page.approveTeamReg(event(1))
  assert.match(calls.modals[0].content, /待分组/)
  page.data.rosterLocked = true
  await calls.modals[0].success({ confirm: true })
  assert.equal(approvals, 0)
  assert.match(calls.toasts.at(-1).title, /已锁定/)
})

test('approval succeeds before grouping and reloads the team as waiting', async () => {
  const detail = details([approved(1, { registration_status: 'pending' })])
  const sent = []
  const { page, calls } = loadPage({
    adminMatchDetail: async () => detail,
    approveTeamRegistration: async (matchId, teamId) => {
      sent.push([matchId, teamId])
      detail.teams[0].registration_status = 'approved'
    }
  })
  await page.load()
  await page.approveTeamReg(event(1))
  await calls.modals[0].success({ confirm: true })
  assert.deepEqual(sent, [[7, 1]])
  assert.equal(page.data.waitingTeams.length, 1)
  assert.equal(page.data.rosterLocked, false)
  assert.equal(calls.todoInvalidations, 1)
})

test('atomic grouping uses one request, prevents duplicate submission and immediately locks approvals', async () => {
  const sent = []
  let resolveRequest
  let reloads = 0
  const { page, calls } = loadPage({ seedAndGroup: (id, groups) => {
    sent.push([id, plain(groups)])
    return new Promise(resolve => { resolveRequest = resolve })
  } })
  page.load = async () => { reloads++ }
  page.promptGroup()
  const dialog = calls.modals[0]
  const saving = dialog.success({ confirm: true, content: '4' })
  assert.equal(page.data.groupingBusy, true)
  page.promptGroup()
  await dialog.success({ confirm: true, content: '3' })
  assert.equal(calls.modals.length, 1)
  assert.deepEqual(sent, [[7, ['A', 'B', 'C', 'D']]])
  resolveRequest({ message: '分组完成', data: { group_count: 4 } })
  await saving
  assert.equal(reloads, 1)
  assert.equal(page.data.groupingBusy, false)
  assert.equal(page.data.rosterLocked, true)
  assert.match(calls.modals[1].content, /无附加赛/)
  assert.equal(calls.todoInvalidations, 1)
})

test('failed atomic grouping keeps approved teams waiting, shows the error and permits retry', async () => {
  const sent = []
  const { page, calls } = loadPage({ seedAndGroup: async (id, groups) => {
    sent.push([id, plain(groups)])
    throw { detail: '挑战者队伍不足，无法分组' }
  } })
  let reloads = 0
  page.load = async () => { reloads++ }
  page.data.teams = [approved(1)]
  const before = plain(page.data.teams)
  page.promptGroup()
  await calls.modals[0].success({ confirm: true, content: '3' })
  assert.equal(sent.length, 1)
  assert.deepEqual(plain(page.data.teams), before)
  assert.equal(page.data.rosterLocked, false)
  assert.equal(page.data.groupingBusy, false)
  assert.equal(reloads, 0)
  assert.equal(calls.modals[1].content, '挑战者队伍不足，无法分组')
  assert.equal(calls.modals.some(modal => modal.title === '分组完成'), false)
  assert.equal(calls.todoInvalidations, 0)
  page.promptGroup()
  assert.equal(calls.modals.length, 3)
})

test('a successful grouping stays locked when the following detail refresh fails', async () => {
  const { page, calls } = loadPage({
    seedAndGroup: async () => ({ data: { group_count: 3 } }),
    adminMatchDetail: async () => { throw { detail: '详情刷新失败，请稍后重试' } }
  })
  Object.assign(page.data, details([approved(1, { registration_status: 'pending' })]))
  page.promptGroup()
  await calls.modals[0].success({ confirm: true, content: '3' })
  assert.equal(page.data.rosterLocked, true)
  assert.equal(page.data.groupingBusy, false)
  assert.equal(calls.modals[1].title, '分组完成')
  assert.equal(calls.modals[2].content, '详情刷新失败，请稍后重试')
  assert.doesNotMatch(render(page), />通过报名</)
})

test('a failed team approval does not invalidate the existing pending summary', async () => {
  const { page, calls } = loadPage({ approveTeamRegistration: async () => { throw { detail: '报名审核失败' } } })
  page.data.teams = [approved(1, { registration_status: 'pending' })]
  let reloads = 0
  page.load = async () => { reloads++ }
  await page.approveTeamReg(event(1))
  await calls.modals[0].success({ confirm: true })
  assert.equal(calls.todoInvalidations, 0)
  assert.equal(reloads, 0)
  assert.equal(calls.toasts.length, 0)
  assert.equal(calls.modals[1].content, '报名审核失败')
})

test('summary notification failure cannot turn successful approval or grouping into a write error', async () => {
  for (const kind of ['approval', 'grouping']) {
    const writes = []
    const { page, calls } = loadPage({
      approveTeamRegistration: async () => { writes.push('approval') },
      seedAndGroup: async () => { writes.push('grouping') }
    }, { invalidate: () => { throw new Error('summary subscriber failed') } })
    page.data.teams = [approved(1, { registration_status: 'pending' })]
    let reloads = 0
    page.load = async () => { reloads++ }
    if (kind === 'approval') {
      await page.approveTeamReg(event(1))
      await calls.modals[0].success({ confirm: true })
      assert.equal(calls.toasts[0].title, '已通过')
      assert.equal(calls.modals.length, 1)
    } else {
      page.promptGroup()
      await calls.modals[0].success({ confirm: true, content: '3' })
      assert.equal(page.data.rosterLocked, true)
      assert.equal(calls.modals[1].title, '分组完成')
      assert.equal(calls.modals.length, 2)
    }
    assert.deepEqual(writes, [kind])
    assert.equal(calls.todoInvalidations, 1)
    assert.equal(reloads, 1)
  }
})
