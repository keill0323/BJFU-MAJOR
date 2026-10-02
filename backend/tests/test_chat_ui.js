// node --test backend/tests/test_chat_ui.js
// 执行真实聊天客户端与缓存；模拟 wx、网络和时钟，不连接生产服务器。
const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const root = path.resolve(__dirname, '../../miniprogram')
const KEY = 'cs2_chat_cache_v1'
const plain = value => JSON.parse(JSON.stringify(value))
const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b }); return { promise, resolve, reject } }
async function settle() { for (let i = 0; i < 30; i++) await Promise.resolve() }

function fixture(options = {}) {
  const state = { now: Date.parse('2026-10-02T08:00:00Z'), storage: new Map([['token', 'account-a']]), requests: [], sockets: [],
    timers: new Map(), nextTimer: 0, hooks: {}, toasts: [], navigations: [] }
  const wx = {
    getStorageSync: key => state.storage.get(key),
    setStorageSync: (key, value) => state.storage.set(key, plain(value)),
    removeStorageSync: key => state.storage.delete(key),
    onAppShow: fn => { state.hooks.show = fn }, onAppHide: fn => { state.hooks.hide = fn },
    onNetworkStatusChange: fn => { state.hooks.network = fn },
    navigateTo: value => state.navigations.push(value), showToast: value => state.toasts.push(value),
    reLaunch: value => state.navigations.push(value), stopPullDownRefresh: () => {},
    showModal: value => value.success && value.success({ confirm: true }),
    connectSocket: value => {
      const task = { url: value.url, callbacks: {}, sent: [], closes: 0,
        send(data) { this.sent.push(data) }, close() { this.closes++; if (this.callbacks.close) this.callbacks.close({}) } }
      for (const event of ['Open', 'Message', 'Close', 'Error']) task['on' + event] = fn => { task.callbacks[event.toLowerCase()] = fn }
      state.sockets.push(task); return task
    }
  }
  if (options.noSocket) delete wx.connectSocket
  const api = { BASE: 'https://example.test', chatRequest(pathname, method, body) {
    state.requests.push({ path: pathname, method, body })
    if (options.request) return options.request(pathname, method, body, state)
    if (pathname === '/config') return Promise.resolve({ enabled: true, self_id: 1, is_admin: false })
    if (pathname === '/socket-ticket') return Promise.resolve({ ticket: 'single-use-ticket' })
    return Promise.resolve({ ok: true })
  } }
  const FakeDate = class extends Date { constructor(...args) { super(...(args.length ? args : [state.now])) } static now() { return state.now } }
  const timer = (fn, ms, interval) => { const id = ++state.nextTimer; state.timers.set(id, { fn, ms, interval }); return id }
  const modules = {}
  function load(filename) {
    if (modules[filename]) return modules[filename]
    const module = { exports: {} }, full = path.join(root, filename)
    let definition
    vm.runInNewContext(fs.readFileSync(full, 'utf8'), {
      wx, module, exports: module.exports, console, Date: FakeDate,
      Page: value => { definition = value }, Component: value => { definition = value }, getCurrentPages: () => [],
      setTimeout: (fn, ms) => timer(fn, ms, false), setInterval: (fn, ms) => timer(fn, ms, true),
      clearTimeout: id => state.timers.delete(id), clearInterval: id => state.timers.delete(id),
      require: name => name.endsWith('api.js') ? api : load('utils/' + path.basename(name))
    }, { filename: full })
    modules[filename] = definition || module.exports; return modules[filename]
  }
  function screen(filename) {
    const definition = load(filename), screen = Object.assign({}, definition, definition.methods)
    screen.data = plain(definition.data || {})
    screen.setData = value => Object.assign(screen.data, plain(value))
    return screen
  }
  function run(ms) {
    const timers = Array.from(state.timers.entries()).filter(([, item]) => item.ms === ms)
    for (const [id, item] of timers) { if (!item.interval) state.timers.delete(id); item.fn() }
    return settle()
  }
  return { state, wx, api, load, screen, run, get store() { return load('utils/chat-store.js') }, get client() { return load('utils/chat-client.js') } }
}
function message(id, fixture, extra = {}) {
  return { id: String(id), seq: String(id), sender_id: 1, client_id: 'client-' + id, kind: 'text', content: '消息 ' + id,
    created_at: new Date(fixture.state.now - 1000).toISOString(), read_at: null, state: 'active', ...extra }
}

