// Exercise administration navigation, queue disclosure, and client-side filters without API writes.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const test = require('node:test')
const { parseWxml, createRenderer } = require('../../tools/preview-miniprogram.js')
const root = path.resolve(__dirname, '../..')
const tap = dataset => ({ currentTarget: { dataset } })
const plain = value => JSON.parse(JSON.stringify(value))

function load(name, methods = {}) {
  let page
  const calls = { actions: [], navigation: [], requests: [] }
  const file = path.join(root, 'miniprogram/pages/admin', name + '.js')
  vm.runInNewContext(fs.readFileSync(file, 'utf8'), {
    Page: value => { page = value },
    require(module) {
      if (module.endsWith('/api.js')) return new Proxy(methods, { get(target, key) {
        if (key === 'BASE') return 'https://example.test'
        if (!(key in target)) throw new Error('Unexpected API: ' + key)
        return (...args) => { calls.requests.push([key, ...args]); return target[key](...args) }
      } })
      if (module.endsWith('/tournament.js')) return require(path.join(root, 'miniprogram/utils/tournament.js'))
      if (module.endsWith('/rank.js')) return require(path.join(root, 'miniprogram/utils/rank.js'))
      throw new Error('Unexpected module ' + module)
    },
    wx: { showActionSheet: item => calls.actions.push(item), showToast() {}, showModal() {}, navigateTo: item => calls.navigation.push(item) }
  }, { filename: file })
  page.data = plain(page.data)
  page.setData = (values, callback) => {
    for (const [key, value] of Object.entries(values)) {
      const parts = key.split('.')
      let object = page.data
      for (const part of parts.slice(0, -1)) object = object[part]
      object[parts[parts.length - 1]] = value
    }
    if (callback) callback()
  }
  const render = () => createRenderer({})({ ...parseWxml(fs.readFileSync(file.replace(/\.js$/, '.wxml'), 'utf8')), scope: page.data })
  return { page, calls, render }
}

test('team and match filters combine status and case-insensitive keywords without refetching or losing raw rows', async () => {
  const { page, calls } = load('admin', {
    getAdminTeams: async () => [{ id: 1, name: '北林', captain_name: 'Captain One', status: 'pending' }, { id: 2, name: '银河', captain_name: '队长二', status: 'approved' }],
    getMatches: async () => [{ id: 7, name: '起源杯', status: 'in_progress' }, { id: 8, name: '往届起源杯', status: 'finished' }]
  })
  await page.loadTeams()
  await page.loadMatches()
  page.setListFilter(tap({ kind: 'teams', filter: 'pending' }))
  page.onListKeyword({ ...tap({ kind: 'teams' }), detail: { value: 'CAPTAIN' } })
  assert.deepEqual(plain(page.data.visibleTeams.map(item => item.id)), [1])
  page.onListKeyword({ ...tap({ kind: 'teams' }), detail: { value: '银河' } })
  assert.equal(page.data.visibleTeams.length, 0)
  page.setListFilter(tap({ kind: 'teams', filter: 'all' }))
  assert.equal(page.data.visibleTeams[0].id, 2)
  page.setListFilter(tap({ kind: 'matches', filter: 'finished' }))
  assert.equal(page.data.visibleMatches[0].id, 8)
  page.onListKeyword({ ...tap({ kind: 'matches' }), detail: { value: '7' } })
  assert.equal(page.data.visibleMatches.length, 0)
  assert.equal(page.data.teams.length, 2)
  assert.equal(page.data.matches.length, 2)
  assert.equal(calls.requests.length, 2)
})

test('review queues load no evidence image elements until expanded, then display only one selected credential', async () => {
  const users = [{ id: 8, nickname: '甲', verify_image: '/uploads/a.png' }, { id: 9, nickname: '乙', verify_image: '/uploads/b.png' }]
  const { page, render } = load('admin', { getVerifyList: async () => users })
  page.data.user = { role: 'admin' }
  page.data.tab = 'verify'
  await page.loadVerifyList()
  assert.doesNotMatch(render(), /class="verify-shot"/)
  page.toggleReview(tap({ kind: 'verify', id: 8 }))
  assert.equal((render().match(/class="verify-shot"/g) || []).length, 1)
  page.data.studentIdDrafts[8].saving = true
  page.toggleReview(tap({ kind: 'verify', id: 9 }))
  assert.equal(page.data.expandedVerifyId, 8)
  page.data.studentIdDrafts[8].saving = false
  page.toggleReview(tap({ kind: 'verify', id: 9 }))
  assert.equal(page.data.expandedVerifyId, 9)
  assert.equal((render().match(/class="verify-shot"/g) || []).length, 1)
  users.pop()
  await page.loadVerifyList()
  assert.equal(page.data.expandedVerifyId, 8, 'next available applicant stays open after a completed review')
})

