const chat = require('../../utils/chat-client.js')
const api = require('../../utils/api.js')
function decorate(room) {
  const peer = room.peer || { nickname: '账号暂不可用' }
  let preview = '开始你们的交流'
  if (room.latest) preview = room.latest.kind === 'text' ? room.latest.content : '分享了' + (room.latest.kind === 'team' ? '队伍' : '对阵') + '卡片'
  return Object.assign({}, room, { peer, preview, initial: Array.from(peer.nickname || '选手')[0], avatar: peer.avatar ? (/^https?:/.test(peer.avatar) ? peer.avatar : api.BASE + peer.avatar) : '',
    timeText: (room.latest ? new Date(room.latest.created_at) : new Date(room.updated_at)).toLocaleString('zh-CN', { hour12: false }).slice(0, 16) })
}
function compareRooms(a, b) {
  return Number(b.pinned) - Number(a.pinned) || b.updated_at.localeCompare(a.updated_at) || chat.store.compare(b.id, a.id)
}
Page({
  data: { rooms: [], people: [], loading: true, error: '', keyword: '', searching: false, searched: false, logged: false, isAdmin: false, more: false, cursor: null },
  onShow() {
    this.stop(); this._visible = true
    if (this._token !== (wx.getStorageSync('token') || '')) this.resetView()
    this._off = chat.subscribe(e => {
      if (e.type === 'account') { this.resetView(); if (this._visible) this.load() }
      else if (['ready', 'dirty', 'tick'].includes(e.type)) this.load()
    })
    this.load()
  },
  onHide() { this.stop() }, onUnload() { this.stop() },
  stop() {
    this._visible = false; this._generation = (this._generation || 0) + 1
    if (this._searchRequest) {
      this._lastSearch = null
      this.setData({ searching: false, searched: false, people: [] })
    }
    this._listRequest = null; this._listAgain = false; this._listResetAgain = false; this._searchRequest = null; this._contactRequest = null
    if (this._off) this._off(); this._off = null
  },
  resetView() {
    this._generation = (this._generation || 0) + 1
    this._token = wx.getStorageSync('token') || ''
    this._listRequest = null; this._listAgain = false; this._listResetAgain = false; this._searchRequest = null; this._lastSearch = null; this._contactRequest = null
    this._windowEnd = null; this._loadedPages = 0; this._scrollTop = 0; this._scrollVersion = (this._scrollVersion || 0) + 1
    this.setData({ rooms: [], people: [], more: false, cursor: null, searched: false, searching: false, keyword: '', error: '',
      isAdmin: false, logged: !!this._token, loading: !!this._token })
  },
  current(g, token) { return this._visible && g === this._generation && token === this._token && token === (wx.getStorageSync('token') || '') },
  onPageScroll(e) { this._scrollTop = e.scrollTop; this._scrollVersion = (this._scrollVersion || 0) + 1 },
  measureRooms() {
    return new Promise(resolve => {
      try { wx.createSelectorQuery().in(this).selectAll('.conversation').fields({ rect: true, dataset: true }, nodes => resolve(nodes || [])).exec() }
      catch (_) { resolve([]) }
    })
  },
  async applyRooms(update, g, token, op, preserveScroll) {
    const usable = () => this.current(g, token) && this._listRequest === op
    const shifted = this.data.rooms.map(room => String(room.id)).join(',') !== update.rooms.map(room => String(room.id)).join(',')
    let anchor = null, version = this._scrollVersion
    if (preserveScroll && shifted && this._scrollTop > 0 && wx.createSelectorQuery && wx.pageScrollTo) {
      const ids = new Set(update.rooms.map(room => String(room.id))), nodes = await this.measureRooms()
      // 优先保留仍存在的可见会话；请求期间的主动滚动以用户位置为准。
      anchor = nodes.find(node => node.bottom > 0 && node.dataset && ids.has(String(node.dataset.id)))
    }
    if (!usable()) return false
    if (!anchor || version !== this._scrollVersion) { this.setData(update); return true }
    await new Promise(resolve => this.setData(update, resolve))
    const nodes = await this.measureRooms(), node = nodes.find(item => item.dataset && String(item.dataset.id) === String(anchor.dataset.id))
    if (usable() && node && version === this._scrollVersion) {
      const scrollTop = Math.max(0, this._scrollTop + node.top - anchor.top)
      if (scrollTop !== this._scrollTop) {
        try { wx.pageScrollTo({ scrollTop, duration: 0 }); this._scrollTop = scrollTop } catch (_) {}
      }
    }
    return true
  },
  async load(more = false, reset = false) {
    if (!this._visible) return
    const token = wx.getStorageSync('token') || ''
    if (this._token !== token) this.resetView()
    if (!token) { this.setData({ logged: false, loading: false }); return }
    if (this._listRequest) { if (!more) { this._listAgain = true; this._listResetAgain = this._listResetAgain || reset } return }
    const g = this._generation, op = {}, cursor = more ? this.data.cursor : null
    // 从首屏重新核实已显示窗口；最多补一页应对首屏新增，避免事件触发无界扫描。
    const boundary = !more && !reset ? this._windowEnd : null, budget = boundary ? Math.max(1, this._loadedPages) + 1 : 1
    this._listRequest = op
    try {
      const cfg = await chat.ensure()
      if (!this.current(g, token) || this._listRequest !== op) return
      const byId = new Map((more ? this.data.rooms : []).map(room => [String(room.id), room]))
      let result, next = cursor, pages = 0
      const cursors = new Set()
      while (pages < budget) {
        const key = next || ''
        if (cursors.has(key)) throw { detail: '会话分页异常，请下拉刷新列表' }
        cursors.add(key)
        result = await chat.call('/conversations' + (next ? '?cursor=' + encodeURIComponent(next) : ''))
        if (!this.current(g, token) || this._listRequest !== op) return
        pages++
        result.items.map(decorate).forEach(room => byId.set(String(room.id), room))
        const tail = result.items[result.items.length - 1]
        if (result.has_more && (!result.next_cursor || !tail)) throw { detail: '会话分页异常，请下拉刷新列表' }
        if (!result.has_more || !boundary || (tail && compareRooms(tail, boundary) >= 0)) break
        if (pages === budget) throw { detail: '会话变化较多，请下拉刷新列表' }
        next = result.next_cursor
      }
      const rooms = Array.from(byId.values()).sort(compareRooms)
      // 覆盖已核实的整个窗口，移除不再返回的会话；失败时不写半成品或新游标。
      const applied = await this.applyRooms({ rooms, cursor: result.next_cursor, more: result.has_more, isAdmin: cfg.is_admin, error: '' }, g, token, op, !reset)
      if (!applied || !this.current(g, token) || this._listRequest !== op) return
      this._loadedPages = more ? this._loadedPages + pages : pages
      const tail = rooms[rooms.length - 1]
      this._windowEnd = tail ? { id: tail.id, pinned: tail.pinned, updated_at: tail.updated_at } : null
    } catch (err) {
      if (this.current(g, token) && this._listRequest === op && !err.stale) this.setData({ error: err.detail || '聊天加载失败，请重试' })
    } finally {
      if (this._listRequest === op) {
        this._listRequest = null
        if (this.current(g, token)) {
          this.setData({ loading: false })
          const again = this._listAgain, resetAgain = this._listResetAgain; this._listAgain = false; this._listResetAgain = false
          if (again) this.load(false, resetAgain)
        }
      }
    }
  },
  retry() { return this.load() }, onPullDownRefresh() { return this.load(false, true).finally(() => wx.stopPullDownRefresh()) },
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
      if (usable()) {
        this._lastSearch = key
        this.setData({ people: result.items.map(person => Object.assign({}, person, {
          initial: Array.from(person.nickname || person.game_id || '选手')[0],
          avatarFull: person.avatar ? (/^https?:\/\//.test(person.avatar) ? person.avatar : api.BASE + person.avatar) : ''
        })) })
      }
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