test('cache restores an account-owned snapshot with a string cursor, never declaring it verified', () => {
  const f = fixture(), store = f.store
  store.bind(1); store.save(10, [message(1, f)], { cursor: '90071992547409931234', has_more: true, next_cursor: '1' })
  const reloaded = fixture()
  reloaded.state.storage.set(KEY, plain(f.state.storage.get(KEY)))
  reloaded.store.bind(1)
  const snapshot = reloaded.store.load(10)
  assert.equal(snapshot.verified, false)
  assert.equal(snapshot.cursor, '90071992547409931234')
  assert.equal(snapshot.items[0].content, '消息 1')
  assert.equal(snapshot.room, undefined, 'stale peer details and permissions are never restored')
  snapshot.items[0].content = 'outside mutation'
  assert.equal(reloaded.store.load(10).items[0].content, '消息 1')
})

test('messages older than 24 hours, pending rows and purged content never persist; recall strips its body', () => {
  const f = fixture(), store = f.store
  store.bind(1)
  store.save(10, [message(1, f), message(2, f, { created_at: new Date(f.state.now - 86400000).toISOString() }),
    message(3, f, { pending: true }), message(4, f, { state: 'purged' }), message(5, f, { state: 'recalled', content: 'must disappear' })], { cursor: '5' })
  const data = store.load(10)
  assert.deepEqual(plain(data.items.map(row => row.id)), ['1', '5'])
  assert.equal(data.items[1].content, null)
  assert.doesNotMatch(JSON.stringify(f.state.storage.get(KEY)), /must disappear/)
  f.state.now += 86400000
  assert.equal(store.load(10), null, 'all-expired cache requires authoritative history')
  assert.equal(f.state.storage.get(KEY).rooms[10].items.length, 0)
})

test('cache caps each conversation at 100 rows and keeps a pagination boundary that cannot skip trimmed messages', () => {
  const f = fixture(), store = f.store
  store.bind(1)
  store.save(10, Array.from({ length: 125 }, (_, i) => message(i + 1, f)), { cursor: '200', has_more: false })
  const cached = store.load(10)
  assert.equal(cached.items.length, 100)
  assert.equal(cached.items[0].id, '26')
  assert.equal(cached.has_more, true)
  assert.equal(cached.next_cursor, '26')
})

test('restoring cache after some older rows expire resets the boundary so unread server history stays accessible', () => {
  const f = fixture(), store = f.store
  store.bind(1)
  store.save(10, [message(1, f, { created_at: new Date(f.state.now - 86300000).toISOString() }), message(2, f)], { cursor: '2' })
  const restored = fixture()
  restored.state.now += 200000
  restored.state.storage.set(KEY, plain(f.state.storage.get(KEY)))
  restored.store.bind(1)
  const cache = restored.store.load(10)
  assert.equal(cache.items.length, 1)
  assert.equal(cache.next_cursor, '2')
  assert.equal(cache.has_more, true)
})

test('cache bounds conversation count and total UTF-8 bytes including Chinese and emoji', () => {
  const f = fixture(), store = f.store
  store.bind(1)
  for (let i = 1; i <= 24; i++) {
    f.state.now++
    store.save(i, [message(i, f)], { cursor: String(i) })
  }
  assert.equal(Object.keys(f.state.storage.get(KEY).rooms).length, 20)
  assert.equal(store.load(1), null)
  const large = '字🙂'.repeat(450)
  for (let i = 1; i <= 12; i++) {
    f.state.now++
    store.save(i, Array.from({ length: 100 }, (_, j) => message(j + 1, f, { content: large })), { cursor: '100' })
  }
  assert.ok(Buffer.byteLength(JSON.stringify(f.state.storage.get(KEY)), 'utf8') <= 2 * 1024 * 1024)
  assert.ok(store.load(12), 'most recently used conversation survives eviction')
})

