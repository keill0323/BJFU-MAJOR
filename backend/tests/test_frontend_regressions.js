// Run from any directory: node --test backend/tests/test_frontend_regressions.js
// Load the real mini-program pages with isolated wx/API mocks; no network requests.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const test = require('node:test')
const { parseWxml, createRenderer } = require('../../tools/preview-miniprogram.js')

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
      if (modulePath.endsWith('/image-upload.js')) return { showImageError() { throw new Error('Unexpected image selection failure') } }
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

// Render the actual card and its conditional chain without depending on comment markers.
function visibleRegistrationHtml(data) {
  const wxml = fs.readFileSync(path.join(root, 'miniprogram/pages/match/match.wxml'), 'utf8')
  const tree = parseWxml(wxml)
  function findRegistration(nodes) {
    for (const node of nodes) {
      if ((node.attrs && node.attrs.class || '').split(/\s+/).includes('register-card')) return node
      const nested = node.children && findRegistration(node.children)
      if (nested) return nested
    }
  }
  const registration = findRegistration(tree.children)
  assert.ok(registration, 'the actual template must contain the registration card')
  tree.children = [registration]
  tree.scope = data
  return createRenderer({})(tree)
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
  assert.match(visibleRegistrationHtml(page.data), /class="btn-primary">队伍报名<\/div>/)
  await page.handleRegister()
  await Promise.all(calls.modalWork)

  assert.deepEqual(submitted, [[8, 10]])
  assert.equal(page.data.canRegisterTeam, false)
  assert.match(visibleRegistrationHtml(page.data), /class="register-done"/)
})

test('team members cannot submit team registration and closed matches hide the captain action', async () => {
  let currentUserId = 2
  let registered = false
  const { page } = loadPage('miniprogram/pages/match/match.js', {
    getMyRegistration: async () => ({ registered, registration: registered ? { team_id: null, status: 'approved' } : null }),
    getMyTeam: async () => ({ id: 10, captain_id: 1 }),
    getMe: async () => ({ id: currentUserId })
  })
  page.data.match = { id: 8, status: 'registering' }
  await page.loadMyStatus()
  assert.equal(page.data.canRegisterTeam, false)
  assert.match(visibleRegistrationHtml(page.data), /class="register-disabled"/)

  currentUserId = 1
  registered = true
  await page.loadMyStatus()
  page.data.match.status = 'finished'
  assert.equal(visibleRegistrationHtml(page.data), '', 'closed events hide the obsolete registration card')
  assert.equal(page.data.registered, true)
  assert.deepEqual(page.data.myReg, { team_id: null, status: 'approved' }, 'hiding the card preserves the registration data')
})

