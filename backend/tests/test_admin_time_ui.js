// Offline tests of the real admin pages; wx and all API methods are mocked.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const test = require('node:test')
const { parseWxml, createRenderer } = require('../../tools/preview-miniprogram.js')
const root = path.resolve(__dirname, '../..')
const plain = value => JSON.parse(JSON.stringify(value))
const event = dataset => ({ currentTarget: { dataset } })

function loadPage(name, methods = {}) {
  let page
  const calls = { toasts: [], modals: [] }
  const file = path.join(root, 'miniprogram/pages/admin', name + '.js')
  vm.runInNewContext(fs.readFileSync(file, 'utf8'), {
    Page: definition => { page = definition },
    require(modulePath) {
      if (modulePath.endsWith('/admin-todos.js')) return { invalidate() {} }
      if (modulePath.endsWith('/api.js')) return new Proxy(methods, { get(target, key) {
        if (key in target) return target[key]
        throw new Error('Unexpected API call: ' + String(key))
      } })
      if (modulePath.endsWith('/rank.js')) return require(path.join(root, 'miniprogram/utils/rank.js'))
      if (modulePath.endsWith('/tournament.js')) return require(path.join(root, 'miniprogram/utils/tournament.js'))
      throw new Error('Unexpected module: ' + modulePath)
    },
    wx: { showToast: args => calls.toasts.push(args), showModal: args => calls.modals.push(args) },
    console
  }, { filename: file })
  page.data = plain(page.data)
  page.setData = values => {
    Object.entries(values).forEach(([key, value]) => {
      const parts = key.split('.')
      let target = page.data
      parts.slice(0, -1).forEach(part => { target = target[part] })
      target[parts[parts.length - 1]] = value
    })
  }
  return { page, calls }
}

function setDates(page) {
  Object.assign(page.data, {
    registrationStartDate: '2026-10-01', registrationStartTime: '09:30',
    registrationEndDate: '2026-10-08', registrationEndTime: '23:00'
  })
}

test('creation submits selected Beijing registration dates and resets them on success', async () => {
  const sent = []
  const { page, calls } = loadPage('admin', {
    createMatch: async data => sent.push(plain(data)), getMatches: async () => []
  })
  page.data.newMatch.name = '秋季赛'
  setDates(page)
  await page.createMatch()
  assert.equal(sent[0].register_start, '2026-10-01T09:30:00')
  assert.equal(sent[0].register_end, '2026-10-08T23:00:00')
  assert.equal(sent[0].name, '秋季赛')
  assert.equal(page.data.registrationStartDate, '')
  assert.equal(page.data.creatingMatch, false)
  assert.equal(calls.modals.length, 0)
})

test('creation supports open bounds, rejects partial dates and blocks duplicate submissions', async () => {
  let resolveRequest
  const sent = []
  const { page, calls } = loadPage('admin', {
    createMatch: data => { sent.push(plain(data)); return new Promise(resolve => { resolveRequest = resolve }) },
    getMatches: async () => []
  })
  page.data.newMatch.name = '秋季赛'
  page.data.registrationStartDate = '2026-10-01'
  await page.createMatch()
  assert.equal(sent.length, 0)
  assert.match(calls.toasts[0].title, /补全日期和时间/)
  page.clearRegistrationLimit(event({ side: 'start' }))
  const saving = page.createMatch()
  await page.createMatch()
  assert.equal(sent.length, 1)
  assert.equal(sent[0].register_start, null)
  assert.equal(sent[0].register_end, null)
  resolveRequest()
  await saving
})

test('registration editor restores saved values, saves both bounds, and keeps match status', async () => {
  const sent = []
  const { page } = loadPage('mdetail/mdetail', {
    updateRegistrationWindow: async (id, data) => { sent.push([id, plain(data)]); return { id, status: 'draft', registered_count: 0, ...data } }
  })
  Object.assign(page.data, { matchId: 7, match: {
    id: 7, status: 'draft', name: '测试赛事', registered_count: 5,
    register_start: '2026-10-01T09:30:00', register_end: '2026-10-08T23:00:00'
  } })
  page.openRegistrationPanel()
  assert.equal(page.data.registrationStartDate, '2026-10-01')
  assert.equal(page.data.registrationStartTime, '09:30')
  assert.equal(page.data.registrationEndTime, '23:00')
  page.clearRegistrationLimit(event({ side: 'start' }))
  await page.saveRegistrationWindow()
  assert.deepEqual(sent, [[7, { register_start: null, register_end: '2026-10-08T23:00:00' }]])
  assert.equal(page.data.match.status, 'draft')
  assert.equal(page.data.match.name, '测试赛事')
  assert.equal(page.data.match.registered_count, 5)
  assert.equal(page.data.showRegistrationPanel, false)
  assert.match(page.data.registrationSummary, /开始：不限制/)
  page.openRegistrationPanel()
  page.clearRegistrationLimit(event({ side: 'all' }))
  await page.saveRegistrationWindow()
  assert.deepEqual(sent[1], [7, { register_start: null, register_end: null }])
})

