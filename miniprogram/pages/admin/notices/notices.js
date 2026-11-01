const api = require('../../../utils/api.js')

Page({
  data: {
    allowed: false, checking: true, error: '', activeTab: 'compose', recipientsExpanded: false, selectedSummary: '', keyword: '', searchedKeyword: '',
    users: [], selected: [], selectedCount: 0, searching: false, searchError: '', cursor: null, hasMore: false,
    title: '', content: '', sending: false, sendError: '', sentMessage: '', history: [], historyError: '', historyLoading: false, expandedHistoryId: null
  },
  onShow() { return this.initialize() },
  onUnload() { this._gone = true; this._generation = (this._generation || 0) + 1; this._searchGeneration = (this._searchGeneration || 0) + 1 },
  current(generation, token) { return !this._gone && generation === this._generation && token === wx.getStorageSync('token') },
  async initialize() {
    if (this._sending) return
    const token = wx.getStorageSync('token')
    const generation = this._generation = (this._generation || 0) + 1
    if (this._token !== token) {
      this._attempt = null
      this.setData({ users: [], selected: [], selectedCount: 0, selectedSummary: '', recipientsExpanded: false, activeTab: 'compose', title: '', content: '', history: [], expandedHistoryId: null, sentMessage: '' })
    }
    this._token = token
    this.setData({ allowed: false, checking: true, error: '' })
    try {
      if (!token) throw { detail: '请先使用管理员账号登录' }
      const user = await api.getMe()
      if (!this.current(generation, token)) return
      if (!user || user.role !== 'admin') throw { detail: '仅管理员可发送定向通知' }
      this.setData({ allowed: true, checking: false })
      await Promise.all([this.search(), this.loadHistory()])
    } catch (err) {
      if (this.current(generation, token)) this.setData({ checking: false, error: err.detail || '权限检查失败，请重试' })
    }
  },
  onKeyword(e) { if (!this.data.sending) this.setData({ keyword: e.detail.value }) },
  onTitle(e) { if (!this.data.sending) this.setData({ title: e.detail.value, sendError: '', sentMessage: '' }) },
  onContent(e) { if (!this.data.sending) this.setData({ content: e.detail.value, sendError: '', sentMessage: '' }) },
  switchTab(e) {
    const tab = e.currentTarget.dataset.tab
    if (!this.data.allowed || this.data.sending || !['compose', 'history'].includes(tab)) return
    this.setData({ activeTab: tab })
  },
  toggleRecipients() {
    if (this.data.allowed && !this.data.sending) this.setData({ recipientsExpanded: !this.data.recipientsExpanded })
  },
  toggleHistory(e) {
    if (!this.data.allowed) return
    const id = Number(e.currentTarget.dataset.id)
    if (!this.data.history.some(item => item.id === id)) return
    this.setData({ expandedHistoryId: this.data.expandedHistoryId === id ? null : id })
  },
  goManagement() {
    if (this.data.sending) return
    const pages = getCurrentPages()
    const index = pages.findIndex(page => page.route === 'pages/admin/admin')
    const fallback = () => wx.redirectTo({ url: '/pages/admin/admin' })
    if (index >= 0 && index < pages.length - 1) wx.navigateBack({ delta: pages.length - 1 - index, fail: fallback })
    else fallback()
  },
  decorate(rows) {
    const selected = new Set(this.data.selected.map(u => u.id))
    return rows.map(u => ({ id: u.id, nickname: u.nickname || '玩家' + u.id, game_id: u.game_id || '',
      is_verified: !!u.is_verified, selected: selected.has(u.id) }))
  },
  async search() { return this.loadUsers(false) },
  async loadMore() { if (!this.data.searching && this.data.hasMore) return this.loadUsers(true) },
  async loadUsers(more) {
    if (!this.data.allowed || this.data.sending) return
    const generation = this._generation, token = this._token
    const search = this._searchGeneration = (this._searchGeneration || 0) + 1
    const keyword = more ? this.data.searchedKeyword : this.data.keyword.trim()
    const cursor = more ? this.data.cursor : null
    if (!more) this.setData({ users: [], cursor: null, hasMore: false, searchedKeyword: keyword })
    this.setData({ searching: true, searchError: '' })
    try {
      const page = await api.getAdminNoticeRecipients(keyword, cursor)
      if (!this.current(generation, token) || search !== this._searchGeneration) return
      const rows = more ? this.data.users.concat(page.items.filter(u => !this.data.users.some(old => old.id === u.id))) : page.items
      this.setData({ users: this.decorate(rows), cursor: page.next_cursor, hasMore: page.has_more })
    } catch (err) {
      if (this.current(generation, token) && search === this._searchGeneration) this.setData({ searchError: err.detail || '人员列表加载失败，请重试' })
    } finally {
      if (this.current(generation, token) && search === this._searchGeneration) this.setData({ searching: false })
    }
  },
  toggleRecipient(e) {
    if (!this.data.allowed || this.data.sending) return
    const id = Number(e.currentTarget.dataset.id)
    let selected = this.data.selected
    if (selected.some(u => u.id === id)) selected = selected.filter(u => u.id !== id)
    else {
      const user = this.data.users.find(u => u.id === id)
      if (!user) return
      if (selected.length >= 100) { wx.showToast({ title: '每次最多选择 100 人', icon: 'none' }); return }
      selected = selected.concat({ id: user.id, nickname: user.nickname })
    }
    const selectedSummary = selected.slice(0, 3).map(user => user.nickname).join('、') + (selected.length > 3 ? '等 ' + selected.length + ' 人' : '')
    this.setData({ selected, selectedCount: selected.length, selectedSummary, sendError: '', sentMessage: '' })
    this.setData({ users: this.decorate(this.data.users) })
  },
  async loadHistory() {
    if (!this.data.allowed) return
    const generation = this._generation, token = this._token
    const request = this._historyRequest = (this._historyRequest || 0) + 1
    this.setData({ historyLoading: true, historyError: '' })
    try {
      const result = await api.getAdminNoticeHistory()
      if (this.current(generation, token) && request === this._historyRequest) this.setData({ history: result.items.map(n => Object.assign({}, n,
        { timeText: String(n.created_at || '').replace('T', ' ').slice(0, 16) })), historyError: '',
        expandedHistoryId: result.items.some(item => item.id === this.data.expandedHistoryId) ? this.data.expandedHistoryId : null })
    } catch (err) {
      if (this.current(generation, token) && request === this._historyRequest) this.setData({ historyError: err.detail || '发送记录加载失败，请重试' })
    } finally {
      if (this.current(generation, token) && request === this._historyRequest) this.setData({ historyLoading: false })
    }
  },
  async sendNotice() {
    if (!this.data.allowed || this._sending || !this.current(this._generation, this._token)) return
    const title = this.data.title.trim(), content = this.data.content.trim()
    const ids = this.data.selected.map(u => u.id).sort((a, b) => a - b)
    if (!ids.length || !title || !content) {
      this.setData({ sendError: '请选择收件人，并填写通知标题和正文' }); return
    }
    const generation = this._generation, token = this._token
    const fingerprint = JSON.stringify([ids, title, content])
    if (!this._attempt || this._attempt.fingerprint !== fingerprint) {
      this._attempt = { fingerprint, requestId: 'notice_' + Date.now().toString(36) + '_' + Math.random().toString(36).slice(2, 14) }
    }
    this._sending = true
    this.setData({ sending: true, sendError: '', sentMessage: '' })
    try {
      const confirmed = await new Promise(resolve => wx.showModal({
        title: '确认发送给 ' + ids.length + ' 人',
        content: '收件人：' + this.data.selected.slice(0, 5).map(u => u.nickname + '（ID ' + u.id + '）').join('、') + (ids.length > 5 ? '等 ' + ids.length + ' 人，完整名单见页面已选人员' : '') + '\n\n标题：' + title + '\n\n' + content,
        confirmText: '发送通知', success: res => resolve(!!res.confirm), fail: () => resolve(false)
      }))
      if (!confirmed || !this.current(generation, token)) return
      const result = await api.sendAdminNotice({ request_id: this._attempt.requestId, recipient_ids: ids, title, content })
      if (!this.current(generation, token)) return
      this._attempt = null
      this.setData({ selected: [], selectedCount: 0, selectedSummary: '', recipientsExpanded: false, title: '', content: '', sentMessage: '通知已送入 ' + result.recipient_count + ' 位用户的站内收件箱' })
      this.setData({ users: this.decorate(this.data.users) })
      await this.loadHistory()
    } catch (err) {
      if (this.current(generation, token)) this.setData({ sendError: err.detail || '未能确认发送结果。可先查看发送记录，再重试；相同内容重试不会重复发送。' })
    } finally {
      this._sending = false
      if (this.current(generation, token)) this.setData({ sending: false })
    }
  }
})
