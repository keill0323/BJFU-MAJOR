// Run from any directory: node --test backend/tests/test_frontend_regressions.js
// Load the real mini-program pages with isolated wx/API mocks; no network requests.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const test = require('node:test')

const root = path.resolve(__dirname, '../..')

function loadPage(relativePath, apiMethods, options = {}) {
  const calls = { modals: [], modalWork: [], toasts: [], reLaunches: [] }
  const api = new Proxy(apiMethods, {
    get(target, key) {
      if (key in target) return target[key]
      throw new Error('Unexpected API access: ' + String(key))
    }
  })
  const wx = {
    getStorageSync: () => 'test-token',
    showToast: value => calls.toasts.push(value),
    reLaunch: value => calls.reLaunches.push(value),
    showModal(value) {
      calls.modals.push(value)
      if (options.confirmModals && value.success) {
        calls.modalWork.push(Promise.resolve(value.success({ confirm: true })))
      }
    }
  }
  let page
  const file = path.join(root, relativePath)
  vm.runInNewContext(fs.readFileSync(file, 'utf8'), {
    Page: definition => { page = definition },
    require(modulePath) {
      if (modulePath.endsWith('/api.js')) return api
      if (modulePath.endsWith('/rank.js')) return require(path.join(root, 'miniprogram/utils/rank.js'))
      if (modulePath.endsWith('/tournament.js')) return require(path.join(root, 'miniprogram/utils/tournament.js'))
      throw new Error('Unexpected module: ' + modulePath)
    },
    wx,
    console
  }, { filename: file })
  page.data = JSON.parse(JSON.stringify(page.data))
  page.setData = values => Object.assign(page.data, values)
  return { page, calls }
}

// Evaluate the actual WXML conditional chain, so changing JS alone cannot make
// the registration test pass while the template still hides the button.
function visibleRegistrationBranch(data) {
  const wxml = fs.readFileSync(path.join(root, 'miniprogram/pages/match/match.wxml'), 'utf8')
  const block = wxml.match(/<!-- 报名区 -->([\s\S]*?)<!-- 阶段 tab -->/)[1]
  const branches = [...block.matchAll(/<(?:view|text) wx:(if|elif|else)(?:="\{\{([^"\n]+)\}\}")?([^>]*)>/g)]
  for (const branch of branches) {
    if (branch[1] === 'else' || vm.runInNewContext(branch[2], data)) return branch[3]
  }
  throw new Error('No registration branch rendered')
}

test('a captain can upgrade personal registration to team registration through the visible button', async () => {
  let registration = { team_id: null, status: 'approved' }
  const submitted = []
  const { page, calls } = loadPage('miniprogram/pages/match/match.js', {
    getMyRegistration: async () => ({ registered: true, registration }),
    getMyTeam: async () => ({ id: 10, captain_id: 1, name: '测试队伍' }),
    getMe: async () => ({ id: 1, is_verified: true, rank: 'A' }),
    registerTeam: async (matchId, teamId) => {
      submitted.push([matchId, teamId])
      registration = { team_id: teamId, status: 'pending' }
    }
  }, { confirmModals: true })
  page.data.match = { id: 8, status: 'registering' }

  await page.loadMyStatus()
  assert.equal(page.data.isCaptain, true)
  assert.match(visibleRegistrationBranch(page.data), /bindtap="handleRegister"/)
  await page.handleRegister()
  await Promise.all(calls.modalWork)

  assert.deepEqual(submitted, [[8, 10]])
  assert.equal(page.data.canRegisterTeam, false)
  assert.match(visibleRegistrationBranch(page.data), /class="register-done"/)
})

test('team members cannot submit team registration and closed matches hide the captain action', async () => {
  let currentUserId = 2
  let registered = false
  const { page } = loadPage('miniprogram/pages/match/match.js', {
    getMyRegistration: async () => ({ registered, registration: registered ? { team_id: null } : null }),
    getMyTeam: async () => ({ id: 10, captain_id: 1 }),
    getMe: async () => ({ id: currentUserId })
  })
  page.data.match = { id: 8, status: 'registering' }
  await page.loadMyStatus()
  assert.equal(page.data.canRegisterTeam, false)
  assert.match(visibleRegistrationBranch(page.data), /class="register-disabled"/)

  currentUserId = 1
  registered = true
  await page.loadMyStatus()
  page.data.match.status = 'finished'
  assert.match(visibleRegistrationBranch(page.data), /class="register-done"/)
})

