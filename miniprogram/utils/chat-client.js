const api = require('./api.js')
const store = require('./chat-store.js')
let token = wx.getStorageSync('token') || '', session = 0, generation = 0, socket = null, opening = null
let reconnect = null, heartbeat = null, poll = null, handshake = null, retries = 0
let config = null, configTime = 0, ensuring = null, status = 'idle', lastPong = 0
let foreground = true, network = true, lifecycleRegistered = false
const listeners = new Set()
function emit(event) { listeners.forEach(fn => { try { fn(event) } catch (_) {} }) }
function changed() { return { detail: '登录状态已变化，请重新打开聊天', stale: true, code: 'CHAT_SESSION_CHANGED' } }
function current(s, t) { return s === session && t === token && t === (wx.getStorageSync('token') || '') }
function alive(g, t) { return g === generation && t === token && t === (wx.getStorageSync('token') || '') && listeners.size > 0 && foreground && network }
function close() {
  generation++; opening = null
  clearTimeout(reconnect); clearTimeout(handshake); clearInterval(heartbeat); clearInterval(poll)
  reconnect = handshake = heartbeat = poll = null
  const old = socket; socket = null
  if (old) { try { old.close({}) } catch (_) {} }
  status = 'idle'
}
function resetAccount() {
  close(); session++; store.clear(); config = null; configTime = 0; ensuring = null; retries = 0
  token = wx.getStorageSync('token') || ''
  emit({ type: 'account', authenticated: !!token })
  // 仍留在聊天页面时换号，也需要重新建立该账号唯一的连接。
  if (token && listeners.size && foreground && network) Promise.resolve().then(() => { startPoll(); connect() })
}
function account() {
  const currentToken = wx.getStorageSync('token') || ''
  if (token !== currentToken) resetAccount()
  return currentToken
}
async function call(path, method, body) {
  const t = account(), s = session
  if (!t) throw { detail: '请先登录', unauthorized: true }
  let timer
  try {
    // HTTP 超时不等于服务端未写入，页面重试复用原 packet.client_id。
    const result = await Promise.race([
      api.chatRequest(path, method, body),
      new Promise((_, reject) => { timer = setTimeout(() => reject({ detail: '网络响应超时，请重试', code: 'CHAT_REQUEST_TIMEOUT', timeout: true }), 15000) })
    ])
    if (!current(s, t)) { account(); throw changed() }
    return result
  } catch (err) {
    if (!current(s, t)) { account(); throw changed() }
    if (err.statusCode === 404 && err.detail === 'Not Found') throw { detail: '聊天暂未开放', statusCode: 503, code: 'CHAT_UNAVAILABLE' }
    throw err
  } finally { clearTimeout(timer) }
}
async function ensure() {
  const t = account(), s = session
  if (!t) throw { detail: '请先登录', unauthorized: true }
  if (config && Date.now() - configTime < 30000) {
    if (!config.enabled) throw { detail: '聊天暂未开放' }
    return config
  }
  if (ensuring) return ensuring
  const operation = (async () => {
    const result = await call('/config')
    if (!current(s, t)) throw changed()
    config = result; configTime = Date.now(); store.bind(result.self_id)
    if (!result.enabled) { store.clear(); throw { detail: '聊天暂未开放' } }
    return result
  })()
  ensuring = operation
  try { return await operation } finally { if (ensuring === operation) ensuring = null }
}
function lifecycle() {
  if (lifecycleRegistered) return
  lifecycleRegistered = true
  if (wx.onAppHide) wx.onAppHide(() => { foreground = false; close() })
  if (wx.onAppShow) wx.onAppShow(() => {
    foreground = true; account(); configTime = 0
    if (listeners.size) { emit({ type: 'tick' }); startPoll(); connect() }
  })
  if (wx.onNetworkStatusChange) wx.onNetworkStatusChange(event => {
    network = !!event.isConnected
    if (!network) { close(); status = 'offline'; emit({ type: 'status', status }) }
    else if (foreground && listeners.size) { retries = 0; emit({ type: 'tick' }); startPoll(); connect() }
  })
}
function startPoll() {
  if (poll || !foreground || !network || !listeners.size || !account()) return
  poll = setInterval(() => {
    if (!foreground || !network || !listeners.size || !account()) return
    // 在线以实时事件驱动；前台掉线时才低频查询。
    if (status !== 'online') { emit({ type: 'tick' }); if (!socket && !opening && !reconnect) connect() }
  }, 10000)
}
function schedule(g, t) {
  if (!alive(g, t) || reconnect) return
  const delay = Math.min(30000, 1000 * Math.pow(2, Math.min(retries++, 5))) + Math.random() * 700
  reconnect = setTimeout(() => { reconnect = null; connect() }, delay)
}
function disconnect(task, g, t) {
  if (!alive(g, t) || socket !== task) return
  socket = null; clearInterval(heartbeat); clearTimeout(handshake); heartbeat = handshake = null
  status = 'offline'; emit({ type: 'status', status })
  try { task.close({}) } catch (_) {}
  schedule(g, t)
}
function connect() {
  if (!account() || !listeners.size || !foreground || !network || socket || opening) return
  startPoll()
  const g = generation, t = token
  status = 'connecting'; emit({ type: 'status', status })
  const operation = (async () => {
    await ensure()
    if (!alive(g, t)) return
    if (typeof wx.connectSocket !== 'function') { status = 'offline'; emit({ type: 'status', status }); return }
    const credentials = await call('/socket-ticket', 'POST')
    if (!alive(g, t)) return
    const task = wx.connectSocket({ url: api.BASE.replace(/^https:/, 'wss:').replace(/^http:/, 'ws:') + '/api/chat/ws' })
    socket = task
    handshake = setTimeout(() => disconnect(task, g, t), 12000)
    task.onOpen(() => {
      if (!alive(g, t) || socket !== task) return
      try { task.send({ data: JSON.stringify({ ticket: credentials.ticket }), fail: () => disconnect(task, g, t) }) }
      catch (_) { disconnect(task, g, t) }
    })
    task.onMessage(event => {
      if (!alive(g, t) || socket !== task) return
      let msg; try { msg = JSON.parse(event.data) } catch (_) { return }
      if (msg.type === 'ready') {
        clearTimeout(handshake); handshake = null
        retries = 0; status = 'online'; lastPong = Date.now(); emit({ type: 'status', status }); emit({ type: 'ready' })
        clearInterval(heartbeat)
        heartbeat = setInterval(() => {
          account()
          if (!alive(g, t) || socket !== task) return
          if (Date.now() - lastPong > 60000) disconnect(task, g, t)
          else {
            try { task.send({ data: 'ping', fail: () => disconnect(task, g, t) }) }
            catch (_) { disconnect(task, g, t) }
          }
        }, 25000)
      } else if (msg.type === 'pong') lastPong = Date.now()
      else if (msg.type === 'dirty' && status === 'online') emit(msg)
    })
    task.onClose(() => disconnect(task, g, t))
    task.onError(() => disconnect(task, g, t))
  })()
  opening = operation
  operation.catch(err => {
    if (!alive(g, t)) return
    status = 'offline'; emit({ type: 'status', status, detail: err.detail }); schedule(g, t)
  }).finally(() => { if (opening === operation) opening = null })
}
function subscribe(fn) {
  lifecycle(); account(); listeners.add(fn)
  try { fn({ type: 'status', status }) } catch (_) {}
  startPoll(); connect()
  return () => { listeners.delete(fn); if (!listeners.size) close() }
}
async function contact(target, source = 'user', card = false, isCurrent = () => true) {
  if (!isCurrent()) return
  if (!account()) { wx.navigateTo({ url: '/pages/login/login' }); return }
  const t = token, s = session
  const usable = () => current(s, t) && isCurrent()
  try {
    await ensure()
    if (!usable()) return
    const room = await call('/conversations', 'POST', { target_id: Number(target), source })
    if (!usable()) return
    wx.navigateTo({ url: '/pages/chat/chat?id=' + room.id + (card && source !== 'user' ? '&card=' + source + '&source=' + target : '') })
  } catch (err) { if (usable()) wx.showToast({ title: err.detail || '暂时无法开始聊天', icon: 'none' }) }
}
function packet(content, kind = 'text', sourceId = null) {
  // 同一发送意图始终使用同一不可变 packet，不在重试时生成新 ID。
  return Object.freeze({ client_id: 'c_' + Date.now().toString(36) + '_' + Math.random().toString(36).slice(2) + Math.random().toString(36).slice(2),
    client_created_at: new Date().toISOString(), kind, content, source_id: sourceId })
}
module.exports = { call, ensure, subscribe, contact, packet, account, resetAccount, store }
