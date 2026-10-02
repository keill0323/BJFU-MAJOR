// 真实聊天页使用内存 wx/HTTP/选择器；不访问线上，不运行真实计时器。
const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm')
const root = path.resolve(__dirname, '../../miniprogram')
const plain = value => JSON.parse(JSON.stringify(value))
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no }); return { promise, resolve, reject } }
const flush = async (count = 30) => { for (let n = 0; n < count; n++) await Promise.resolve() }
const click = id => ({ currentTarget: { dataset: { id } } })
const message = (id, overrides = {}) => ({ id: String(id), seq: String(id), sender_id: 2,
  client_id: 'server-message-client-' + id, kind: 'text', content: '正文 ' + id,
  state: 'active', created_at: new Date().toISOString(), read_at: null, ...overrides })
const room = () => ({ id: 7, peer: { id: 2, nickname: '对方', available: true },
  write_enabled: true, blocked: false, muted_until: null, unread: 0 })

function setup(overrides = {}) {
  const state = { token: 'account-a', selfId: 1, calls: [], packets: [], toasts: [], intervals: new Set(),
    listeners: new Set(), cache: new Map(), history: [message(1)], more: false, nextCursor: null,
    forgotten: [], saved: [], selections: [], selectorAuto: true, rects: [], viewport: { top: 100, bottom: 400, height: 300 } }
  const wx = {
    getStorageSync: key => key === 'token' ? state.token : state.cache.get(key),
    setStorageSync: (key, value) => state.cache.set(key, plain(value)), removeStorageSync: key => state.cache.delete(key),
    getWindowInfo: () => ({ windowHeight: 750, windowWidth: 375 }),
    showToast: options => state.toasts.push(options), showModal: options => options.success({ confirm: true }),
    showActionSheet: options => options.success({ tapIndex: 0 }), navigateTo() {}, setClipboardData() {}
  }
  const storeModule = { exports: {} }
  vm.runInNewContext(fs.readFileSync(path.join(root, 'utils/chat-store.js'), 'utf8'), { module: storeModule, wx, console, Date })
  const store = storeModule.exports
  const originalSave = store.save, originalForget = store.forget
  store.save = (...args) => { state.saved.push(plain(args)); return originalSave(...args) }
  store.forget = cid => { state.forgotten.push(cid); return originalForget(cid) }
  const chat = {
    store,
    ensure: async () => { if (overrides.ensure) await overrides.ensure(state); store.bind(state.selfId); return { self_id: state.selfId, enabled: true } },
    subscribe: listener => { state.listeners.add(listener); return () => state.listeners.delete(listener) },
    packet: (content, kind = 'text', sourceId = null) => {
      const packet = Object.freeze({ client_id: 'immutable-packet-' + (state.packets.length + 1),
        client_created_at: new Date().toISOString(), content, kind, source_id: sourceId })
      state.packets.push(packet); return packet
    },
    call: async (route, method, body) => {
      state.calls.push({ route, method, body })
      if (overrides.call) {
        const response = overrides.call(route, method, body, state)
        if (response !== undefined) return response
      }
      if (method === 'POST') return { message: message(501, { sender_id: 1, client_id: body.client_id, content: body.content }) }
      if (method === 'PUT') return { message: '已读已确认' }
      if (route.includes('/events')) return { items: [], cursor: route.split('after_seq=')[1] || '0', room: room(), has_more: false }
      return { items: plain(state.history), cursor: '500', room: room(), has_more: state.more, next_cursor: state.nextCursor }
    }
  }
  let page
  vm.runInNewContext(fs.readFileSync(path.join(root, 'pages/chat/chat.js'), 'utf8'), {
    Page: definition => { page = definition }, require: name => { assert.match(name, /chat-client\.js$/); return chat },
    wx, console, Date,
    setInterval: fn => { state.intervals.add(fn); return fn }, clearInterval: fn => state.intervals.delete(fn),
    setTimeout: fn => fn, clearTimeout() {}
  })
  page.data = plain(page.data)
  page.setData = (data, callback) => { Object.assign(page.data, data); if (callback) callback() }
  page.onLoad({ id: '7' })
  function installSelectors() {
    page.createSelectorQuery = () => ({
      select: selector => { assert.equal(selector, '#thread'); return { boundingClientRect() {} } },
      selectAll: selector => { assert.equal(selector, '.incoming-unread'); return { fields: options => assert.deepEqual(plain(options), { rect: true, dataset: true }) } },
      exec: callback => {
        const selected = () => [state.viewport, state.rects.filter(rect => page.data.rows.some(row => row.id === String(rect.dataset.messageId) && !row.mine && !row.read_at && row.state === 'active'))]
        state.selections.push(() => callback(selected()))
        if (state.selectorAuto) callback(selected())
      }
    })
  }
  async function open() { page.onShow(); await flush(); return page }
  return { page, state, chat, store, open, installSelectors }
}

