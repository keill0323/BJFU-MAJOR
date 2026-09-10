const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const { parseWxml, createRenderer } = require('../../tools/preview-miniprogram.js')
const root = path.resolve(__dirname, '../../miniprogram')
const counts = n => ({ verification_count: n, rank_application_count: 0, team_count: 0, registration_count: 0, total: n, registration_matches: [] })
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
  assert.match(html, /认证 5/)
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
