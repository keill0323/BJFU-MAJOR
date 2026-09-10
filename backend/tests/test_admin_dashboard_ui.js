// Real management dashboard behavior with offline API and shared-summary mocks.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const test = require('node:test')
const { parseWxml, createRenderer } = require('../../tools/preview-miniprogram.js')
const root = path.resolve(__dirname, '../..')
const plain = value => JSON.parse(JSON.stringify(value))
const event = dataset => ({ currentTarget: { dataset } })
const summary = overrides => Object.assign({ verification_count: 2, rank_application_count: 3, team_count: 1, registration_count: 4, total: 10, registration_matches: [{ match_id: 7, match_name: '秋季赛事', count: 4, roster_locked: false }] }, overrides)
const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b }); return { promise, resolve, reject } }

function loadPage(methods = {}, todoMethods = {}) {
  let page
  const calls = { api: [], todos: [], navigations: [], modals: [], toasts: [], invalidations: 0 }
  const defaults = { getMe: async () => ({ id: 1, role: 'admin' }) }
  const api = new Proxy(Object.assign(defaults, methods), { get(target, key) {
    if (key === 'BASE') return 'https://api.example.test'
    if (!(key in target)) throw new Error('Unexpected API call: ' + key)
    return (...args) => { calls.api.push([key, ...args]); return target[key](...args) }
  } })
  const todos = {
    refresh: force => { calls.todos.push(force); return (todoMethods.refresh || (async () => summary()))(force) },
    invalidate: () => { calls.invalidations++; if (todoMethods.invalidate) todoMethods.invalidate() }
  }
  const file = path.join(root, 'miniprogram/pages/admin/admin.js')
  vm.runInNewContext(fs.readFileSync(file, 'utf8'), {
    Page: value => { page = value },
    require(name) {
      if (name.endsWith('/api.js')) return api
      if (name.endsWith('/admin-todos.js')) return todos
      if (name.endsWith('/rank.js')) return require(path.join(root, 'miniprogram/utils/rank.js'))
      throw new Error('Unexpected module: ' + name)
    },
    wx: {
      showToast: args => calls.toasts.push(args), showModal: args => calls.modals.push(args),
      showActionSheet: args => calls.modals.push(args), navigateTo: args => calls.navigations.push(args),
      navigateBack: args => calls.navigations.push(args), redirectTo: args => calls.navigations.push(args),
      stopPullDownRefresh: () => {}
    }, console
  }, { filename: file })
  page.data = plain(page.data)
  page.setData = values => Object.entries(values).forEach(([key, value]) => {
    const parts = key.split('.')
    let dest = page.data
    parts.slice(0, -1).forEach(part => { dest = dest[part] })
    dest[parts[parts.length - 1]] = value
  })
  return { page, calls }
}

function render(page) {
  const tree = parseWxml(fs.readFileSync(path.join(root, 'miniprogram/pages/admin/admin.wxml'), 'utf8'))
  tree.scope = page.data
  return createRenderer({})(tree)
}

test('management opens actionable overview without downloading user, team or screenshot lists', async () => {
  const { page, calls } = loadPage()
  const loading = page.onLoad({})
  page.onShow()
  await loading
  assert.equal(page.data.tab, 'overview')
  assert.deepEqual(calls.api.map(item => item[0]), ['getMe'])
  assert.deepEqual(calls.todos, [true])
  assert.equal(page.data.todos.total, 10)
  assert.equal(page.data.showCreateMatch, false)
  const html = render(page)
  assert.match(html, /待办事项/)
  assert.match(html, /待审核赛事报名/)
  assert.match(html, /秋季赛事/)
  assert.doesNotMatch(html, /当前没有待办|暂无用户/)
})

