const api = require('../../../utils/api.js')

function errorText(err, fallback) { return err && err.detail || fallback }
function displayMessage(message) {
  if (!message) return '原消息已删除，无法查看正文'
  if (message.state !== 'active') return { hidden: '该消息已隐藏', purged: '该消息已按保留规则删除', recalled: '该消息已撤回' }[message.state] || '原消息已不可见'
  if (message.kind === 'text') return message.content || '空消息'
  let card = {}
  try { card = JSON.parse(message.content || '{}') } catch (_) {}
  return (message.kind === 'round' ? '对阵卡片' : '队伍卡片') + (card.title || card.name ? ' · ' + (card.title || card.name) : '')
}

Page({
  data: {
    allowed: false, checking: true, error: '', reports: [], pending: 0,
    loading: false, listError: '', cursor: null, more: false,
    reasons: {}, processing: null, actionError: '',
    sanctionUser: '', sanctionReason: '', sanctionIndex: 2, sanctionBusy: false, sanctionMessage: '',
    sanctionOptions: [{ hours: 1, label: '禁言 1 小时' }, { hours: 6, label: '禁言 6 小时' },
      { hours: 24, label: '禁言 1 天' }, { hours: 72, label: '禁言 3 天' },
      { hours: 168, label: '禁言 7 天' }, { hours: 720, label: '禁言 30 天' }, { hours: 0, label: '解除禁言' }]
  },
  onShow() { return this.initialize() },
  onHide() { this.stop() },
  onUnload() { this.stop() },
  stop() {
    this._visible = false
    this._generation = (this._generation || 0) + 1
    this._loadId = (this._loadId || 0) + 1
    this._action = null
    this.setData({ allowed: false, reports: [], reasons: {}, pending: 0, loading: false,
      processing: null, sanctionBusy: false, sanctionUser: '', sanctionReason: '', sanctionMessage: '' })
  },
  current(generation, token) {
    return this._visible && generation === this._generation && token === wx.getStorageSync('token')
  },
  async initialize() {
    this._visible = true
    const generation = this._generation = (this._generation || 0) + 1
    const token = this._token = wx.getStorageSync('token')
    this._action = null
    this.setData({ allowed: false, checking: true, error: '', reports: [], reasons: {},
      cursor: null, more: false, pending: 0, loading: false, processing: null, sanctionBusy: false,
      sanctionUser: '', sanctionReason: '', sanctionMessage: '', actionError: '' })
    try {
      if (!token) throw { detail: '请先使用管理员账号登录' }
      const user = await api.getMe()
      if (!this.current(generation, token)) return
      if (!user || user.role !== 'admin') throw { detail: '仅管理员可处理聊天举报和禁言' }
      this.setData({ allowed: true, checking: false })
      await this.loadReports()
    } catch (err) {
      if (this.current(generation, token)) this.setData({ checking: false, error: errorText(err, '权限检查失败，请重试') })
    }
  },
  async loadReports(more = false) {
    // bindtap 传入的是事件，只有显式 true 才是翻页。
    more = more === true
    if (!this.data.allowed || this.data.loading || (more && !this.data.more)) return
    const generation = this._generation, token = this._token
    const request = this._loadId = (this._loadId || 0) + 1
    const before = more ? this.data.cursor : null
    this.setData({ loading: true, listError: '' })
    try {
      const result = await require('../../../utils/chat-client.js').call('/admin/reports' + (before ? '?before_id=' + before : ''))
      if (!this.current(generation, token) || request !== this._loadId) return
      const rows = result.items.map(row => Object.assign({}, row, {
        contentText: displayMessage(row.message),
        statusText: { pending: '待处理', dismiss: '已驳回', hide: '已隐藏' }[row.status] || '已处理',
        timeText: String(row.created_at || '').replace('T', ' ').slice(0, 16)
      }))
      const old = more ? this.data.reports : []
      this.setData({ reports: old.concat(rows.filter(row => !old.some(item => item.id === row.id))),
        pending: result.pending || 0, cursor: result.next_cursor, more: !!result.has_more })
    } catch (err) {
      if (this.current(generation, token) && request === this._loadId) this.setData({ listError: errorText(err, '举报列表加载失败，请重试') })
    } finally {
      if (this.current(generation, token) && request === this._loadId) this.setData({ loading: false })
    }
  },
  loadMore() { if (!this._action) return this.loadReports(true) },
  onReason(e) {
    if (this._action) return
    const id = Number(e.currentTarget.dataset.id)
    if (this.data.reports.some(row => row.id === id)) this.setData({ ['reasons.' + id]: e.detail.value, actionError: '' })
  },
  async resolve(e) {
    if (!this.data.allowed || this._action || !this.current(this._generation, this._token)) return
    const id = Number(e.currentTarget.dataset.id), action = e.currentTarget.dataset.action
    const row = this.data.reports.find(item => item.id === id)
    const reason = (this.data.reasons[id] || '').trim()
    if (!row || row.status !== 'pending' || !['dismiss', 'hide'].includes(action)) return
    if (!reason) { this.setData({ actionError: '请填写这条举报的处理原因' }); return }
    const generation = this._generation, token = this._token, attempt = this._action = {}
    this.setData({ processing: id, actionError: '' })
    try {
      const confirmed = await new Promise(resolve => wx.showModal({ title: action === 'hide' ? '隐藏举报消息' : '驳回举报',
        content: (action === 'hide' ? '双方将无法继续查看此消息正文。' : '原消息继续保留。') + '\n处理原因：' + reason,
        confirmText: '确认处理', success: result => resolve(!!result.confirm), fail: () => resolve(false) }))
      if (!confirmed || !this.current(generation, token)) return
      await require('../../../utils/chat-client.js').call('/admin/reports/' + id + '/resolve', 'POST', { action, reason })
      if (!this.current(generation, token)) return
      wx.showToast({ title: '举报已处理', icon: 'success' })
      await this.loadReports()
    } catch (err) {
      if (this.current(generation, token)) this.setData({ actionError: errorText(err, '处理失败，请刷新列表后重试') })
    } finally {
      if (this._action === attempt) this._action = null
      if (this.current(generation, token)) this.setData({ processing: null })
    }
  },
  targetSender(e) {
    if (!this.data.allowed || this._action) return
    const id = Number(e.currentTarget.dataset.id)
    if (!Number.isSafeInteger(id) || id <= 0) return
    this.setData({ sanctionUser: String(id), sanctionMessage: '' })
    wx.pageScrollTo({ selector: '#sanctions', duration: 200 })
  },
  onSanctionUser(e) { if (!this._action) this.setData({ sanctionUser: e.detail.value, sanctionMessage: '' }) },
  onSanctionReason(e) { if (!this._action) this.setData({ sanctionReason: e.detail.value, sanctionMessage: '' }) },
  onSanctionIndex(e) {
    const index = Number(e.detail.value)
    if (!this._action && Number.isInteger(index) && this.data.sanctionOptions[index]) this.setData({ sanctionIndex: index, sanctionMessage: '' })
  },
  async sanction() {
    if (!this.data.allowed || this._action || !this.current(this._generation, this._token)) return
    const userId = Number(this.data.sanctionUser), option = this.data.sanctionOptions[this.data.sanctionIndex]
    const reason = this.data.sanctionReason.trim()
    if (!Number.isSafeInteger(userId) || userId <= 0 || !reason || !option) {
      this.setData({ sanctionMessage: '请填写有效的用户 ID 和处理原因' }); return
    }
    const generation = this._generation, token = this._token, attempt = this._action = {}
    this.setData({ sanctionBusy: true, sanctionMessage: '' })
    try {
      const confirmed = await new Promise(resolve => wx.showModal({ title: option.label,
        content: '操作对象：用户 ID ' + userId + '\n处理原因：' + reason,
        confirmText: '确认设置', success: result => resolve(!!result.confirm), fail: () => resolve(false) }))
      if (!confirmed || !this.current(generation, token)) return
      const result = await require('../../../utils/chat-client.js').call('/admin/sanctions', 'POST', { user_id: userId, hours: option.hours, reason })
      if (this.current(generation, token)) this.setData({ sanctionMessage: result.message || '禁言设置已更新' })
    } catch (err) {
      if (this.current(generation, token)) this.setData({ sanctionMessage: errorText(err, '设置失败，请重试') })
    } finally {
      if (this._action === attempt) this._action = null
      if (this.current(generation, token)) this.setData({ sanctionBusy: false })
    }
  }
})