test('registration editor validates date pairs and ordering, preserves input on failure, and prevents duplicate saves', async () => {
  let rejectRequest
  let requests = 0
  const { page, calls } = loadPage('mdetail/mdetail', {
    updateRegistrationWindow: () => { requests++; return new Promise((resolve, reject) => { rejectRequest = reject }) }
  })
  Object.assign(page.data, { matchId: 7, match: { id: 7, status: 'registering' } })
  page.openRegistrationPanel()
  setDates(page)
  page.data.registrationEndTime = ''
  await page.saveRegistrationWindow()
  assert.equal(requests, 0)
  assert.match(calls.toasts.pop().title, /补全/)
  setDates(page)
  page.data.registrationEndDate = '2026-09-30'
  await page.saveRegistrationWindow()
  assert.equal(requests, 0)
  assert.match(calls.toasts.pop().title, /早于/)
  setDates(page)
  const saving = page.saveRegistrationWindow()
  await page.saveRegistrationWindow()
  page.closeRegistrationPanel()
  assert.equal(requests, 1)
  assert.equal(page.data.showRegistrationPanel, true)
  rejectRequest({ detail: '请重试' })
  await saving
  assert.equal(page.data.registrationSaving, false)
  assert.equal(page.data.showRegistrationPanel, true)
  assert.equal(page.data.registrationStartTime, '09:30')
  assert.equal(calls.modals[0].content, '请重试')
})

test('window groups come from the actual three or four groups even before rounds exist', async () => {
  const { page } = loadPage('mdetail/mdetail', { getStageWindows: async () => [] })
  for (const groups of [['A', 'B', 'C'], ['A', 'B', 'C', 'D']]) {
    page.data.teams = groups.map((group_name, index) => ({ team_id: index + 1, stage: 'challenger', group_name }))
    page.data.allRounds = []
    await page.openWindowPanel()
    assert.deepEqual(plain(page.data.stageWindows.map(w => w.group_name)), groups)
    assert.equal(page.data.windowSections.length, 1)
    assert.equal(page.data.windowSections[0].title, '挑战者组')
    assert.equal(page.data.stageWindows[0].label, '挑战者组 · A 组')
    assert.equal(page.data.stageWindows.every(w => !w.saved_only), true)
  }
})

test('window sections preserve custom, historical and independently saved groups without inventing new ones', async () => {
  const { page } = loadPage('mdetail/mdetail', { getStageWindows: async () => [
    { group_name: '旧组', window_start: '2026-10-02T18:00:00', window_end: '2026-10-03T23:00:00' },
    { group_name: '上区', window_start: '2026-10-04T18:00:00', window_end: '2026-10-05T23:00:00' }
  ] })
  page.data.teams = [{ group_name: '松林组' }, { group_name: '上区' }]
  page.data.allRounds = [{ group_name: '历史组' }, { group_name: '附加赛' }, { group_name: '下区' }, { group_name: '淘汰赛' }]
  await page.openWindowPanel()
  assert.equal(page.data.stageWindows.length, 7)
  assert.deepEqual(plain(page.data.windowSections.map(s => s.title)), ['挑战者组', '传奇组', '淘汰赛'])
  const old = page.data.stageWindows.find(w => w.group_name === '旧组')
  assert.equal(old.saved_only, true)
  assert.match(old.source_hint, /当前无队伍或对阵/)
  assert.equal(page.data.stageWindows.find(w => w.group_name === '上区').saved_only, false)
  assert.equal(page.data.stageWindows.find(w => w.group_name === '附加赛').label, '挑战者组 · 附加赛')
  page.openWindowEdit(event({ group: '旧组' }))
  assert.equal(page.data.windowEditGroup, '旧组')
  assert.equal(page.data.windowEditLabel, '挑战者组 · 旧组')
  assert.equal(page.data.windowStartDate, '2026-10-02')
  assert.equal(page.data.windowStartTime, '18:00')
})

