// Real public page and WXML, isolated from WeChat and production APIs.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const test = require('node:test')
const { parseWxml, createRenderer } = require('../../tools/preview-miniprogram.js')

const root = path.resolve(__dirname, '../..')
const pageFile = path.join(root, 'miniprogram/pages/match/match.js')
const template = fs.readFileSync(path.join(root, 'miniprogram/pages/match/match.wxml'), 'utf8')
const clone = value => JSON.parse(JSON.stringify(value))

function loadPage(methods = {}, token = 'test-token') {
  let page
  const calls = { modals: [], toasts: [], stoppedRefreshes: 0 }
  const api = new Proxy(methods, { get(target, key) {
    if (key in target) return target[key]
    throw new Error('Unexpected API: ' + String(key))
  } })
  vm.runInNewContext(fs.readFileSync(pageFile, 'utf8'), {
    Page: value => { page = value },
    require(name) {
      if (name.endsWith('/api.js')) return api
      if (name.endsWith('/tournament.js')) return require(path.resolve(path.dirname(pageFile), name))
      throw new Error('Unexpected module: ' + name)
    },
    wx: {
      getStorageSync: () => token,
      showModal: value => calls.modals.push(value),
      showToast: value => calls.toasts.push(value),
      stopPullDownRefresh: () => { calls.stoppedRefreshes++ }
    },
    console
  }, { filename: pageFile })
  page.data = clone(page.data)
  page.setData = values => Object.assign(page.data, clone(values))
  return { page, calls }
}

function detail(overrides = {}) {
  return { match: { id: 8, status: 'registering', statusText: '报名中', max_teams: 16 }, teams: [], rounds: [], ...overrides }
}

function waitingTeam(id = 10) {
  return { team_id: id, team_name: '待编排队' + id, registration_status: 'approved', stage: 'challenger', seed: 0, group_name: null, wins: 0, losses: 0, diff: 0, rating: 1000 }
}

function applyDetail(page, value, captain = false) {
  page.detail = value
  page.setData({ match: value.match })
  page.buildData(value, captain ? 10 : null, captain)
}

function render(page, section) {
  const source = section === 'registration'
    ? template.split('<!-- 报名区 -->')[1].split('<!-- 阶段 tab -->')[0]
    : template
  const tree = parseWxml(source)
  tree.scope = page.data
  return createRenderer({})(tree)
}

function deferred() {
  let resolve
  const promise = new Promise(done => { resolve = done })
  return { promise, resolve }
}

test('approved unseeded teams appear as 待分组 and remain in totals and ranking', () => {
  const { page } = loadPage()
  applyDetail(page, detail({ teams: [waitingTeam(10), waitingTeam(11)] }))
  assert.equal(page.data.teams.length, 2)
  assert.equal(page.data.waitingTeams.length, 2)
  assert.equal(page.data.rankingTeams.length, 2)
  assert.ok(page.data.rankingTeams.every(team => team.stage_text === '待分组'))
  assert.equal(page.data.challengerTeams.length, 0)
  assert.equal(page.data.teamsSwiper[0].teams.length, 0)
  assert.equal(page.data.rosterLocked, false)
  const html = render(page)
  assert.match(html, /待分组队伍 · 2/)
  assert.match(html, /待编排队10/)
  assert.doesNotMatch(html, /进行中/)
  assert.doesNotMatch(html, /附加赛/)
})

test('seeded top four and actual challenger groups replace the waiting section', () => {
  const { page } = loadPage()
  const teams = Array.from({ length: 8 }, (_, index) => ({ ...waitingTeam(index + 1), seed: index + 1,
    stage: index < 4 ? 'legend' : 'challenger', group_name: index < 4 ? null : ['松林', '竹林', '银杏', '白杨'][index - 4] }))
  applyDetail(page, detail({ teams }))
  assert.equal(page.data.waitingTeams.length, 0)
  assert.equal(page.data.legendTeams.length, 4)
  assert.equal(page.data.challengerTeams.length, 4)
  assert.equal(page.data.rankingTeams.length, 8)
  assert.equal(page.data.groupCount, 4)
  assert.equal(page.data.hasPlayoffStage, false)
  assert.equal(page.data.rosterLocked, true)
  assert.doesNotMatch(render(page), /待分组队伍/)
})

test('public details without the new flag still lock on assigned seeds, groups or rounds', () => {
  for (const value of [
    detail({ teams: [{ ...waitingTeam(), seed: 1 }] }),
    detail({ teams: [{ ...waitingTeam(), group_name: '松林' }] }),
    detail({ teams: [{ ...waitingTeam(), stage: 'legend' }] }),
    detail({ rounds: [{ id: 1, group_name: 'A', status: 'pending' }] }),
    detail({ match: { id: 8, status: 'registering', roster_locked: true } })
  ]) {
    const { page } = loadPage()
    applyDetail(page, value, true)
    assert.equal(page.data.rosterLocked, true)
    assert.equal(page.data.canRegisterTeam, false)
  }
})

