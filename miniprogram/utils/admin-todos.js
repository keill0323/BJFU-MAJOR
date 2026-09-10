// 管理入口共用轻量待办摘要，缓存仅存内存并按登录 token 隔离。
const api = require('./api.js')
const CACHE_MS = 30000
let sessionToken = '', generation = 0, pending = null
let state = { data: null, loading: false, error: '', updatedAt: 0 }
const listeners = new Set()

function emit() {
  listeners.forEach(listener => listener(Object.assign({}, state)))
}

function syncSession() {
  const token = wx.getStorageSync('token') || ''
  if (token !== sessionToken) {
    sessionToken = token
    generation++
    pending = null
    state = { data: null, loading: false, error: '', updatedAt: 0 }
    emit()
  }
  return token
}

function getState() {
  syncSession()
  return Object.assign({}, state)
}

function subscribe(listener) {
  syncSession()
  listeners.add(listener)
  listener(Object.assign({}, state))
  return () => listeners.delete(listener)
}

function invalidate() {
  syncSession()
  // 审核成功后，正在返回的旧摘要也不能恢复旧角标。
  generation++
  pending = null
  state = Object.assign({}, state, { updatedAt: 0, loading: false })
  emit()
}

function refresh(force = false) {
  const token = syncSession()
  if (!token) return Promise.resolve(null)
  if (pending) return pending
  if (!force && state.data && state.updatedAt && Date.now() - state.updatedAt < CACHE_MS) return Promise.resolve(state.data)
  const requestGeneration = generation
  state = Object.assign({}, state, { loading: true, error: '' })
  emit()
  const isCurrent = () => syncSession() === token && generation === requestGeneration
  const request = Promise.resolve().then(() => api.getAdminTodos()).then(data => {
    if (!isCurrent()) return null
    state = { data, loading: false, error: '', updatedAt: Date.now() }
    emit()
    return data
  }).catch(err => {
    if (isCurrent()) {
      state = Object.assign({}, state, {
        data: err && (err.statusCode === 401 || err.statusCode === 403) ? null : state.data,
        loading: false, error: (err && err.detail) || '待办暂未更新，请稍后重试', updatedAt: 0
      })
      emit()
    }
    throw err
  }).finally(() => {
    if (pending === request) pending = null
  })
  pending = request
  return request
}

module.exports = { refresh, invalidate, subscribe, getState }
