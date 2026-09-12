const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const { parseWxml, createRenderer } = require('../../tools/preview-miniprogram.js')
const root = path.resolve(__dirname, '../../miniprogram')
const counts = n => ({ verification_count: n, rank_application_count: 0, team_count: 0, registration_count: 0, total: n,
  registration_matches: [], blocked_registration_count: 0, blocked_registration_matches: [] })
const deferred = () => {
  let resolve, reject
  const promise = new Promise((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}

function setup(getTodos, initialToken = 'admin-token') {
  const storage = { token: initialToken }
  const api = { getAdminTodos: getTodos, getMe: async () => ({ role: 'admin', rank: 'A+' }) }
  const wx = { getStorageSync: key => storage[key], navigateTo: options => navigations.push(options) }
  const navigations = []
  const module = { exports: {} }
  vm.runInNewContext(fs.readFileSync(path.join(root, 'utils/admin-todos.js'), 'utf8'), {
    module, require: () => api, wx, Date, console
  })
  const store = module.exports
  function header(role = 'admin') {
    api.getMe = async () => ({ role, rank: 'A+' })
    let definition
    vm.runInNewContext(fs.readFileSync(path.join(root, 'components/nav-drawer/nav-drawer.js'), 'utf8'), {
      Component: value => { definition = value }, wx, console,
      require(name) {
        if (name.endsWith('/api.js')) return api
        if (name.endsWith('/admin-todos.js')) return store
        if (name.endsWith('/navigation.js')) return {}
        if (name.endsWith('/rank.js')) return require(path.join(root, 'utils/rank.js'))
        throw Error('Unexpected module ' + name)
      }
    })
    const instance = Object.assign({}, definition, definition.methods)
    instance.data = { ...JSON.parse(JSON.stringify(definition.data)), title: '北林 MAJOR', showAdminNotice: true, currentRoute: '/pages/index/index' }
    instance.setData = values => Object.assign(instance.data, values)
    instance.triggerEvent = () => {}
    return instance
  }
  return { store, storage, header, navigations }
}

function renderHeader(instance) {
  const tree = parseWxml('<nav-drawer title="北林 MAJOR" show-admin-notice="{{true}}"/>')
  tree.scope = {}
  return createRenderer(instance.data)(tree)
}

function headerAction(value, attribute = 'class') {
  const tree = parseWxml(fs.readFileSync(path.join(root, 'components/nav-drawer/nav-drawer.wxml'), 'utf8'))
  const search = node => {
    if (node.attrs && node.attrs[attribute] === value) return node
    for (const child of node.children || []) {
      const found = search(child)
      if (found) return found
    }
  }
  return search(tree)
}

test('admin surfaces share an in-flight summary and a short cache without reading full lists', async () => {
  const pending = deferred()
  let reads = 0
  const { store } = setup(() => { reads++; return pending.promise })
  const states = []
  const unsubscribe = store.subscribe(state => states.push(state))
  const first = store.refresh()
  assert.equal(store.refresh(true), first)
  await Promise.resolve()
  assert.equal(reads, 1)
  pending.resolve(counts(3))
  await first
  await store.refresh()
  assert.equal(reads, 1)
  assert.equal(store.getState().data.total, 3)
  unsubscribe()
  const count = states.length
  store.invalidate()
  assert.equal(states.length, count)
})

test('review invalidation prevents an older summary from restoring a cleared badge', async () => {
  const old = deferred()
  let reads = 0
  const { store } = setup(() => ++reads === 1 ? old.promise : Promise.resolve(counts(0)))
  const first = store.refresh()
  await Promise.resolve()
  store.invalidate()
  await store.refresh(true)
  old.resolve(counts(9))
  assert.equal(await first, null)
  assert.equal(store.getState().data.total, 0)
})

test('logout and account changes clear summary data and reject late cross-account updates', async () => {
  const old = deferred()
  let reads = 0
  const { store, storage } = setup(() => ++reads === 1 ? old.promise : Promise.resolve(counts(2)))
  const first = store.refresh()
  await Promise.resolve()
  storage.token = 'another-admin'
  assert.equal(store.getState().data, null)
  await store.refresh()
  old.resolve(counts(7))
  assert.equal(await first, null)
  assert.equal(store.getState().data.total, 2)
  storage.token = ''
  assert.equal(await store.refresh(), null)
  assert.equal(store.getState().data, null)
  assert.equal(reads, 2)
})

test('failed refresh preserves known counts as stale instead of declaring no pending work', async () => {
  let fail = false
  const { store } = setup(async () => { if (fail) throw { detail: '网络中断' }; return counts(4) })
  await store.refresh()
  fail = true
  await assert.rejects(store.refresh(true))
  assert.equal(store.getState().data.total, 4)
  assert.equal(store.getState().error, '网络中断')
  assert.equal(store.getState().loading, false)
  fail = false
  await store.refresh(true)
  assert.equal(store.getState().error, '')
})

test('revoked management permissions clear cached private summary', async () => {
  let revoked = false
  const { store } = setup(async () => { if (revoked) throw { statusCode: 403, detail: '权限不足' }; return counts(2) })
  await store.refresh()
  revoked = true
  await assert.rejects(store.refresh(true))
  assert.equal(store.getState().data, null)
})

test('only managers fetch and see actionable header notices and numeric drawer badges', async () => {
  let reads = 0
  const { header, navigations } = setup(async () => { reads++; return counts(5) })
  const manager = header()
  await manager.loadUser()
  await manager.loadAdminTodos()
  const tree = parseWxml('<nav-drawer title="北林 MAJOR" show-admin-notice="{{true}}"/>')
  tree.scope = {}
  const html = createRenderer(manager.data)(tree)
  assert.match(html, /5 项待处理/)
  assert.match(html, /在校认证 5 份/)
  assert.match(html, /drawer-todo-badge/)
  manager.goAdmin()
  assert.equal(navigations[0].url, '/pages/admin/admin')
  const ordinary = header('user')
  await ordinary.loadUser()
  assert.equal(ordinary.data.isManager, false)
  assert.equal(reads, 1)
  ordinary.goAdmin()
  assert.equal(navigations.length, 1)
  assert.doesNotMatch(createRenderer(ordinary.data)(tree), /class="admin-notice"/)
})

test('each positive category exposes its real review action and opens that exact admin tab', async () => {
  const action = headerAction('admin-task')
  assert.ok(action, 'A rendered category must expose a tappable action')
  assert.equal(action.attrs['data-tab'], '{{item.tab}}')
  for (const [key, tab, label, unit] of [
    ['verification_count', 'verify', '在校认证', '份'],
    ['rank_application_count', 'rankapps', '段位申请', '份'],
    ['team_count', 'teams', '新建队伍', '支'],
    ['registration_count', 'matches', '赛事报名', '队']
  ]) {
    const summary = { ...counts(0), [key]: 2, total: 2 }
    const { header, navigations } = setup(async () => summary)
    const manager = header(tab === 'rankapps' ? 'reviewer' : 'admin')
    await manager.loadUser()
    await manager.loadAdminTodos()
    const html = renderHeader(manager)
    assert.match(html, new RegExp(label + ' 2 ' + unit))
    assert.equal((html.match(/class="admin-task"/g) || []).length, 1, 'Zero-count categories have no false action')
    assert.equal(manager.data.adminTodoItems.length, 1)
    const item = manager.data.adminTodoItems[0]
    assert.equal(item.tab, tab)
    manager.setData({ open: true })
    manager[action.attrs.bindtap]({ currentTarget: { dataset: { tab: item.tab } } })
    assert.equal(navigations[0].url, '/pages/admin/admin?tab=' + tab)
    assert.equal(manager.data.open, false)
    manager[action.attrs.bindtap]({ currentTarget: { dataset: { tab: 'users' } } })
    assert.equal(navigations.length, 1, 'An unknown review category cannot open another management function')
    const ordinary = header('user')
    await ordinary.loadUser()
    ordinary[action.attrs.bindtap]({ currentTarget: { dataset: { tab } } })
    assert.equal(navigations.length, 1, 'Non-managers cannot use a review shortcut')
  }
})

test('initial summary failure shows unknown status, and retry replaces it with fresh actionable categories', async () => {
  const retryAction = headerAction('retryAdminTodos', 'bindtap')
  assert.ok(retryAction, 'The error notice must expose a retry action')
  let fail = true
  const { header } = setup(async () => {
    if (fail) throw { detail: '暂时无法连接服务器' }
    return { ...counts(0), rank_application_count: 3, total: 3 }
  })
  const manager = header()
  await manager.loadUser()
  await manager.loadAdminTodos()
  const unavailable = renderHeader(manager)
  assert.match(unavailable, /待办状态暂时不可用/)
  assert.match(unavailable, /暂时无法连接服务器/)
  assert.match(unavailable, /重新获取/)
  assert.match(unavailable, /未更新/)
  assert.doesNotMatch(unavailable, /需要你审核|项待处理|class="menu-dot"|class="admin-task"/)
  assert.equal(manager.data.adminTodoCount, null)
  assert.equal(manager.data.adminTodoItems.length, 0)
  fail = false
  await manager[retryAction.attrs.bindtap]()
  const refreshed = renderHeader(manager)
  assert.match(refreshed, /段位申请 3 份/)
  assert.match(refreshed, /3 项待处理/)
  assert.doesNotMatch(refreshed, /待办状态暂时不可用|暂时无法连接服务器|未更新/)
})

test('failed cached counts stay explicitly stale during retry, and a successful zero result clears every badge', async () => {
  let fail = false, retry
  const { header } = setup(async () => {
    if (retry) return retry.promise
    if (fail) throw { detail: '网络中断，请重试' }
    return counts(4)
  })
  const manager = header()
  await manager.loadUser()
  await manager.loadAdminTodos()
  assert.match(renderHeader(manager), /4 项待处理/)
  fail = true
  await manager.retryAdminTodos()
  const failed = renderHeader(manager)
  assert.match(failed, /上次记录：在校认证 4 份/)
  assert.match(failed, /请刷新确认/)
  assert.doesNotMatch(failed, /需要你审核|项待处理|class="menu-dot"|class="admin-task"/)

  retry = deferred()
  const work = manager.retryAdminTodos()
  const loading = renderHeader(manager)
  assert.match(loading, /正在重试/)
  assert.match(loading, /上次记录：在校认证 4 份/)
  assert.doesNotMatch(loading, /需要你审核|项待处理|class="menu-dot"|class="admin-task"/)
  retry.resolve(counts(0))
  await work
  const clear = renderHeader(manager)
  assert.equal(manager.data.adminTodoCount, 0)
  assert.equal(manager.data.adminTodoItems.length, 0)
  assert.equal(manager.data.adminTodoError, false)
  assert.equal(manager.data.adminTodoBadge, '')
  assert.doesNotMatch(clear, /class="admin-notice|class="menu-dot"|drawer-todo-badge|项待处理|未更新/)
})

test('locked registration records alone do not create a red actionable notice or drawer badge', async () => {
  const { header } = setup(async () => ({
    ...counts(0), blocked_registration_count: 2,
    blocked_registration_matches: [{ match_id: 7, match_name: '已编排赛事', count: 2, roster_locked: true }]
  }))
  const manager = header()
  await manager.loadUser()
  await manager.loadAdminTodos()
  assert.equal(manager.data.adminTodoItems.length, 0)
  assert.doesNotMatch(renderHeader(manager), /class="admin-notice|class="menu-dot"|drawer-todo-badge|项待处理/)
})

test('detached headers unsubscribe and never render late todo counts', async () => {
  const pending = deferred()
  const { header } = setup(() => pending.promise)
  const page = header()
  await page.loadUser()
  const work = page.loadAdminTodos()
  page.lifetimes.detached.call(page)
  page.setData = () => { throw new Error('Detached header cannot be updated') }
  pending.resolve(counts(7))
  await work
})

test('admin API wrappers preserve payloads and explain missing versions without alternate writes', async () => {
  const requests = [], module = { exports: {} }
  let missing = false
  vm.runInNewContext(fs.readFileSync(path.join(root, 'utils/api.js'), 'utf8'), { module, wx: {
    getStorageSync: () => 'token', request(options) {
      requests.push(options)
      options.success(missing ? { statusCode: 404, data: { detail: 'Not Found' } } : { statusCode: 200, data: {} })
    }
  } })
  await module.exports.getAdminTeams()
  await module.exports.getAdminTodos()
  await module.exports.getAdminChampion(8)
  const payload = { champion_name: '历史冠军', expected_version: 0, roster: [] }
  await module.exports.saveAdminChampion(8, payload)
  assert.ok(requests[0].url.endsWith('/api/teams/admin'))
  assert.ok(requests[1].url.endsWith('/api/admin/todos'))
  assert.ok(requests[2].url.endsWith('/api/hall/admin/champions/8'))
  assert.equal(requests[3].method, 'PUT')
  assert.equal(requests[3].data, payload)
  missing = true
  await assert.rejects(module.exports.saveAdminChampion(8, payload), error => error.code === 'BACKEND_UPGRADE_REQUIRED')
  await assert.rejects(module.exports.getAdminTodos(), error => error.code === 'BACKEND_UPGRADE_REQUIRED')
  assert.equal(requests.length, 6)
})