test('deep links fetch only the selected queue and unknown links safely show the overview', async () => {
  for (const [tab, method] of [['verify', 'getVerifyList'], ['rankapps', 'getRankApplications'], ['teams', 'getAdminTeams'], ['matches', 'getMatches']]) {
    const { page, calls } = loadPage({ [method]: async () => [] })
    await page.onLoad({ tab })
    assert.equal(page.data.tab, tab)
    assert.deepEqual(calls.api.map(item => item[0]), ['getMe', method])
    assert.equal(page.data.listReady[tab], true)
  }
  const { page } = loadPage()
  await page.onLoad({ tab: 'invalid-tab' })
  assert.equal(page.data.tab, 'overview')
})

test('summary failure is distinct from zero and preserves previous numbers as stale', async () => {
  let failed = true
  const { page } = loadPage({}, { refresh: async () => { if (failed) throw new Error('offline'); return summary() } })
  await page.onLoad()
  assert.equal(page.data.todos, null)
  assert.match(render(page), /暂时无法确认待办数量/)
  assert.doesNotMatch(render(page), /当前没有待办/)
  failed = false
  await page.retryTodos()
  assert.equal(page.data.todos.total, 10)
  failed = true
  await page.retryTodos()
  assert.equal(page.data.todos.total, 10)
  assert.equal(page.data.todoError, true)
  assert.match(render(page), /保留上次记录/)
  assert.doesNotMatch(render(page), /当前没有待办/)
})

test('confirmed empty overview renders success only after counts finish loading', async () => {
  const pending = deferred()
  const { page } = loadPage({}, { refresh: () => pending.promise })
  const loading = page.onLoad()
  await Promise.resolve()
  assert.doesNotMatch(render(page), /当前没有待办/)
  pending.resolve(summary({ total: 0, verification_count: 0, rank_application_count: 0, team_count: 0, registration_count: 0, registration_matches: [] }))
  await loading
  assert.match(render(page), /当前没有待办/)
})

test('queue loading errors do not masquerade as empty and retry restores the list', async () => {
  let fail = true
  const { page } = loadPage({ getVerifyList: async () => { if (fail) throw new Error('offline'); return [] } })
  await page.onLoad({ tab: 'verify' })
  assert.match(render(page), /列表加载失败/)
  assert.doesNotMatch(render(page), /暂无待认证用户/)
  fail = false
  await page.retryList()
  assert.match(render(page), /暂无待认证用户/)
  assert.doesNotMatch(render(page), /列表加载失败/)
})

test('returning to the dashboard refreshes counts and only the selected tab', async () => {
  const { page, calls } = loadPage({ getRankApplications: async () => [] })
  await page.onLoad()
  page.onShow()
  await page.switchTab(event({ tab: 'rankapps' }))
  await page.switchTab(event({ tab: 'rankapps' }))
  assert.equal(calls.api.filter(item => item[0] === 'getRankApplications').length, 1)
  await page.onShow()
  assert.equal(calls.todos.length, 2)
  assert.equal(calls.api.filter(item => item[0] === 'getRankApplications').length, 2)
})

test('review invalidates shared counts and updates only the current queue', async () => {
  let approved = false
  const { page, calls } = loadPage({
    getVerifyList: async () => approved ? [] : [{ id: 8, nickname: '选手', verify_image: '/uploads/school.png' }],
    adminUpdateUser: async () => { approved = true }
  }, { refresh: async () => summary({ verification_count: approved ? 0 : 1 }) })
  await page.onLoad({ tab: 'verify' })
  await page.verifyPass(event({ id: 8 }))
  assert.equal(calls.invalidations, 1)
  assert.equal(page.data.todos.verification_count, 0)
  assert.equal(page.data.verifyUsers.length, 0)
  assert.deepEqual(calls.api.map(item => item[0]), ['getMe', 'getVerifyList', 'adminUpdateUser', 'getVerifyList'])
})

test('administrator team queue uses the pending-inclusive endpoint and prioritizes pending teams', async () => {
  const { page, calls } = loadPage({ getAdminTeams: async () => [{ id: 1, name: '通过队', status: 'approved' }, { id: 2, name: '待审队', status: 'pending' }] })
  await page.onLoad({ tab: 'teams' })
  assert.equal(page.data.teams[0].id, 2)
  assert.equal(page.data.pendingTeamCount, 1)
  assert.deepEqual(calls.api.map(item => item[0]), ['getMe', 'getAdminTeams'])
  assert.match(render(page), /待审队/)
})