test('closed events retain pending and rejected registration feedback without reopening registration actions', () => {
  for (const matchStatus of ['in_progress', 'finished']) {
    for (const registrationStatus of ['pending', 'rejected']) {
      const { page } = loadPage('miniprogram/pages/match/match.js', {})
      page.setData({ match: { id: 8, status: matchStatus }, registered: true,
        myReg: { team_id: 10, status: registrationStatus }, canRegisterTeam: false })
      const html = visibleRegistrationHtml(page.data)
      assert.match(html, registrationStatus === 'pending' ? /报名待审核/ : /报名未通过/)
      assert.doesNotMatch(html, /class="btn-primary"/)
      assert.equal(page.data.myReg.status, registrationStatus)
    }
  }
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

test('rank upload shows local detector failure while keeping manual review available', async () => {
  const { page, calls } = loadPage('miniprogram/pages/verify/verify.js', {
    uploadRank: async () => ({ rank: null, need_review: true,
      rank_detection: { state: 'error' }, reason: '权重 SHA256 不匹配，已拒绝加载' }),
    getMe: async () => ({ id: 1, rank: null })
  })
  await page.uploadRankWithPath('local-test-image.jpg')
  assert.match(calls.modals[0].content, /SHA256 不匹配/)
  assert.match(calls.modals[0].content, /等待管理员审核/)
})

for (const result of [
  { rank: null, reason: '截图中没有历史段位，请上传 PC 页面截图。' },
  { rank: 'A+', reason: '两路识别结果不同，需要管理员复核。' }
]) {
  test('rank review displays the server reason without detector metadata, candidate: ' + result.rank, async () => {
    const { page, calls } = loadPage('miniprogram/pages/verify/verify.js', {
      uploadRank: async () => ({ ...result, auto_applied: false, need_review: true }),
      getMe: async () => ({ id: 1, rank: null })
    })
    await page.uploadRankWithPath('local-test-image.jpg')
    assert.ok(calls.modals[0].content.includes(result.reason))
    assert.doesNotMatch(calls.modals[0].content, /。。/)
    assert.match(calls.modals[0].content, /消息页查看审核结果/)
    if (result.rank) assert.match(calls.modals[0].content, /AI 识别为 A\+/)
    assert.equal(page.data.uploading, false)
  })
}

test('rank review prefers public feedback over a technical legacy reason', async () => {
  const { page, calls } = loadPage('miniprogram/pages/verify/verify.js', {
    uploadRank: async () => ({ rank: null, need_review: true, reason: 'rank_label_invalid',
      rank_feedback: { message: '  手机界面暂不支持自动认证，已转人工审核。  ' } }),
    getMe: async () => ({ id: 1, rank: null })
  })
  await page.uploadRankWithPath('local-test-image.jpg')
  assert.match(calls.modals[0].content, /手机界面暂不支持自动认证/)
  assert.doesNotMatch(calls.modals[0].content, /rank_label_invalid|。。|  手机/)
})

test('blank or malformed public rank feedback falls back to the existing reason', async () => {
  for (const message of ['  ', null, 42]) {
    const { page, calls } = loadPage('miniprogram/pages/verify/verify.js', {
      uploadRank: async () => ({ need_review: true, reason: '本次识别未能确认历史段位。',
        rank_feedback: { message } }),
      getMe: async () => ({ id: 1, rank: null })
    })
    await page.uploadRankWithPath('local-test-image.jpg')
    assert.match(calls.modals[0].content, /本次识别未能确认历史段位。/)
    assert.doesNotMatch(calls.modals[0].content, /undefined|null|。。/)
  }
})

test('an automatically applied rank shows success and refreshes without a review modal', async () => {
  const { page, calls } = loadPage('miniprogram/pages/verify/verify.js', {
    uploadRank: async () => ({ rank: 'S0', auto_applied: true, need_review: false }),
    getMe: async () => ({ id: 1, rank: 'S0' })
  })
  await page.uploadRankWithPath('local-test-image.jpg')
  assert.equal(calls.toasts[0].title, '识别成功：S0')
  assert.equal(calls.modals.length, 0)
  assert.equal(page.data.user.rank, 'S0')
  assert.equal(page.data.uploading, false)
})

test('school verification retains its existing upload feedback and does not use rank feedback', async () => {
  const uploaded = []
  const { page, calls } = loadPage('miniprogram/pages/verify/verify.js', {
    uploadVerify: async image => {
      uploaded.push(image)
      return { rank_feedback: { message: '段位字段不应影响在校认证' } }
    },
    getMe: async () => ({ id: 1, rank: null, is_verified: false })
  })
  page.uploadWithPath('compressed-school-image.jpg')
  await new Promise(resolve => setImmediate(resolve))
  assert.deepEqual(uploaded, ['compressed-school-image.jpg'])
  assert.equal(calls.toasts[0].title, '上传成功，等待审核')
  assert.equal(calls.modals.length, 0)
  assert.equal(page.data.user.is_verified, false)
  assert.equal(page.data.uploading, false)
})

test('rank upload instructions distinguish PC history screenshots from manual mobile review', () => {
  const { page } = loadPage('miniprogram/pages/verify/verify.js', {})
  page.setData({ token: 'test-token', user: { rank: null, is_verified: false } })
  const tree = parseWxml(fs.readFileSync(path.join(root, 'miniprogram/pages/verify/verify.wxml'), 'utf8'))
  tree.scope = page.data
  const html = createRenderer({})(tree)
  assert.match(html, /PC 页面 → 天梯历史最高段位/)
  assert.match(html, /PC 页面 → 生涯数据 → 最佳赛季/)
  assert.match(html, /手机界面截图暂不支持自动认证，将转人工审核/)
  assert.doesNotMatch(html, /当前赛季段位/)
  assert.match(html, /上传学信网、教务系统或校园卡截图，需清晰显示姓名、学号和学校名称/)
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