test('user action menu preserves reviewer limits and delegates selected edits to existing handlers', () => {
  const { page, calls } = load('admin')
  page.data.users = [{ id: 8 }]
  page.data.user = { role: 'reviewer' }
  page.showUserActions(tap({ id: 8 }))
  assert.equal(calls.actions[0].itemList.length, 3)
  assert.equal(calls.actions[0].itemList.some(label => label.includes('权限')), false)
  let selectedId
  page.setIdentity = e => { selectedId = e.currentTarget.dataset.id }
  calls.actions[0].success({ tapIndex: 2 })
  assert.equal(selectedId, 8)
  page.data.user.role = 'admin'
  page.showUserActions(tap({ id: 8 }))
  assert.equal(calls.actions[1].itemList[3], '修改管理权限')
  page.onUnload()
  selectedId = null
  calls.actions[1].success({ tapIndex: 2 })
  assert.equal(selectedId, null)
})

test('registration queue opens the team approval tab while ordinary event entry opens the match-day view', () => {
  const { page, calls } = load('admin')
  page.goMDetail(tap({ id: 7, tab: 'teams' }))
  assert.equal(calls.navigation[0].url, '/pages/admin/mdetail/mdetail?matchId=7&tab=teams')
  const detail = load('mdetail/mdetail').page
  detail.load = () => {}
  detail.onLoad({ matchId: '7' })
  assert.equal(detail.data.tab, 'schedule')
  detail.onLoad({ matchId: '7', tab: 'teams' })
  assert.equal(detail.data.tab, 'teams')
})

const round = (id, group_name, overrides = {}) => ({ id, group_name, round_number: id, team1_id: 1, team2_id: 2,
  team1_name: '北林', team2_name: '银河', status: 'pending', team1_score: 0, team2_score: 0,
  scheduled_time: '2026-10-18T13:00:00', schedule_status: 'confirmed', team1_confirmed: true, team2_confirmed: true, ...overrides })

test('match-day date, group and state filters preserve all games and treat historic proposals as unarranged', async () => {
  const rounds = [round(1, 'A'), round(2, 'B', { status: 'finished' }), round(3, 'A', { scheduled_time: '2026-10-19T13:00:00' }),
    round(4, 'A', { scheduled_time: null }), round(5, 'B', { schedule_status: 'pending', team2_confirmed: false }),
    round(6, 'D', { team2_id: null, scheduled_time: null })]
  const { page, calls } = load('mdetail/mdetail', { adminMatchDetail: async () => ({ match: { id: 7 }, teams: [], rounds }) })
  page.beijingToday = () => '2026-10-18'
  await page.load()
  assert.deepEqual(plain(page.data.roundCounts), { all: 6, today: 2, pending: 4, unscheduled: 2 })
  page.setRoundFilter(tap({ filter: 'today' }))
  assert.deepEqual(plain(page.data.filteredRounds.map(item => item.id)), [1, 2], 'only confirmed dated games appear today')
  page.setRoundGroup(tap({ group: 'A' }))
  assert.deepEqual(plain(page.data.filteredRounds.map(item => item.id)), [1])
  page.setRoundFilter(tap({ filter: 'unscheduled' }))
  assert.deepEqual(plain(page.data.filteredRounds.map(item => item.id)), [4])
  page.setRoundGroup(tap({ group: '' }))
  assert.deepEqual(plain(page.data.filteredRounds.map(item => item.id)), [4, 5])
  page.setRoundDate({ detail: { value: '2026-10-19' } })
  assert.deepEqual(plain(page.data.filteredRounds.map(item => item.id)), [3])
  page.resetRoundFilters()
  assert.equal(page.data.filteredRounds.length, 6)
  assert.equal(page.data.allRounds.length, 6)
  assert.equal(calls.requests.length, 1)
})

test('historic playoff operations stay read-only in match day and completed scores can be reviewed before editing', async () => {
  const rounds = [round(1, 'A'), round(2, 'B'), round(3, 'C'), round(4, 'D'), round(5, '附加赛'),
    round(6, '淘汰赛', { status: 'finished', team1_score: 2, team2_score: 1, bo3_scores: [{ t1: 13, t2: 9 }, { t1: 8, t2: 13 }, { t1: 13, t2: 5 }] })]
  const { page } = load('mdetail/mdetail', { adminMatchDetail: async () => ({ match: { id: 7 }, teams: [], rounds }) })
  await page.load()
  const history = page.data.filteredRounds.find(item => item.id === 5)
  assert.equal(history.admin_readonly, true)
  assert.equal(history.admin_can_schedule, false)
  page.openScore(tap({ id: 6 }))
  assert.equal(page.data.isBo3, true)
  assert.deepEqual(plain(page.data.bo3Games), ['13-9', '8-13', '13-5'])
  page.toggleAdminSection(tap({ section: 'challengerFlow' }))
  assert.equal(page.data.expandedSections.challengerFlow, true)
  page.toggleAdminSection(tap({ section: 'not-a-section' }))
  assert.equal(page.data.expandedSections['not-a-section'], undefined)
})