test('binding another account and clearing login immediately removes the previous cache', () => {
  const f = fixture(), store = f.store
  store.bind(1); store.save(10, [message(1, f)], { cursor: '1' })
  store.bind(2)
  assert.equal(store.load(10), null)
  assert.doesNotMatch(JSON.stringify(f.state.storage.get(KEY)), /消息 1/)
  store.clear()
  assert.equal(f.state.storage.has(KEY), false)
})

test('event merge sorts and de-duplicates string big integers, normalizes IDs, and removes deleted or recalled text', () => {
  const f = fixture(), store = f.store, old = message(1, f)
  const rows = store.merge([old, message(2, f), message(3, f)], [
    { seq: '90071992547409930004', message_id: '1', message: { ...old, state: 'recalled', content: 'stale recalled body' } },
    { seq: '90071992547409930003', message_id: 1, message: { ...old, content: 'earlier' } },
    { seq: '90071992547409930005', message_id: '2', message: null },
    { seq: '90071992547409930006', message_id: '3', message: { ...message(3, f), state: 'purged' } },
    { seq: '90071992547409930002', message_id: '9', message: message(9, f, { seq: '90071992547409930001' }) },
    { seq: '90071992547409930002', message_id: '9', message: message(9, f, { seq: '90071992547409930001' }) }
  ])
  assert.equal(rows.length, 2)
  assert.equal(rows[0].id, '1')
  assert.equal(rows[0].content, null)
  assert.equal(rows[1].id, '9')
  assert.equal(store.compare('00010', '9'), 1)
  assert.equal(store.compare('90071992547409931235', '90071992547409931234'), 1)
  assert.equal(store.merge([], [{ seq: '5', message: message(1, f) }], '5').length, 0)
})

test('first ensure restores same-account cache after app restart rather than deleting it', async () => {
  const f = fixture()
  f.store.bind(1); f.store.save(10, [message(1, f)], { cursor: '1' })
  await f.client.ensure()
  assert.equal(f.client.store.load(10).items.length, 1)
})

test('concurrent subscribers and config requests share one WebSocket and one config request', async () => {
  const f = fixture(), a = [], b = [], client = f.client
  const offA = client.subscribe(event => a.push(event)), offB = client.subscribe(event => b.push(event))
  await Promise.all([client.ensure(), client.ensure()]); await settle()
  assert.equal(f.state.requests.filter(row => row.path === '/config').length, 1)
  assert.equal(f.state.sockets.length, 1)
  const socket = f.state.sockets[0]
  assert.equal(socket.url, 'wss://example.test/api/chat/ws')
  socket.callbacks.open()
  assert.equal(JSON.parse(socket.sent[0].data).ticket, 'single-use-ticket')
  socket.callbacks.message({ data: JSON.stringify({ type: 'ready' }) })
  socket.callbacks.message({ data: JSON.stringify({ type: 'dirty', conversation_id: 10, seq: '9007199254740993' }) })
  assert.ok(a.some(event => event.type === 'dirty'))
  assert.ok(b.some(event => event.type === 'dirty'))
  offA(); assert.equal(socket.closes, 0)
  offB(); assert.equal(socket.closes, 1)
  assert.equal(f.state.timers.size, 0)
})

test('background suspends transport and polling, foreground creates a fresh socket, network recovery reconnects', async () => {
  const f = fixture(), events = [], off = f.client.subscribe(event => events.push(event))
  await settle()
  const first = f.state.sockets[0]
  first.callbacks.message({ data: '{"type":"ready"}' })
  f.state.hooks.hide()
  assert.equal(first.closes, 1)
  assert.equal(f.state.timers.size, 0)
  f.state.hooks.show(); await settle()
  assert.equal(f.state.sockets.length, 2)
  f.state.hooks.network({ isConnected: false })
  assert.equal(f.state.sockets[1].closes, 1)
  assert.equal(f.state.timers.size, 0)
  f.state.hooks.network({ isConnected: true }); await settle()
  assert.equal(f.state.sockets.length, 3)
  off()
})