test('an ungrouped event presents a window empty state instead of fixed A-F placeholders', async () => {
  const { page } = loadPage('mdetail/mdetail', { getStageWindows: async () => [] })
  page.data.teams = [{ team_id: 1, group_name: null }]
  await page.openWindowPanel()
  assert.equal(page.data.showWindowPanel, true)
  assert.equal(page.data.stageWindows.length, 0)
  assert.equal(page.data.windowSections.length, 0)
})

test('admin schedule renders custom challenger groups while separating the later stages', async () => {
  const groups = ['松林组', 'G', 'constructor', '__proto__', '附加赛', '上区', '下区', '淘汰赛']
  const { page, calls } = loadPage('mdetail/mdetail', { adminMatchDetail: async () => ({
    match: { id: 7, status: 'in_progress' },
    teams: [{ team_id: 1, stage: 'eliminated', group_name: '松林组', seed: null }],
    rounds: groups.map((group_name, id) => ({ id: id + 1, group_name, round_number: id + 1, status: 'pending' }))
  }) })
  await page.load()
  assert.deepEqual(plain(page.data.challengerRounds.map(r => r.group_name)), ['松林组', 'G', 'constructor', '__proto__', '附加赛'])
  assert.deepEqual(Object.keys(page.data.challengerSwiper[0].groups), ['松林组', 'G', 'constructor', '__proto__'])
  assert.equal(page.data.challengerSwiper.length, 1, 'four ordinary groups must not render an extra stage')
  assert.equal(page.data.allRounds.some(r => r.group_name === '附加赛'), true, 'historical rounds remain available')
  assert.equal(page.data.teamsSwiper[0].teams[0].team_id, 1)
  assert.equal(calls.modals.length, 0)
})

test('registration API uses the dedicated PUT endpoint with explicit null bounds', async () => {
  const requests = []
  const module = { exports: {} }
  vm.runInNewContext(fs.readFileSync(path.join(root, 'miniprogram/utils/api.js'), 'utf8'), {
    module,
    wx: { getStorageSync: () => 'test-token', request: args => {
      requests.push(args)
      args.success({ statusCode: 200, data: { id: 7 } })
    } }
  })
  await module.exports.updateRegistrationWindow(7, { register_start: null, register_end: null })
  assert.match(requests[0].url, /\/api\/matches\/7\/registration-window$/)
  assert.equal(requests[0].method, 'PUT')
  assert.deepEqual(requests[0].data, { register_start: null, register_end: null })
})

function renderAdmin(page) {
  const tree = parseWxml(fs.readFileSync(path.join(root, 'miniprogram/pages/admin/mdetail/mdetail.wxml'), 'utf8'))
  tree.scope = page.data
  return createRenderer({})(tree)
}

function tournament(groups, withRounds) {
  return {
    match: { id: 7, name: '测试赛事', status: 'in_progress' },
    teams: groups.map((group_name, index) => ({ team_id: index + 1, team_name: '队伍' + index, group_name, stage: 'challenger' })),
    rounds: withRounds ? groups.map((group_name, index) => ({ id: index + 1, group_name, round_number: index + 1, team1_id: index + 1, team2_id: index + 10, status: 'pending' })) : []
  }
}

test('actual admin template exposes the extra stage and operation for three groups, but hides them for four and ungrouped events', async () => {
  for (const withRounds of [false, true]) {
    for (const groups of [[], ['A', 'B', 'C'], ['A', 'B', 'C', 'D']]) {
      const { page } = loadPage('mdetail/mdetail', { adminMatchDetail: async () => tournament(groups, withRounds) })
      page.data.tab = 'challenger'
      page.data.challengerStage = 1
      await page.load()
      assert.equal(page.data.groupCount, groups.length)
      assert.equal(page.data.canFinishPlayoff, groups.length === 3)
      assert.equal(page.data.hasPlayoffStage, groups.length === 3)
      assert.equal(page.data.challengerSwiper.length, groups.length === 3 ? 2 : 1)
      const html = renderAdmin(page)
      if (groups.length === 3) {
        assert.match(html, /⑤结束附加赛/)
        assert.match(html, /class="stage-tab active">附加赛/)
      } else {
        assert.doesNotMatch(html, /⑤结束附加赛/)
        assert.doesNotMatch(html, /class="stage-tab[^"]*">附加赛/)
        assert.equal(page.data.challengerStage, 0)
        page.switchChallengerStage(event({ idx: 1 }))
        page.onChallengerSwiper({ detail: { current: 1 } })
        assert.equal(page.data.challengerStage, 0, 'stale slide events cannot select a removed stage')
      }
    }
  }
})