test('reviewer cannot change roles or open champion supplement through hidden controls or handlers', async () => {
  const { page, calls } = loadPage({ getMe: async () => ({ id: 1, role: 'reviewer' }), adminListUsers: async () => [{ id: 8 }] })
  await page.onLoad({ tab: 'users' })
  assert.doesNotMatch(render(page), /修改权限/)
  page.setRole(event({ id: 8 }))
  page.goChampion(event({ id: 7 }))
  page.openHonors()
  assert.equal(calls.modals.length, 0)
  assert.equal(calls.navigations.length, 0)
})

test('finished event champion action and registration queue navigate to their exact workflows', async () => {
  const { page, calls } = loadPage({ getMatches: async () => [{ id: 7, name: '已结束赛事', status: 'finished' }, { id: 8, name: '报名赛事', status: 'registering' }] })
  await page.onLoad({ tab: 'matches' })
  let html = render(page)
  assert.match(html, /补录冠军/)
  assert.doesNotMatch(html, /placeholder="赛事名称"/)
  page.goChampion(event({ id: 8 }))
  page.goChampion(event({ id: 7 }))
  page.goMDetail(event({ id: 8 }))
  assert.deepEqual(calls.navigations.map(item => item.url), ['/pages/admin/champion/champion?matchId=7', '/pages/admin/mdetail/mdetail?matchId=8'])
  page.toggleCreateMatch()
  html = render(page)
  assert.ok(html.indexOf('已结束赛事') < html.indexOf('新建赛事</'))
  assert.match(html, /每队人数|队伍上限/)
})

test('parallel reads coalesce while newer searches win and unloaded pages ignore results', async () => {
  const first = deferred(), second = deferred(), count = deferred()
  const { page, calls } = loadPage({ adminListUsers: value => value === 'old' ? first.promise : second.promise }, { refresh: () => count.promise })
  page.data.user = { role: 'admin' }
  const a = page.loadTodos(), b = page.loadTodos()
  assert.equal(a, b)
  const old = page.loadUsers('old'), same = page.loadUsers('old'), newer = page.loadUsers('new')
  assert.equal(old, same)
  await Promise.resolve()
  second.resolve([{ id: 2 }])
  await newer
  first.resolve([{ id: 1 }])
  await old
  assert.equal(page.data.users[0].id, 2)
  page.onUnload()
  count.resolve(summary())
  await a
  assert.equal(page.data.todos, null)
  assert.equal(calls.todos.length, 1)
})

test('late summary invalidated by a completed review cannot restore the old badge', async () => {
  const old = deferred()
  let first = true
  const { page } = loadPage({}, { refresh: () => { if (first) { first = false; return old.promise } return Promise.resolve(summary({ total: 0 })) } })
  page.data.user = { role: 'admin' }
  const loading = page.loadTodos()
  await Promise.resolve()
  await page.refreshAfterReview()
  old.resolve(summary({ total: 9 }))
  await loading
  assert.equal(page.data.todos.total, 0)
})

test('an invalidated or logged-out summary does not clear a newer successful value', async () => {
  const { page } = loadPage({}, { refresh: async () => null })
  page.data.todos = summary({ total: 2 })
  await page.loadTodos()
  assert.equal(page.data.todos.total, 2)
})

test('return refresh asks shared store again after another page invalidated an in-flight summary', async () => {
  const stale = deferred()
  let requests = 0
  const { page } = loadPage({}, { refresh: () => ++requests === 1 ? stale.promise : Promise.resolve(summary({ total: 3 })) })
  const old = page.loadTodos(true)
  await Promise.resolve()
  await page.loadTodos(true)
  stale.resolve(null)
  await old
  assert.equal(page.data.todos.total, 3)
  assert.equal(page.data.todoLoading, false)
})