test('handshake timeout and socket errors enter fallback; stale socket events cannot affect the new connection', async () => {
  const f = fixture(), events = [], off = f.client.subscribe(event => events.push(event))
  await settle()
  const first = f.state.sockets[0]
  await f.run(12000)
  assert.equal(first.closes, 1)
  assert.ok(events.some(event => event.type === 'status' && event.status === 'offline'))
  const reconnect = Array.from(f.state.timers.values()).find(item => !item.interval && item.ms < 2000)
  assert.ok(reconnect)
  reconnect.fn(); await settle()
  assert.equal(f.state.sockets.length, 2)
  const count = events.length
  first.callbacks.message({ data: '{"type":"ready"}' })
  first.callbacks.message({ data: '{"type":"dirty","conversation_id":1}' })
  assert.equal(events.length, count)
  f.state.sockets[1].callbacks.error({})
  assert.equal(f.state.sockets[1].closes, 1)
  off()
})

test('fallback polling occurs only offline and in foreground, and unsupported WebSocket still polls', async () => {
  const f = fixture(), events = [], off = f.client.subscribe(event => events.push(event))
  await settle()
  await f.run(10000)
  const offlineTicks = events.filter(event => event.type === 'tick').length
  assert.equal(offlineTicks, 1)
  f.state.sockets[0].callbacks.message({ data: '{"type":"ready"}' })
  await f.run(10000)
  assert.equal(events.filter(event => event.type === 'tick').length, offlineTicks)
  f.state.hooks.hide(); await f.run(10000)
  assert.equal(events.filter(event => event.type === 'tick').length, offlineTicks)
  off()
  const noSocket = fixture({ noSocket: true }), fallback = []
  const stop = noSocket.client.subscribe(event => fallback.push(event))
  await settle(); await noSocket.run(10000)
  assert.ok(fallback.some(event => event.type === 'tick'))
  stop()
})

test('account change invalidates pending config and request responses without restoring old account data', async () => {
  const old = deferred()
  const f = fixture({ request: (pathname, _method, _body, state) => pathname === '/config' && state.storage.get('token') === 'account-a'
    ? old.promise : Promise.resolve({ enabled: true, self_id: 2 }) })
  const original = f.client.ensure()
  f.state.storage.set('token', 'account-b'); f.client.account()
  old.resolve({ enabled: true, self_id: 1 })
  await assert.rejects(original, err => err.stale && err.code === 'CHAT_SESSION_CHANGED')
  await f.client.ensure()
  f.client.store.save(10, [message(1, f)], { cursor: '1' })
  assert.equal(f.state.storage.get(KEY).owner, '2')
  const pending = deferred()
  f.api.chatRequest = () => pending.promise
  const request = f.client.call('/summary')
  f.state.storage.delete('token'); f.client.account()
  pending.resolve({ unread: 123 })
  await assert.rejects(request, err => err.stale)
  assert.equal(f.state.storage.has(KEY), false)
})

test('logout closes the socket immediately, clears cache and ignores its queued notifications', async () => {
  const f = fixture(), events = [], off = f.client.subscribe(event => events.push(event))
  await settle()
  f.client.store.save(10, [message(1, f)], { cursor: '1' })
  const socket = f.state.sockets[0]
  f.state.storage.delete('token'); f.client.account()
  assert.equal(socket.closes, 1)
  assert.equal(f.state.storage.has(KEY), false)
  const size = events.length
  socket.callbacks.message({ data: '{"type":"dirty","conversation_id":1}' })
  assert.equal(events.length, size)
  assert.ok(events.some(event => event.type === 'account' && !event.authenticated))
  off()
})

test('timeout retries reuse an immutable client ID even if the original server response arrives later', async () => {
  const pending = deferred(), f = fixture({ request: () => pending.promise }), client = f.client
  const packet = client.packet('一次发送')
  assert.equal(Object.isFrozen(packet), true)
  const first = client.call('/conversations/10/messages', 'POST', packet)
  const rejected = assert.rejects(first, err => err.timeout && err.code === 'CHAT_REQUEST_TIMEOUT')
  await f.run(15000); await rejected
  f.api.chatRequest = (pathname, method, body) => { f.state.requests.push({ path: pathname, method, body }); return Promise.resolve({ saved: true }) }
  assert.equal((await client.call('/conversations/10/messages', 'POST', packet)).saved, true)
  pending.resolve({ saved: true }); await settle()
  const sends = f.state.requests.filter(row => row.method === 'POST')
  assert.equal(sends.length, 2)
  assert.equal(sends[0].body.client_id, sends[1].body.client_id)
  assert.equal(sends[0].body, sends[1].body)
})

