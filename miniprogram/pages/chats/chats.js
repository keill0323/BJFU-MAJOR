const chat = require('../../utils/chat-client.js')
const api = require('../../utils/api.js')
function decorate(room) {
  const peer = room.peer || { nickname: '账号暂不可用' }
  let preview = '开始你们的交流'
  if (room.latest) preview = room.latest.kind === 'text' ? room.latest.content : '分享了' + (room.latest.kind === 'team' ? '队伍' : '对阵') + '卡片'
  return Object.assign({}, room, { peer, preview, initial: Array.from(peer.nickname || '选手')[0], avatar: peer.avatar ? (/^https?:/.test(peer.avatar) ? peer.avatar : api.BASE + peer.avatar) : '',
    timeText: (room.latest ? new Date(room.latest.created_at) : new Date(room.updated_at)).toLocaleString('zh-CN', { hour12: false }).slice(0, 16) })
}
Page({
  data: { rooms: [], people: [], loading: true, error: '', keyword: '', searching: false, searched: false, logged: false, isAdmin: false, more: false, cursor: null },
  onShow() {
    this.stop(); this._visible = true
    this.resetView()
    this._off = chat.subscribe(e => {
      if (e.type === 'account') { this.resetView(); if (this._visible) this.load() }
      else if (['ready', 'dirty', 'tick'].includes(e.type)) this.load()
    })
    this.load()
  },
  onHide() { this.stop() }, onUnload() { this.stop() },
  stop() {
    this._visible = false; this._generation = (this._generation || 0) + 1
    this._listRequest = null; this._listAgain = false; this._searchRequest = null; this._contactRequest = null
    if (this._off) this._off(); this._off = null
  },
  resetView() {
    this._generation = (this._generation || 0) + 1
    this._token = wx.getStorageSync('token') || ''
    this._listRequest = null; this._listAgain = false; this._searchRequest = null; this._lastSearch = null; this._contactRequest = null
    this.setData({ rooms: [], people: [], more: false, cursor: null, searched: false, searching: false, keyword: '', error: '',
      isAdmin: false, logged: !!this._token, loading: !!this._token })
  },
  current(g, token) { return this._visible && g === this._generation && token === this._token && token === (wx.getStorageSync('token') || '') },
  async load(more = false) {
    if (!this._visible) return
    const token = wx.getStorageSync('token') || ''
    if (this._token !== token) this.resetView()
    if (!token) { this.setData({ logged: false, loading: false }); return }
    if (this._listRequest) { if (!more) this._listAgain = true; return }
    const g = this._generation, op = {}, cursor = more ? this.data.cursor : null
    this._listRequest = op
    try {
      const cfg = await chat.ensure()
      if (!this.current(g, token) || this._listRequest !== op) return
      const result = await chat.call('/conversations' + (cursor ? '?cursor=' + encodeURIComponent(cursor) : ''))
      if (!this.current(g, token) || this._listRequest !== op) return
      const byId = new Map((more ? this.data.rooms : []).map(room => [String(room.id), room]))
      result.items.map(decorate).forEach(room => byId.set(String(room.id), room))
      this.setData({ rooms: Array.from(byId.values()).sort((a, b) => Number(b.pinned) - Number(a.pinned)
          || b.updated_at.localeCompare(a.updated_at) || chat.store.compare(b.id, a.id)),
        cursor: result.next_cursor, more: result.has_more, isAdmin: cfg.is_admin, error: '' })
    } catch (err) {
      if (this.current(g, token) && this._listRequest === op && !err.stale) this.setData({ error: err.detail || '聊天加载失败，请重试' })
    } finally {
      if (this._listRequest === op) {
        this._listRequest = null
        if (this.current(g, token)) {
          this.setData({ loading: false })
          const again = this._listAgain; this._listAgain = false
          if (again) this.load()
        }
      }
    }
  },
  retry() { return this.load() }, onPullDownRefresh() { return this.load().finally(() => wx.stopPullDownRefresh()) },
  onReachBottom() { if (this.data.more && !this._listRequest) return this.load(true) },
  open(e) {
    if (this._visible && this._token && this._token === wx.getStorageSync('token')) wx.navigateTo({ url: '/pages/chat/chat?id=' + encodeURIComponent(e.currentTarget.dataset.id) })
  },
  input(e) {
    this._searchRequest = null; this._lastSearch = null
    this.setData({ keyword: e.detail.value, people: [], searched: false, searching: false })
  },
  async search(e) {
    if (!this._visible) return
    const admins = !!(e && e.currentTarget && e.currentTarget.dataset.admins), keyword = this.data.keyword.trim()
    const token = wx.getStorageSync('token') || '', g = this._generation, key = admins ? 'admins' : 'users:' + keyword
    if (!token) return
    if (this._searchRequest && this._searchRequest.key === key) return
    if (!this.data.searching && this.data.searched && this._lastSearch === key) return
    const op = { key }; this._searchRequest = op
    const usable = () => this.current(g, token) && this._searchRequest === op
    this.setData({ searching: true, searched: true, people: [] })
    try {
      await chat.ensure()
      if (!usable()) return
      const result = await chat.call('/contacts?' + (admins ? 'admins=true' : 'keyword=' + encodeURIComponent(keyword)))
      if (usable()) { this._lastSearch = key; this.setData({ people: result.items }) }
    } catch (err) { if (usable() && !err.stale) wx.showToast({ title: err.detail || '查找失败', icon: 'none' }) }
    finally { if (usable()) { this._searchRequest = null; this.setData({ searching: false }) } }
  },
  async contact(e) {
    if (this._contactRequest || !this._visible) return
    const g = this._generation, token = this._token, op = {}; this._contactRequest = op
    try { await chat.contact(e.currentTarget.dataset.id, 'user', false, () => this.current(g, token) && this._contactRequest === op) }
    finally { if (this._contactRequest === op) this._contactRequest = null }
  },
  closeSearch() { this._searchRequest = null; this._lastSearch = null; this.setData({ searched: false, searching: false, people: [] }) },
  login() { wx.navigateTo({ url: '/pages/login/login' }) },
  manage() { if (this.data.isAdmin && this._visible) wx.navigateTo({ url: '/pages/admin/chat/chat' }) }
})