test('locked captains see the roster explanation and cannot bypass it through the handler', async () => {
  const { page, calls } = loadPage()
  applyDetail(page, detail({ teams: [{ ...waitingTeam(), seed: 1 }] }), true)
  page.setData({ myTeam: { id: 10, name: '测试队' }, isCaptain: true })
  const html = render(page, 'registration')
  assert.match(html, /参赛名单已锁定/)
  assert.doesNotMatch(html, />队伍报名</)
  assert.doesNotMatch(html, />个人报名</)
  await page.handleRegister()
  assert.equal(calls.modals.length, 1)
  assert.equal(calls.modals[0].title, '参赛名单已锁定')
})

test('locked events retain existing team and personal registration information', () => {
  for (const teamId of [10, null]) {
    const { page } = loadPage()
    applyDetail(page, detail({ match: { id: 8, status: 'registering', roster_locked: true } }), true)
    page.setData({ myTeam: { id: 10 }, registered: true, myReg: { status: 'approved', team_id: teamId } })
    const html = render(page, 'registration')
    assert.match(html, /已报名/)
    assert.match(html, teamId ? /随队伍/ : /个人/)
    assert.match(html, /参赛名单已锁定/)
    assert.doesNotMatch(html, />队伍报名</)
  }
})

test('the roster lock still allows a free player to register individually', async () => {
  const submitted = []
  const { page, calls } = loadPage({
    getMe: async () => ({ id: 1, is_verified: true, rank: 'A' }),
    registerUser: async matchId => submitted.push(matchId),
    getMyRegistration: async () => ({ registered: true, registration: { status: 'approved', team_id: null } }),
    getMyTeam: async () => null
  })
  applyDetail(page, detail({ match: { id: 8, status: 'registering', roster_locked: true } }))
  assert.match(render(page, 'registration'), />个人报名</)
  await page.handleRegister()
  assert.equal(calls.modals[0].title, '个人报名')
  await calls.modals[0].success({ confirm: true })
  assert.deepEqual(submitted, [8])
  assert.equal(page.data.registered, true)
})

test('a roster lock received while the confirmation is open blocks the submission', async () => {
  let submitted = 0
  const { page, calls } = loadPage({
    getMe: async () => ({ id: 1, is_verified: true, rank: 'A' }),
    registerTeam: async () => { submitted++ }
  })
  applyDetail(page, detail(), true)
  page.setData({ myTeam: { id: 10 }, isCaptain: true })
  await page.handleRegister()
  assert.equal(calls.modals[0].title, '队伍报名')
  applyDetail(page, detail({ teams: [{ ...waitingTeam(), seed: 1 }] }), true)
  await calls.modals[0].success({ confirm: true })
  assert.equal(submitted, 0)
  assert.equal(calls.modals[1].title, '参赛名单已锁定')
})

test('a fresh ungrouped detail clears the old lock and restores a captain action', async () => {
  const { page } = loadPage({
    getMyRegistration: async () => ({ registered: false, registration: null }),
    getMyTeam: async () => ({ id: 10, captain_id: 1 }),
    getMe: async () => ({ id: 1 })
  })
  applyDetail(page, detail({ teams: [{ ...waitingTeam(), seed: 1 }] }), true)
  assert.equal(page.data.rosterLocked, true)
  applyDetail(page, detail({ teams: [waitingTeam()] }), true)
  await page.loadMyStatus()
  assert.equal(page.data.rosterLocked, false)
  assert.equal(page.data.canRegisterTeam, true)
  assert.match(render(page, 'registration'), />队伍报名</)
})

test('late registration responses cannot replace the newest personal/team status', async () => {
  const pending = deferred()
  let requests = 0
  const { page } = loadPage({
    getMyRegistration: () => ++requests === 1 ? pending.promise : Promise.resolve({ registered: true, registration: { status: 'approved', team_id: 10 } }),
    getMyTeam: async () => ({ id: 10, captain_id: 1 }),
    getMe: async () => ({ id: 1 })
  })
  applyDetail(page, detail())
  const first = page.loadMyStatus()
  await page.loadMyStatus()
  pending.resolve({ registered: false, registration: null })
  await first
  assert.equal(page.data.registered, true)
  assert.equal(page.data.myReg.team_id, 10)
  assert.equal(page.data.canRegisterTeam, false)
})

test('late detail refreshes cannot overwrite a newer grouped roster', async () => {
  const old = deferred()
  let requests = 0
  const { page, calls } = loadPage({
    getMatchDetail: () => ++requests === 1 ? old.promise : Promise.resolve(detail({ teams: [{ ...waitingTeam(), seed: 1 }] }))
  }, '')
  applyDetail(page, detail())
  const first = page.onPullDownRefresh()
  await page.onPullDownRefresh()
  old.resolve(detail({ teams: [waitingTeam()] }))
  await first
  assert.equal(page.data.rosterLocked, true)
  assert.equal(page.data.teams[0].seed, 1)
  assert.equal(calls.stoppedRefreshes, 2)
})