test('contact aborts navigation and toasts when the source page or account is no longer current', async () => {
  const pending = deferred()
  const f = fixture({ request: pathname => pathname === '/config' ? Promise.resolve({ enabled: true, self_id: 1 }) : pending.promise })
  let visible = true
  const opening = f.client.contact(2, 'user', false, () => visible)
  await settle(); visible = false; pending.resolve({ id: 5 }); await opening
  assert.equal(f.state.navigations.length, 0)
  assert.equal(f.state.toasts.length, 0)
  const old = deferred(); f.api.chatRequest = () => old.promise
  const second = f.client.contact(2)
  await settle(); f.state.storage.set('token', 'account-b'); f.client.account(); old.reject({ detail: 'old account failure' }); await second
  assert.equal(f.state.toasts.length, 0)
})

function room(id, extra = {}) {
  return { id, peer: { id: id + 100, nickname: '选手 ' + id }, pinned: false, updated_at: '2026-10-02T08:00:00Z', latest: null, unread: 0, ...extra }
}
function listResult(items, next_cursor = null) { return { items, next_cursor, has_more: !!next_cursor } }
function pageRequest(handler) {
  return (pathname, method, body, state) => {
    if (pathname === '/config') return Promise.resolve({ enabled: true, self_id: state.storage.get('token') === 'account-a' ? 1 : 2, is_admin: false })
    if (pathname === '/socket-ticket') return Promise.resolve({ ticket: 'ticket' })
    return handler(pathname, method, body, state)
  }
}

test('conversation pagination encodes opaque cursors and de-duplicates the same room across pages', async () => {
  const cursor = 'opaque+cursor/with=padding'
  const f = fixture({ request: pageRequest(pathname => Promise.resolve(pathname.includes('?')
    ? listResult([room(2, { unread: 3 }), room(3)]) : listResult([room(1, { pinned: true }), room(2)], cursor))) })
  const page = f.screen('pages/chats/chats.js')
  page.onShow(); await settle()
  assert.equal(page.data.rooms.length, 2)
  await page.onReachBottom(); await settle()
  assert.ok(f.state.requests.some(row => row.path === '/conversations?cursor=' + encodeURIComponent(cursor)))
  assert.equal(page.data.rooms.length, 3)
  assert.equal(page.data.rooms.find(row => row.id === 2).unread, 3)
  assert.equal(page.data.rooms[0].id, 1, 'pinned rooms stay first')
  page.stop()
})

test('hidden conversation requests cannot overwrite or release a fresh foreground request', async () => {
  const old = deferred(), fresh = deferred(); let lists = 0
  const f = fixture({ request: pageRequest(() => (++lists === 1 ? old.promise : fresh.promise)) })
  const page = f.screen('pages/chats/chats.js')
  page.onShow(); await settle(); assert.equal(lists, 1)
  page.onHide(); page.onShow(); await settle(); assert.equal(lists, 2)
  old.resolve(listResult([room(1)])); await settle()
  assert.equal(page.data.rooms.length, 0)
  assert.equal(page.data.loading, true)
  fresh.resolve(listResult([room(2)])); await settle()
  assert.equal(page.data.rooms[0].id, 2)
  assert.equal(page.data.loading, false)
  page.stop()
})

test('account switch clears list, contacts and administrator state before fetching the new account', async () => {
  const next = deferred()
  const f = fixture({ request: pageRequest((_pathname, _method, _body, state) => state.storage.get('token') === 'account-a'
    ? Promise.resolve(listResult([room(1)])) : next.promise) })
  const page = f.screen('pages/chats/chats.js')
  page.onShow(); await settle()
  page.setData({ people: [{ id: 55 }], isAdmin: true, keyword: '旧账号查找' })
  f.state.storage.set('token', 'account-b'); f.client.account()
  assert.equal(page.data.rooms.length, 0)
  assert.equal(page.data.people.length, 0)
  assert.equal(page.data.isAdmin, false)
  assert.equal(page.data.keyword, '')
  await settle(); next.resolve(listResult([room(2)])); await settle()
  assert.equal(page.data.rooms[0].id, 2)
  page.stop()
})