test('four-group historical extra-stage rounds remain in team history, while all current extra-stage actions stay hidden', async () => {
  const detail = tournament(['A', 'B', 'C', 'D'], true)
  detail.rounds.push({ id: 9, group_name: '附加赛', team1_id: 1, team2_id: 2, status: 'finished' })
  let advances = 0
  const { page, calls } = loadPage('mdetail/mdetail', {
    adminMatchDetail: async () => detail,
    finishPlayoffStage: async () => { advances++ },
    getStageWindows: async () => [{ group_name: '附加赛', window_start: '2026-10-01T09:00:00', window_end: '2026-10-02T20:00:00' }]
  })
  page.data.tab = 'challenger'
  await page.load()
  assert.equal(page.data.challengerSwiper.length, 1)
  assert.doesNotMatch(renderAdmin(page), /⑤结束附加赛|class="stage-tab[^"]*">附加赛/)
  await page.finishPlayoff()
  assert.equal(advances, 0)
  page.showTeam(event({ id: 1 }))
  assert.equal(page.data.historyRounds.some(round => round.id === 9), true)
  page.openScore(event({ id: 9 }))
  assert.equal(page.data.showScoreInput, false)
  assert.match(calls.toasts.pop().title, /仅可查看/)
  await page.openWindowPanel()
  const historical = page.data.stageWindows.find(w => w.group_name === '附加赛')
  assert.equal(historical.history_only, true)
  assert.equal(historical.stage, 'history')
  assert.match(historical.label, /不适用当前 4 组/)
  page.openWindowEdit(event({ group: '附加赛' }))
  assert.equal(page.data.windowEditGroup, null)
})

test('saved old windows do not change the current three-group format or create an ungrouped playoff stage', async () => {
  for (const groups of [[], ['A', 'B', 'C']]) {
    const { page } = loadPage('mdetail/mdetail', {
      adminMatchDetail: async () => tournament(groups, false),
      getStageWindows: async () => [{ group_name: 'D', window_start: null, window_end: null }]
    })
    await page.load()
    await page.openWindowPanel()
    await page.load()
    assert.equal(page.data.groupCount, groups.length)
    assert.equal(page.data.hasPlayoffStage, groups.length === 3)
    assert.equal(page.data.stageWindows.find(w => w.group_name === 'D').saved_only, true)
    if (groups.length === 3) assert.match(page.data.windowSections[0].hint, /3 组赛制/)
  }
})

test('group-count prompt accepts only exact 3 or 4 and submits the corresponding groups', async () => {
  for (const value of ['2', '5', '3abc', '3.5', '3', '4']) {
    const submitted = []
    const { page, calls } = loadPage('mdetail/mdetail', {
      seedAndGroup: async (id, groups) => submitted.push(['group', id, plain(groups)])
    })
    page.data.matchId = 7
    page.load = async () => {}
    page.promptGroup()
    assert.match(calls.modals[0].title, /3 组含附加赛.*4 组直晋/)
    await calls.modals[0].success({ confirm: true, content: value })
    if (value === '3' || value === '4') {
      assert.deepEqual(submitted, [['group', 7, value === '3' ? ['A', 'B', 'C'] : ['A', 'B', 'C', 'D']]])
    } else assert.equal(submitted.length, 0)
  }
})

test('finishing groups follows backend format result and only the three-group flow permits extra-stage advancement', async () => {
  for (const groupCount of [3, 4]) {
    let advances = 0
    const { page, calls } = loadPage('mdetail/mdetail', {
      finishGroupStage: async () => ({ data: {
        group_count: groupCount, has_playoff: groupCount === 3,
        group_winners: Array.from({ length: groupCount }, (_, index) => index + 1), added_rounds: groupCount === 3 ? 3 : 0
      } }),
      finishPlayoffStage: async () => { advances++ }
    })
    Object.assign(page.data, { groupCount, canFinishPlayoff: groupCount === 3, matchId: 7 })
    page.load = async () => {}
    await page.finishGroup()
    if (groupCount === 3) assert.match(calls.modals[0].content, /附加赛对阵已生成 3 场/)
    else {
      assert.match(calls.modals[0].content, /4 个小组冠军已晋级传奇组/)
      assert.doesNotMatch(calls.modals[0].content, /请录入附加赛/)
    }
    await page.finishPlayoff()
    assert.equal(advances, groupCount === 3 ? 1 : 0)
  }
})