test('saved stage windows render and populate edits before any rounds exist', async () => {
  const requested = []
  const saved = [{
    group_name: '上区',
    window_start: '2026-10-01T18:00:00',
    window_end: '2026-10-03T23:00:00'
  }, {
    group_name: '自定义组',
    window_start: '2026-10-04T18:00:00',
    window_end: '2026-10-05T23:00:00'
  }]
  const { page } = loadPage('miniprogram/pages/admin/mdetail/mdetail.js', {
    getStageWindows: async matchId => { requested.push(matchId); return saved }
  })
  page.data.matchId = 8
  page.data.allRounds = []
  // A is a real group assigned to this event, even before rounds are generated.
  page.data.teams = [{ team_id: 10, stage: 'challenger', group_name: 'A' }]
  await page.openWindowPanel()
  assert.deepEqual(requested, [8])
  assert.equal(page.data.showWindowPanel, true)
  assert.equal(page.data.stageWindows.find(w => w.group_name === '上区').has_window, true)
  assert.equal(page.data.stageWindows.find(w => w.group_name === '自定义组').has_window, true)
  assert.equal(page.data.stageWindows.find(w => w.group_name === 'A').has_window, false)
  page.openWindowEdit({ currentTarget: { dataset: { group: '上区' } } })
  assert.equal(page.data.windowStartDate, '2026-10-01')
  assert.equal(page.data.windowStartTime, '18:00')
  assert.equal(page.data.windowEndDate, '2026-10-03')
  assert.equal(page.data.windowEndTime, '23:00')

  // Reopening must use the latest independent record, not a stale round snapshot.
  page.closeWindowPanel()
  page.data.allRounds = [{ group_name: '上区', window_start: '2025-01-01T00:00:00', window_end: '2025-01-02T00:00:00' }]
  saved[0].window_start = '2026-10-02T19:00:00'
  await page.openWindowPanel()
  assert.equal(page.data.stageWindows.find(w => w.group_name === '上区').window_start, '2026-10-02T19:00:00')
})

test('failed stage-window reads report an error instead of presenting empty saved windows', async () => {
  const { page, calls } = loadPage('miniprogram/pages/admin/mdetail/mdetail.js', {
    getStageWindows: async () => { throw { detail: '读取失败，请重试' } }
  })
  await page.openWindowPanel()
  assert.equal(page.data.showWindowPanel, false)
  assert.equal(calls.modals[0].content, '读取失败，请重试')
})

test('registration sends unverified users to the dedicated verification page', async () => {
  const { page, calls } = loadPage('miniprogram/pages/match/match.js', {
    getMe: async () => ({ id: 1, is_verified: false })
  }, { confirmModals: true })
  page.data.match = { id: 8, status: 'registering' }
  await page.handleRegister()
  await Promise.all(calls.modalWork)
  assert.equal(calls.reLaunches[0].url, '/pages/verify/verify')
})

test('first-time rank review gives an actionable pending-review message and refreshes the user', async () => {
  const { page, calls } = loadPage('miniprogram/pages/verify/verify.js', {
    uploadRank: async () => ({ rank: null, auto_applied: false, need_review: true }),
    getMe: async () => ({ id: 1, rank: null })
  })
  await page.uploadRankWithPath('local-test-image.jpg')
  assert.equal(calls.modals[0].title, '已提交段位审核')
  assert.match(calls.modals[0].content, /消息页查看审核结果/)
  assert.equal(page.data.user.id, 1)
  assert.equal(page.data.uploading, false)
})

for (const relativePath of ['miniprogram/pages/match/match.js', 'miniprogram/pages/admin/mdetail/mdetail.js']) {
  test(relativePath + ' awards champion and runner-up only after the final is completed', async () => {
    const final = {
      id: 5, round_number: 5, group_name: '淘汰赛', status: 'pending',
      team1_id: 1, team2_id: 4, winner_id: null, team1_score: null, team2_score: null
    }
    const detail = {
      match: { id: 8, status: 'in_progress' },
      teams: [1, 2, 3, 4, 5, 6].map(id => ({
        team_id: id, team_name: '队伍' + id, group_name: '淘汰赛', seed: id,
        stage: id === 1 || id === 4 ? 'playoff' : 'eliminated', wins: 2, losses: 0
      })),
      rounds: [[2, 3, 2], [5, 6, 5], [1, 5, 1], [4, 2, 4]].map(([team1_id, team2_id, winner_id], index) => ({
        id: index + 1, round_number: index + 1, group_name: '淘汰赛', status: 'finished',
        team1_id, team2_id, winner_id, team1_score: 2, team2_score: 0
      })).concat(final)
    }
    const { page, calls } = loadPage(relativePath, { adminMatchDetail: async () => detail })
    const refresh = async () => {
      if (page.buildData) page.buildData(detail, null, false)
      else await page.load()
    }

    // Creating the final (and later starting it) does not yet determine medals.
    for (const status of ['pending', 'in_progress']) {
      final.status = status
      await refresh()
      assert.equal(page.data.rankingTeams.some(t => ['冠军', '亚军'].includes(t.ko_rank)), false)
    }

    final.status = 'finished'
    final.winner_id = 4
    final.team1_score = 1
    final.team2_score = 2
    await refresh()
    assert.equal(page.data.rankingTeams.find(t => t.team_id === 4).ko_rank, '冠军')
    assert.equal(page.data.rankingTeams.find(t => t.team_id === 1).ko_rank, '亚军')
    assert.equal(page.data.rankingTeams[0].team_id, 4)
    assert.equal(page.data.rankingTeams[1].team_id, 1)
    assert.equal(calls.modals.length, 0, 'fixture loading must not hide a page error')
  })
}