test('search de-duplicates pending identical terms and ignores old results after changing or closing search', async () => {
  const old = deferred(), fresh = deferred(); let searches = 0
  const f = fixture({ request: pageRequest(pathname => {
    if (pathname.startsWith('/contacts')) return ++searches === 1 ? old.promise : fresh.promise
    return Promise.resolve(listResult([]))
  }) })
  const page = f.screen('pages/chats/chats.js')
  page.onShow(); await settle()
  page.input({ detail: { value: '第一位' } })
  const first = page.search(); page.search(); await settle()
  assert.equal(searches, 1)
  page.input({ detail: { value: '第二位' } })
  const second = page.search(); await settle()
  old.resolve({ items: [{ id: 1 }] }); await first
  assert.equal(page.data.people.length, 0)
  assert.equal(page.data.searching, true)
  fresh.resolve({ items: [{ id: 2 }] }); await second
  assert.equal(page.data.people[0].id, 2)
  await page.search(); assert.equal(searches, 2, 'completed identical result is reused')
  page.closeSearch(); assert.equal(page.data.people.length, 0)
  page.stop()
})

test('homepage badge resets at logout and a stale hidden summary cannot clear its fresh loading request', async () => {
  const old = deferred(), fresh = deferred(); let requests = 0
  const f = fixture({ request: pageRequest(pathname => pathname === '/summary' ? (++requests === 1 ? old.promise : fresh.promise) : Promise.resolve({})) })
  const badge = f.screen('components/chat-entry/chat-entry.js')
  badge.start(); await settle(); badge.stop(); badge.start(); await settle()
  old.resolve({ unread: 88 }); await settle()
  assert.equal(badge.data.unread, 0)
  assert.ok(badge._request, 'new foreground summary remains active')
  fresh.resolve({ unread: 3 }); await settle()
  assert.equal(badge.data.unread, 3)
  f.state.storage.delete('token'); f.client.account()
  assert.equal(badge.data.unread, 0)
  badge.stop()
})

test('login and all three logout paths invalidate cached chat as soon as their token changes', async () => {
  const login = fixture()
  login.api.login = async () => ({ access_token: 'account-b' }); login.wx.login = async () => ({ code: 'code' })
  login.client.store.bind(1); login.client.store.save(10, [message(1, login)], { cursor: '1' })
  const page = login.screen('pages/login/login.js'); page.data.agreed = true
  await page.handleLogin()
  assert.equal(login.state.storage.get('token'), 'account-b')
  assert.equal(login.state.storage.has(KEY), false)
  for (const filename of ['pages/settings/settings.js', 'pages/profile/profile.js', 'components/nav-drawer/nav-drawer.js']) {
    const f = fixture(), screen = f.screen(filename)
    f.client.store.bind(1); f.client.store.save(10, [message(1, f)], { cursor: '1' })
    if (screen.logout) screen.logout()
    else screen.go({ currentTarget: { dataset: { url: '/pages/login/login' } } })
    assert.equal(f.state.storage.has('token'), false, filename)
    assert.equal(f.state.storage.has(KEY), false, filename)
  }
})

test('HTTP and upload 401 responses expire only their own token and immediately notify the chat account', async () => {
  for (const upload of [false, true]) {
    for (const switched of [false, true]) {
      let token = 'account-a', pending, resets = 0
      const wx = { getStorageSync: () => token, removeStorageSync: () => { token = '' },
        request: value => { pending = value }, uploadFile: value => { pending = value } }
      const module = { exports: {} }, filename = path.join(root, 'utils/api.js')
      vm.runInNewContext(fs.readFileSync(filename, 'utf8'), { wx, module,
        require: name => { assert.equal(name, './chat-client.js'); return { resetAccount: () => resets++ } } }, { filename })
      const api = module.exports, operation = upload ? api.uploadAvatar('/local.png') : api.getMe()
      if (switched) token = 'account-b'
      pending.success({ statusCode: 401, data: upload ? '{"detail":"invalid"}' : { detail: 'invalid' } })
      await assert.rejects(operation, err => err.unauthorized)
      assert.equal(token, switched ? 'account-b' : '')
      assert.equal(resets, switched ? 0 : 1)
    }
  }
})
