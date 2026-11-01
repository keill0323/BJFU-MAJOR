// One user gesture creates one subscription attempt, never a local sending-credit counter.
const api = require('./api.js')
const SCENES = { personal_registration: 'team_invitation', captain_recruitment: 'team_application', all: null }
let token = null, generation = 0, cached = null, pending = null, active = null

function resetAccount() { token = wx.getStorageSync('token') || ''; generation++; cached = null; pending = null; active = null }
function account() { const current = wx.getStorageSync('token') || ''; if (current !== token) resetAccount(); return token }
function current(g, t) { return generation === g && t === token && t === (wx.getStorageSync('token') || '') }
function snapshot() {
  const t = account()
  if (!t || !cached) return { loggedIn: !!t, ready: false, configured: false, failed: false, stateFailed: false,
    templates: [], label: '', needsResubscribe: false, deliveryUnknown: false, deliveryFailed: false }
  const rows = cached.templates
  return { loggedIn: true, ready: true, configured: cached.enabled && rows.length > 0, failed: cached.failed,
    stateFailed: cached.stateFailed, templates: rows.map(row => Object.assign({}, row)), label: [...new Set(rows.map(row => row.label))].join('、'),
    needsResubscribe: rows.some(row => row.needs_resubscribe === true),
    deliveryUnknown: rows.some(row => row.last_delivery_status === 'unknown'),
    deliveryFailed: rows.some(row => row.last_delivery_status === 'failed') }
}
function prefetch() {
  const t = account(), g = generation
  if (!t) return Promise.resolve(snapshot())
  if (pending) return pending
  const task = Promise.allSettled([api.getWechatConfig(), typeof api.getWechatSubscriptionState === 'function' ? api.getWechatSubscriptionState() : Promise.reject(Error('legacy state unavailable'))])
    .then(results => {
      if (!current(g, t)) return snapshot()
      if (results[0].status !== 'fulfilled') { cached = { enabled: false, templates: [], failed: true, stateFailed: true }; return snapshot() }
      const config = results[0].value || {}, state = results[1].status === 'fulfilled' ? results[1].value || {} : {}
      const states = Array.isArray(state.templates) ? state.templates : []
      const templates = (Array.isArray(config.templates) ? config.templates : []).filter(row => row && typeof row.template_id === 'string' && row.template_id.length <= 128)
        .map(row => {
          const saved = states.find(item => item.template_id === row.template_id) || {}
          return { kind: row.kind, template_id: row.template_id, label: typeof row.label === 'string' ? row.label : '入队通知',
            enabled: saved.enabled === true, has_accepted: saved.has_accepted === true, actual_remaining: 'unknown', last_delivery_status: saved.last_delivery_status || null,
            grant_version: Number(saved.grant_version) || 0,
            successful_send_count: Number(saved.successful_send_count) || 0, needs_resubscribe: saved.needs_resubscribe === true }
        })
      cached = { enabled: config.enabled === true, templates, failed: false, stateFailed: results[1].status !== 'fulfilled' }
      return snapshot()
    }).finally(() => { if (pending === task) pending = null })
  pending = task
  return task
}
function hint(text) { if (wx.showToast) wx.showToast({ title: text, icon: 'none' }) }
function prompt(scene) {
  const t = account(), g = generation
  if (!Object.prototype.hasOwnProperty.call(SCENES, scene)) return Promise.resolve({ status: 'unsupported_scene' })
  if (!t) return Promise.resolve({ status: 'logged_out' })
  const info = snapshot()
  if (!info.ready || info.failed) { prefetch().catch(() => {}); hint('微信提醒暂未准备好，可查看站内消息'); return Promise.resolve({ status: 'not_ready' }) }
  const rows = info.templates.filter(row => !SCENES[scene] || row.kind === SCENES[scene])
  const ids = [...new Set(rows.map(row => row.template_id))].slice(0, 3)
  if (!info.configured || !ids.length) { hint('微信提醒尚未配置，可能错过通知；可查看站内消息'); return Promise.resolve({ status: 'unconfigured' }) }
  if (active) return Promise.resolve({ status: 'busy' })
  if (typeof wx.requestSubscribeMessage !== 'function') { hint('当前微信不支持订阅，可查看站内消息'); return Promise.resolve({ status: 'unsupported' }) }
  const attempt = { id: 'ws_' + Date.now().toString(36) + '_' + Math.random().toString(36).slice(2).padEnd(13, '0') + Math.random().toString(36).slice(2).padEnd(13, '0') }
  active = attempt
  return new Promise(resolve => {
    let received = false
    const finish = result => { if (active === attempt) active = null; resolve(result) }
    // No await or network read may precede this call: it must remain in the user gesture.
    try {
      wx.requestSubscribeMessage({ tmplIds: ids,
        success: async result => {
          if (received) return
          received = true
          if (!current(g, t)) { finish({ status: 'account_changed' }); return }
          const choices = {}
          ids.forEach(id => { if (['accept', 'reject', 'ban'].includes(result[id])) choices[id] = result[id] })
          if (!Object.keys(choices).length) { hint('本次未开启微信提醒，可查看站内消息'); finish({ status: 'cancelled' }); return }
          try {
            await api.saveWechatSubscriptions(choices, attempt.id)
            if (!current(g, t)) { finish({ status: 'account_changed' }); return }
            const accepted = Object.values(choices).includes('accept')
            hint(accepted ? '本次订阅已记录，可再次补充订阅' : '未开启微信提醒，可查看站内消息')
            cached = null
            finish({ status: accepted ? 'accepted' : 'declined', attempt_id: attempt.id })
            prefetch().catch(() => {})
          } catch (_) {
            if (current(g, t)) hint('订阅记录未保存，可能错过微信提醒；可查看站内消息')
            finish({ status: 'save_failed', attempt_id: attempt.id })
          }
        },
        fail: () => {
          if (received) return
          received = true
          if (current(g, t)) hint('未开启微信提醒，可查看站内消息')
          finish({ status: 'cancelled_or_failed' })
        }
      })
    } catch (_) { if (current(g, t)) hint('未能订阅微信提醒，可查看站内消息'); finish({ status: 'unsupported_or_failed' }) }
  })
}
module.exports = { prefetch, prompt, snapshot, resetAccount }