test('cached bodies are invisible until server delete and recall changes have been verified', async () => {
  const update = deferred()
  let first = true
  const setupResult = setup({ call: route => route.includes('/events') && first ? (first = false, update.promise) : undefined })
  const { page, state, store } = setupResult
  store.bind(1); store.save(7, [message(1), message(2), message(3)], { cursor: '3' })
  page.onShow(); await flush()
  assert.equal(page.data.rows.length, 0)
  assert.equal(page.data.canSend, false)
  assert.equal(state.calls[0].route, '/conversations/7/events?after_seq=3')
  update.resolve({ items: [
    { seq: '4', kind: 'delete', message_id: '1', message: null },
    { seq: '5', kind: 'recall', message_id: '2', message: message(2, { state: 'recalled', content: null }) }
  ], cursor: '5', has_more: false, room: room() })
  await flush()
  assert.deepEqual(plain(page.data.rows.map(row => [row.id, row.content, row.state])), [['2', null, 'recalled'], ['3', '正文 3', 'active']])
  assert.equal(page.data.canSend, true)
  page.onUnload()
})

test('failed cache validation discards saved bodies and never falls back to showing them', async () => {
  const { page, state, store, open } = setup({ call: route => route.includes('/events') ? Promise.reject({ detail: '验证同步失败' }) : undefined })
  store.bind(1); store.save(7, [message(1)], { cursor: '1' })
  await open()
  assert.equal(page.data.rows.length, 0)
  assert.equal(page.data.canSend, false)
  assert.match(page.data.error, /验证同步失败/)
  assert.equal(store.load(7), null)
  assert.equal(state.calls.some(call => call.route === '/conversations/7/messages'), false)
  page.onUnload()
})

test('a reset response fetches a fresh snapshot without displaying the old cache during the request', async () => {
  const snapshot = deferred(); let first = true
  const { page, store } = setup({ call: route => {
    if (route.includes('/events') && first) { first = false; return { reset: true } }
    if (route === '/conversations/7/messages') return snapshot.promise
  } })
  store.bind(1); store.save(7, [message(1, { content: '已过期旧正文' })], { cursor: '1' })
  page.onShow(); await flush()
  assert.equal(page.data.rows.length, 0)
  snapshot.resolve({ items: [message(2)], cursor: '2', has_more: false, room: room() })
  await flush()
  assert.deepEqual(plain(page.data.rows.map(row => row.id)), ['2'])
  page.onUnload()
})

test('returning to the same account clears previous screen bodies before revalidating cached changes', async () => {
  let blocked = false; const recheck = deferred()
  const { page, open } = setup({ call: route => blocked && route.includes('/events') ? recheck.promise : undefined })
  await open(); assert.equal(page.data.rows.length, 1)
  page.onHide(); blocked = true; page.onShow(); await flush()
  assert.equal(page.data.rows.length, 0)
  assert.equal(page.data.canSend, false)
  blocked = false
  recheck.resolve({ items: [{ seq: '501', kind: 'delete', message_id: '1', message: null }], cursor: '501', room: room(), has_more: false })
  await flush(); assert.equal(page.data.rows.length, 0)
  page.onUnload()
})

