const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const test = require('node:test')
const { parseWxml, createRenderer } = require('../../tools/preview-miniprogram.js')

const root = path.resolve(__dirname, '../..')
const filename = path.join(root, 'miniprogram/pages/admin/champion/champion.js')
const clone = value => JSON.parse(JSON.stringify(value))
const field = (key, value) => ({ currentTarget: { dataset: { field: key } }, detail: { value } })
const pick = value => ({ detail: { value } })
const deferred = () => {
  let resolve, reject
  const promise = new Promise((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}
const manual = fields => ({ match_id: 8, match_name: '春季校赛', snapshot_source: 'manual', champion_name: '林间回响', champion_team_id: null, event_date: '2025-05-01T00:00:00', champion_score: null, runner_up_score: null, runner_up_name: null, champion_logo: null, roster: [], ...fields })

function loadPage(methods = {}, options = {}) {
  let page
  const calls = { api: [], modals: [], saves: [], navigations: [] }
  const api = {
    getMe: async () => ({ role: 'admin' }),
    getMatch: async id => ({ id, name: '春季校赛', status: 'finished' }),
    getAdminChampion: async () => ({ champion: null, note: null, version: 0 }),
    getAdminTeams: async () => [{ id: 20, name: '现在的战队' }],
    getTeam: async id => ({ id, name: '现在的战队', logo: '/uploads/team.svg', members: [{ user_id: 2, nickname: '当前队员', rank: 'A+', avatar: '/uploads/avatar.png' }] }),
    saveAdminChampion: async (id, payload) => ({ champion: manual({ ...payload, match_id: id }), note: payload.note, version: payload.expected_version + 1 }),
    ...methods
  }
  Object.keys(api).forEach(key => {
    const method = api[key]
    api[key] = (...args) => {
      calls.api.push({ method: key, args: clone(args) })
      if (key === 'saveAdminChampion') calls.saves.push(clone(args))
      return method(...args)
    }
  })
  vm.runInNewContext(fs.readFileSync(filename, 'utf8'), {
    Page: value => { page = value }, require: () => api,
    getCurrentPages: () => [{ route: 'pages/admin/admin' }, { route: 'pages/admin/champion/champion' }],
    wx: {
      showModal(value) { calls.modals.push(value); if (options.confirm !== undefined) value.success({ confirm: options.confirm }) },
      navigateTo: value => calls.navigations.push(value), navigateBack: value => calls.navigations.push(value), redirectTo: value => calls.navigations.push(value)
    }, console
  }, { filename })
  page.data = clone(page.data)
  page.setData = values => Object.assign(page.data, clone(values))
  return { page, calls }
}
function fill(page, values = {}) {
  Object.entries({ champion_name: '往届冠军', event_date: '2025-05-01', note: '依据赛事组委会的公开公告补录。', ...values }).forEach(([key, value]) => page.onField(field(key, value)))
}
function render(page, pageFile = filename) {
  const tree = parseWxml(fs.readFileSync(pageFile.replace(/\.js$/, '.wxml'), 'utf8'))
  tree.scope = page.data
  return createRenderer({})(tree)
}

test('editor verifies admin before reading archives and never publishes on load', async () => {
  const { page, calls } = loadPage()
  await page.onLoad({ matchId: '8' })
  assert.equal(calls.api[0].method, 'getMe')
  assert.deepEqual(calls.api.map(call => call.method), ['getMe', 'getMatch', 'getAdminChampion'])
  assert.equal(page.data.canEdit, true)
  assert.equal(page.data.version, 0)
  assert.equal(calls.saves.length, 0)
  const html = render(page)
  assert.match(html, /所选赛事/)
  assert.match(html, /冠军战队/)
  assert.match(html, /未知资料留空/)
  assert.doesNotMatch(html, /正在读取/)
})

test('reviewer, player and guest cannot read or publish admin champion archives', async () => {
  for (const user of [{ role: 'reviewer' }, { role: 'member' }, null]) {
    const { page, calls } = loadPage({ getMe: async () => user })
    await page.onLoad({ matchId: '8' })
    await page.submit()
    await page.loadTeams()
    assert.equal(page.data.denied, true)
    assert.equal(page.data.canEdit, false)
    assert.deepEqual(calls.api.map(call => call.method), ['getMe'])
    assert.match(render(page), /仅管理员可补录冠军/)
  }
})

test('authentication failure and invalid event do not enable editor or load teams', async () => {
  const auth = loadPage({ getMe: async () => { throw { detail: '请先登录', statusCode: 401 } } })
  await auth.page.onLoad({ matchId: '8' })
  assert.equal(auth.page.data.loadError, '请先登录')
  assert.equal(auth.calls.api.length, 1)
  const invalid = loadPage()
  await invalid.page.onLoad({ matchId: '../8' })
  assert.equal(invalid.calls.api.length, 0)
  assert.match(invalid.page.data.loadError, /参数无效/)
  const missing = loadPage({ getMatch: async () => { throw { detail: '赛事不存在' } } })
  await missing.page.onLoad({ matchId: '8' })
  assert.equal(missing.page.data.loadError, '赛事不存在')
  assert.equal(missing.page.data.canEdit, false)
  assert.ok(missing.calls.api.every(call => call.method !== 'getAdminTeams'))
})

test('unfinished events and automatic final/backfill archives remain read-only', async () => {
  for (const source of ['final_result', 'backfill']) {
    const { page, calls } = loadPage({ getAdminChampion: async () => ({ champion: manual({ snapshot_source: source }), version: 3 }) })
    await page.onLoad({ matchId: '8' })
    assert.equal(page.data.canEdit, false)
    await page.loadTeams()
    await page.submit()
    assert.ok(calls.api.every(call => call.method !== 'getAdminTeams'))
    assert.equal(calls.saves.length, 0)
    assert.match(render(page), /修正比分/)
  }
  const { page } = loadPage({ getMatch: async () => ({ id: 8, name: '比赛中', status: 'in_progress' }) })
  await page.onLoad({ matchId: '8' })
  assert.equal(page.data.canEdit, false)
  assert.match(render(page), /赛事结束后才可补录/)
})

test('new historical champion allows dissolved teams, unknown scores and unknown roster without inventing data', async () => {
  const { page, calls } = loadPage({}, { confirm: true })
  await page.onLoad({ matchId: '8' })
  fill(page)
  await page.submit()
  assert.equal(calls.saves.length, 1)
  const [matchId, payload] = calls.saves[0]
  assert.equal(matchId, 8)
  assert.equal(payload.champion_team_id, null)
  assert.equal(payload.runner_up_name, null)
  assert.equal(payload.champion_score, null)
  assert.equal(payload.runner_up_score, null)
  assert.deepEqual(payload.roster, [])
  assert.equal(payload.expected_version, 0)
  assert.equal(payload.event_date, '2025-05-01T00:00:00')
  assert.match(calls.modals[0].content, /春季校赛/)
  assert.match(calls.modals[0].content, /往届冠军/)
  assert.match(calls.modals[0].content, /暂无记录/)
  assert.equal(page.data.published, true)
  assert.equal(page.data.version, 1)
  assert.match(render(page), /冠军档案已发布/)
})

test('manual edit uses loaded version and retains archived member metadata only while names stay unchanged', async () => {
  const record = { champion: manual({ champion_team_id: 20, roster: [{ user_id: 4, nickname: '历史选手', rank: 'S25', avatar: '/uploads/old.png' }] }), note: '旧档案来源', version: 6 }
  const { page, calls } = loadPage({ getAdminChampion: async () => record }, { confirm: true })
  await page.onLoad({ matchId: '8' })
  assert.equal(page.data.canEdit, true)
  page.onField(field('note', '核实后修订来源'))
  await page.submit()
  assert.equal(calls.saves[0][1].expected_version, 6)
  assert.deepEqual(calls.saves[0][1].roster, [{ user_id: 4, nickname: '历史选手', rank: 'S25', avatar: '/uploads/old.png' }])
  assert.equal(page.data.version, 7)
  page.onField(field('rosterText', '重新核对的名字\n另一名夺冠队员'))
  await page.submit()
  assert.deepEqual(calls.saves[1][1].roster, [{ nickname: '重新核对的名字' }, { nickname: '另一名夺冠队员' }])
  assert.equal(calls.saves[1][1].expected_version, 7)
})

test('deleted team or user links can be removed without changing any historical facts before resaving', async () => {
  const record = { champion: manual({ champion_team_id: 20, champion_logo: '/uploads/old-team.png', runner_up_name: '旧亚军', champion_score: 2, runner_up_score: 0, roster: [{ user_id: 4, nickname: '旧成员', rank: 'S25', avatar: '/uploads/old-member.png' }] }), note: '旧赛果公告', version: 3 }
  const { page, calls } = loadPage({
    getAdminChampion: async () => record,
    saveAdminChampion: async (id, payload) => {
      if (payload.champion_team_id || payload.roster.some(member => member.user_id)) throw { detail: '所选队伍或选手已不存在', statusCode: 400 }
      return { champion: manual({ ...payload, match_id: id }), note: payload.note, version: 4 }
    }
  }, { confirm: true })
  await page.onLoad({ matchId: '8' })
  page.onField(field('note', '核对旧公告后修订来源'))
  await page.submit()
  assert.equal(page.data.published, false)
  assert.match(page.data.formError, /已不存在/)
  const formBefore = clone(page.data.form)
  assert.equal(page.data.hasAccountLinks, true)
  assert.match(render(page), /仅保留历史资料/)
  page.keepHistoricalOnly()
  assert.deepEqual(page.data.form, formBefore)
  assert.equal(page.data.selectedTeamId, null)
  assert.equal(page.data.hasAccountLinks, false)
  assert.equal(page.data.historicalOnly, true)
  await page.submit()
  assert.equal(page.data.published, true)
  const payload = calls.saves[1][1]
  assert.equal(payload.champion_team_id, null)
  assert.deepEqual(payload.roster, [{ nickname: '旧成员', rank: 'S25', avatar: '/uploads/old-member.png' }])
  assert.equal(payload.champion_name, '林间回响')
  assert.equal(payload.champion_logo, '/uploads/old-team.png')
  assert.equal(payload.event_date, '2025-05-01T00:00:00')
  assert.equal(payload.champion_score, 2)
  assert.equal(payload.runner_up_score, 0)
  assert.equal(payload.runner_up_name, '旧亚军')
  assert.equal(payload.note, '核对旧公告后修订来源')
})

test('removing historical links cancels late team imports and stays disabled while saving or read-only', async () => {
  const waiting = deferred()
  const record = { champion: manual({ champion_team_id: 20, roster: [{ user_id: 4, nickname: '旧成员', rank: 'A+' }] }), note: '旧资料', version: 3 }
  const { page } = loadPage({ getAdminChampion: async () => record, getTeam: () => waiting.promise })
  await page.onLoad({ matchId: '8' })
  await page.loadTeams()
  const importing = page.onPickTeam(pick('0'))
  page.keepHistoricalOnly()
  waiting.resolve({ id: 20, name: '当前战队', members: [{ user_id: 5, nickname: '当前成员' }] })
  await importing
  assert.equal(page.data.selectedTeamId, null)
  assert.equal(page.data.teamLoading, false)
  assert.equal(page.data.form.champion_name, '林间回响')
  assert.deepEqual(clone(page.makePayload().roster), [{ nickname: '旧成员', rank: 'A+' }])
  for (const guard of [{ saving: true }, { canEdit: false }]) {
    const guarded = loadPage({ getAdminChampion: async () => record })
    await guarded.page.onLoad({ matchId: '8' })
    guarded.page.setData(guard)
    guarded.page.keepHistoricalOnly()
    assert.equal(guarded.page.data.selectedTeamId, 20)
    assert.equal(guarded.page.data.hasAccountLinks, true)
  }
})

test('invalid scores are rejected before confirmation while valid zero runner score survives', async () => {
  const { page, calls } = loadPage({}, { confirm: true })
  await page.onLoad({ matchId: '8' })
  for (const [winner, runner] of [['2', ''], ['', '0'], ['1', '1'], ['0', '2'], ['1.5', '0'], ['1000', '0'], ['-1', '0']]) {
    fill(page, { champion_score: winner, runner_up_score: runner })
    await page.submit()
    assert.ok(page.data.formError)
  }
  assert.equal(calls.modals.length, 0)
  assert.equal(calls.saves.length, 0)
  fill(page, { champion_score: '2', runner_up_score: '0' })
  await page.submit()
  assert.equal(calls.saves[0][1].champion_score, 2)
  assert.equal(calls.saves[0][1].runner_up_score, 0)
})

test('invalid date, missing source, oversize roster and unsafe image fail before publication', async () => {
  const { page, calls } = loadPage({}, { confirm: true })
  await page.onLoad({ matchId: '8' })
  for (const values of [{ event_date: '2025-02-30' }, { note: '   ' }, { champion_name: ' ' }, { champion_logo: 'javascript:alert(1)' }, { champion_logo: '//outside.example/logo.png' }, { champion_logo: 'https://user:pass@example.test/logo.png' }, { champion_logo: '/uploads/' + 'x'.repeat(250) }, { rosterText: Array(21).fill('选手').join('\n') }]) {
    page.setData({ form: { champion_name: '', champion_logo: '', event_date: '', runner_up_name: '', champion_score: '', runner_up_score: '', rosterText: '', note: '' } })
    fill(page, values)
    await page.submit()
    assert.ok(page.data.formError)
  }
  assert.equal(calls.saves.length, 0)
  assert.equal(calls.modals.length, 0)
})

test('team import is explicit and warns current roster must be checked against historical roster', async () => {
  const { page, calls } = loadPage()
  await page.onLoad({ matchId: '8' })
  await page.loadTeams()
  await page.onPickTeam(pick('0'))
  assert.equal(page.data.form.champion_name, '现在的战队')
  assert.equal(page.data.selectedTeamId, 20)
  assert.equal(page.data.rosterCount, 1)
  assert.equal(page.data.fromCurrentTeam, true)
  assert.equal(page.data.form.event_date, '')
  assert.equal(page.data.form.champion_score, '')
  assert.match(render(page), /当前资料/)
  assert.match(render(page), /夺冠当时的记录/)
  assert.equal(calls.saves.length, 0)
  page.onField(field('champion_name', '旧队名'))
  assert.equal(page.data.selectedTeamId, null)
})

test('late team import cannot overwrite manual changes or newer team selection', async () => {
  const first = deferred(), second = deferred()
  const { page } = loadPage({ getAdminTeams: async () => [{ id: 1, name: '一队' }, { id: 2, name: '二队' }], getTeam: id => id === 1 ? first.promise : second.promise })
  await page.onLoad({ matchId: '8' })
  await page.loadTeams()
  const stale = page.onPickTeam(pick('0'))
  const newer = page.onPickTeam(pick('1'))
  second.resolve({ id: 2, name: '新选择', members: [] })
  await newer
  first.resolve({ id: 1, name: '旧选择', members: [] })
  await stale
  assert.equal(page.data.form.champion_name, '新选择')
  const waiting = deferred()
  const manualCase = loadPage({ getTeam: () => waiting.promise })
  await manualCase.page.onLoad({ matchId: '8' })
  await manualCase.page.loadTeams()
  const importing = manualCase.page.onPickTeam(pick('0'))
  manualCase.page.onField(field('champion_name', '手动修正'))
  waiting.resolve({ id: 20, name: '延迟响应', members: [] })
  await importing
  assert.equal(manualCase.page.data.form.champion_name, '手动修正')
  assert.match(manualCase.page.data.teamsError, /带入已取消/)
  assert.equal(manualCase.page.data.teamLoading, false)
})

test('cancelled confirmation does not publish and repeated taps share a single confirmation and request', async () => {
  const waiting = deferred()
  const { page, calls } = loadPage({ saveAdminChampion: () => waiting.promise })
  await page.onLoad({ matchId: '8' })
  fill(page)
  const cancelled = page.submit()
  await page.submit()
  assert.equal(calls.modals.length, 1)
  calls.modals[0].success({ confirm: false })
  await cancelled
  assert.equal(calls.saves.length, 0)
  assert.equal(page.data.saving, false)
  const request = page.submit()
  calls.modals[1].success({ confirm: true })
  await Promise.resolve()
  await page.submit()
  assert.equal(calls.modals.length, 2)
  assert.equal(calls.saves.length, 1)
  waiting.resolve({ champion: manual(), note: '发布来源', version: 1 })
  await request
  assert.equal(page.data.saving, false)
  assert.equal(page.data.published, true)
})

test('server conflict or failure preserves source and form and never silently overwrites a newer record', async () => {
  const { page, calls } = loadPage({ saveAdminChampion: async () => { throw { detail: '其他管理员已更新此档案', statusCode: 409 } } }, { confirm: true })
  await page.onLoad({ matchId: '8' })
  fill(page, { rosterText: '老队员\n老队长', champion_score: '2', runner_up_score: '1' })
  const before = clone(page.data.form)
  await page.submit()
  assert.deepEqual(page.data.form, before)
  assert.equal(page.data.published, false)
  assert.equal(page.data.saving, false)
  assert.equal(page.data.version, 0)
  assert.match(page.data.formError, /其他管理员/)
  assert.equal(calls.saves.length, 1)
})

test('unloading during confirmation prevents publication and during import prevents setData', async () => {
  const { page, calls } = loadPage()
  await page.onLoad({ matchId: '8' })
  fill(page)
  const confirming = page.submit()
  page.onUnload()
  calls.modals[0].success({ confirm: true })
  await confirming
  assert.equal(calls.saves.length, 0)
  const waiting = deferred()
  const importer = loadPage({ getTeam: () => waiting.promise })
  await importer.page.onLoad({ matchId: '8' })
  await importer.page.loadTeams()
  const importing = importer.page.onPickTeam(pick('0'))
  importer.page.onUnload()
  importer.page.setData = () => { throw new Error('setData after unload') }
  waiting.resolve({ id: 20, name: '队伍', members: [] })
  await importing
})

test('manual public archive handles null team/member IDs and absent scores without invented ranks', async () => {
  const hallFile = path.join(root, 'miniprogram/pages/hall/hall.js')
  let page
  vm.runInNewContext(fs.readFileSync(hallFile, 'utf8'), {
    Page: value => { page = value },
    require: name => name.endsWith('/rank.js') ? require(path.resolve(path.dirname(hallFile), name)) : {
      BASE: 'https://example.test', getHallChampions: async () => ({ items: [manual({ roster: [{ user_id: null, nickname: '旧队长' }, { user_id: null, nickname: '旧选手' }] })], total: 1, has_more: false })
    }, wx: {}, console
  }, { filename: hallFile })
  page.data = clone(page.data)
  page.setData = values => Object.assign(page.data, clone(values))
  await page.onLoad()
  page.toggleRoster({ currentTarget: { dataset: { id: 8 } } })
  const item = page.data.champions.items[0]
  assert.equal(item.hasScore, false)
  assert.equal(item.sourceLabel, '管理员补录')
  assert.equal(new Set(item.roster.map(member => member.rosterKey)).size, 2)
  const html = render(page, hallFile)
  assert.match(html, /管理员补录/)
  assert.match(html, /比分未归档/)
  assert.match(html, /亚军未归档/)
  assert.match(html, /段位未归档/)
  assert.doesNotMatch(html, /待认证/)
  assert.match(html, /旧队长/)
})