test('account changes immediately clear previous messages, draft and pending sends', async () => {
  const response = deferred()
  const { page, state, open } = setup({ call: (_route, method) => method === 'POST' ? response.promise : undefined })
  await open()
  page.input({ detail: { value: '未完成发送' } }); await page.send(); await flush()
  page.input({ detail: { value: '私人草稿' } })
  assert.equal(page._pending.length, 1)
  state.token = 'account-b'; state.selfId = 3
  state.listeners.forEach(listener => listener({ type: 'account' }))
  assert.equal(page.data.rows.length, 0); assert.equal(page.data.draft, '')
  assert.equal(page.data.room, null); assert.equal(page.data.canSend, false)
  assert.equal(page._pending.length, 0)
  response.resolve({ message: message(501, { sender_id: 1, content: '旧账号私密发送' }) })
  await flush()
  assert.equal(page._items.length, 0); assert.equal(page.data.rows.length, 0)
  page.onUnload()
})

test('an old in-flight sync neither blocks re-entry sync nor overwrites the new snapshot', async () => {
  const oldSync = deferred(); let hold = false
  const { page, state, store, open } = setup({ call: route => hold && route.includes('/events') ? oldSync.promise : undefined })
  await open()
  hold = true; const pending = page.sync(); await flush()
  page.onHide(); store.forget(7); state.history = [message(2)]; hold = false
  page.onShow(); await flush()
  assert.deepEqual(plain(page.data.rows.map(row => row.id)), ['2'])
  assert.equal(page._syncing, null)
  oldSync.resolve({ items: [{ seq: '999', kind: 'delete', message_id: '2', message: null }], cursor: '999', room: room(), has_more: false })
  await pending; await flush()
  assert.deepEqual(plain(page.data.rows.map(row => row.id)), ['2'])
  assert.equal(page._cursor, '500')
  page.onUnload()
})

test('late delivery from a previous page generation cannot resurrect a body after new synchronization', async () => {
  const response = deferred()
  const { page, store, open } = setup({ call: (_route, method) => method === 'POST' ? response.promise : undefined })
  await open()
  page.input({ detail: { value: '之后已被清理' } }); await page.send(); await flush()
  const packet = page._pending[0].packet
  page.onHide(); store.forget(7); page.onShow(); await flush()
  response.resolve({ message: message(501, { sender_id: 1, content: packet.content, client_id: packet.client_id }) })
  await flush()
  assert.equal(page._items.some(item => item.id === '501'), false)
  page.render(false)
  assert.equal(page.data.rows.some(item => !item.pending && item.id === '501'), false)
  page.onUnload()
})

test('failed-send retry preserves the same immutable packet and replaces the local row with one acknowledgement', async () => {
  let posts = 0
  const { page, state, open } = setup({ call: (_route, method) => method === 'POST' && posts++ === 0 ? Promise.reject({ detail: '网络超时' }) : undefined })
  await open(); page.input({ detail: { value: ' 重试这一条 ' } }); await page.send(); await flush()
  assert.equal(page._pending.length, 1); assert.equal(page._pending[0].failed, true)
  const pendingId = page._pending[0].id
  page.retry(click(pendingId)); await flush()
  const calls = state.calls.filter(call => call.method === 'POST')
  assert.equal(calls.length, 2); assert.equal(calls[0].body, calls[1].body)
  assert.equal(Object.isFrozen(calls[0].body), true); assert.equal(state.packets.length, 1)
  assert.equal(calls[1].body.content, '重试这一条')
  assert.equal(page._pending.length, 0)
  assert.equal(page.data.rows.filter(row => row.client_id === calls[0].body.client_id).length, 1)
  page.onUnload()
})

test('visible read acknowledgement includes intersecting incoming messages and excludes off-screen rows', async () => {
  const { page, state, open, installSelectors } = setup()
  state.history = [1, 2, 3, 4].map(id => message(id))
  await open(); installSelectors()
  state.rects = [
    { top: 120, bottom: 160, dataset: { messageId: '1' } },
    { top: 20, bottom: 80, dataset: { messageId: '2' } },
    { top: 450, bottom: 500, dataset: { messageId: '3' } },
    { top: 80, bottom: 120, dataset: { messageId: '4' } }
  ]
  page.observeRead(); await flush()
  const calls = state.calls.filter(call => call.method === 'PUT')
  assert.equal(calls.length, 1); assert.deepEqual(plain(calls[0].body), { message_ids: [1, 4] })
  assert.ok(page._items.find(row => row.id === '1').read_at)
  assert.ok(page._items.find(row => row.id === '4').read_at)
  assert.equal(page._items.find(row => row.id === '2').read_at, null)
  assert.equal(page._items.find(row => row.id === '3').read_at, null)
  page.onUnload()
})

test('simultaneous visibility callbacks coalesce one read request and callbacks from a hidden page do nothing', async () => {
  const acknowledgement = deferred()
  const { page, state, open, installSelectors } = setup({ call: (_route, method) => method === 'PUT' ? acknowledgement.promise : undefined })
  await open(); installSelectors(); state.selectorAuto = false
  state.rects = [{ top: 120, bottom: 160, dataset: { messageId: '1' } }]
  page.observeRead(); page.observeRead()
  state.selections[0](); state.selections[1](); await flush()
  assert.equal(state.calls.filter(call => call.method === 'PUT').length, 1)
  page.onHide()
  acknowledgement.resolve({}); await flush()
  assert.equal(page._items[0].read_at, null)
  page._visible = true; page._generation++; page._reading = null
  page.observeRead(); const callback = state.selections.at(-1); page.onHide(); callback(); await flush()
  assert.equal(state.calls.filter(call => call.method === 'PUT').length, 1)
  page.onUnload()
})

test('failed read requests never mark local messages read and allow a later retry', async () => {
  let fail = true
  const { page, state, open, installSelectors } = setup({ call: (_route, method) => method === 'PUT' && fail ? Promise.reject({ detail: '已读确认失败' }) : undefined })
  await open(); installSelectors()
  state.rects = [{ top: 120, bottom: 160, dataset: { messageId: '1' } }]
  page.observeRead(); await flush()
  assert.equal(page._items[0].read_at, null); assert.equal(page._reading, null)
  fail = false; page.observeRead(); await flush()
  assert.ok(page._items[0].read_at)
  assert.equal(state.calls.filter(call => call.method === 'PUT').length, 2)
  page.onUnload()
})

test('history stays within 200 rows, forgets old-window cache and ignores newer additions until returning to latest', async () => {
  let newEvent = false
  const { page, state, store, open } = setup({ call: route => {
    if (route.includes('messages?before_seq=')) return { items: Array.from({ length: 100 }, (_, n) => message(n + 1)), next_cursor: null, has_more: false }
    if (route.includes('/events') && newEvent) {
      newEvent = false
      return { items: [{ seq: '501', kind: 'message', message_id: '301', message: message(301) }], cursor: '501', room: room(), has_more: false }
    }
  } })
  state.history = Array.from({ length: 200 }, (_, n) => message(n + 101)); state.more = true; state.nextCursor = '101'
  await open(); assert.equal(page.data.rows.length, 200)
  assert.equal(store.load(7).items.length, 100)
  await page.older(); await flush()
  assert.equal(page.data.rows.length, 200)
  assert.equal(page.data.rows[0].id, '1'); assert.equal(page.data.rows.at(-1).id, '200')
  assert.equal(page._olderWindow, true); assert.equal(store.load(7), null)
  newEvent = true; await page.sync(); await flush()
  assert.equal(page.data.rows.some(row => row.id === '301'), false); assert.equal(page.data.newMessages, true)
  state.history = Array.from({ length: 200 }, (_, n) => message(n + 201)); state.nextCursor = '201'
  page.bottom(); await flush()
  assert.equal(page._olderWindow, false); assert.equal(page.data.rows.length, 200)
  assert.equal(page.data.rows[0].id, '201'); assert.equal(page.data.rows.at(-1).id, '400')
  page.onUnload()
})

test('failed pending sends are capped at ten without creating additional packets or network requests', async () => {
  const { page, state, open } = setup({ call: (_route, method) => method === 'POST' ? Promise.reject({ detail: '断网' }) : undefined })
  await open()
  for (let n = 0; n < 11; n++) { page.input({ detail: { value: '消息 ' + n } }); await page.send(); await flush() }
  assert.equal(page._pending.length, 10); assert.equal(state.packets.length, 10)
  assert.equal(state.calls.filter(call => call.method === 'POST').length, 10)
  assert.match(state.toasts.at(-1).title, /重试或长按移除/)
  page.onUnload(); assert.equal(state.intervals.size, 0); assert.equal(state.listeners.size, 0)
})
