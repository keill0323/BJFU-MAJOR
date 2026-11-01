const chat = require('../../utils/chat-client.js')
const MAX_ROWS = 200, MAX_PENDING = 10
function decorate(m, selfId) {
  let card = null
  if (m.kind !== 'text' && m.content) { try { card = JSON.parse(m.content) } catch (_) {} }
  return Object.assign({}, m, { mine: m.sender_id === selfId, card, elementId: 'm' + m.id.replace(/[^a-zA-Z0-9_-]/g, ''),
    statusText: m.pending ? m.failed ? '发送失败 · 点击重试' : '发送中…' : m.read_at ? '已读' : '已发送',
    timeText: new Date(m.created_at).toLocaleString('zh-CN', { hour12: false }) })
}
Page({
  data: { room: null, rows: [], draft: '', loading: true, error: '', status: 'connecting', selfId: null,
    more: false, loadingMore: false, anchor: '', newMessages: false, keyboardHeight: 0, windowHeight: 600, returnHeight: 44, canSend: false, cardOffer: '', showRules: false },
  onLoad(options) {
    this._cid = Number(options.id); this._items = []; this._pending = []; this._atBottom = true
    this._card = ['team', 'round'].includes(options.card) && Number(options.source) > 0 ? { kind: options.card, id: Number(options.source) } : null
    const info = wx.getWindowInfo ? wx.getWindowInfo() : wx.getSystemInfoSync()
    this.setData({ windowHeight: info.windowHeight, returnHeight: 36 + (info.windowWidth || 375) * 16 / 750, cardOffer: this._card ? (this._card.kind === 'round' ? '分享本场对阵' : '分享队伍') : '' })
  },
  onShow() {
    this._visible = true; this._generation = (this._generation || 0) + 1
    this._syncing = null; this._syncAgain = false; this._reading = null
    this.setData({ loadingMore: false })
    const token = wx.getStorageSync('token') || ''
    if (this._token !== token) { this._items = []; this._pending = []; this._cursor = null; this.setData({ rows: [], room: null, draft: '' }) }
    this._token = token
    this._off = chat.subscribe(e => {
      if (e.type === 'account') {
        this._generation++; this._token = wx.getStorageSync('token') || ''; this._items = []; this._pending = []; this._cursor = null
        this._syncing = this._reading = null; this._syncAgain = false
        this.setData({ rows: [], room: null, draft: '', canSend: false, loadingMore: false, error: '登录状态已变化，请重新打开聊天' })
      } else if (e.type === 'status') this.setData({ status: e.status })
      else if (e.type === 'ready' || e.type === 'tick' || (e.type === 'dirty' && e.conversation_id === this._cid)) this.sync()
    })
    this.load()
    this._readTimer = setInterval(() => this.observeRead(), 1500)
  },
  onHide() { this.stop() }, onUnload() { this.stop() },
  stop() {
    this._visible = false; this._generation = (this._generation || 0) + 1
    if (this._off) this._off(); this._off = null
    clearInterval(this._readTimer); clearTimeout(this._scrollTimer)
  },
  current(g) { return this._visible && this._generation === g && this._token === (wx.getStorageSync('token') || '') },
  async load(fresh = false) {
    const g = this._generation
    const operation = {}; this._loadOperation = operation
    this._cursor = null
    this._pending = this._pending.filter(m => Date.parse(m.created_at) > Date.now() - 86400000)
    this.setData({ loading: true, canSend: false, rows: [] })
    try {
      const cfg = await chat.ensure()
      if (!this.current(g) || this._loadOperation !== operation) return
      // 缓存必须先完成鉴权与删除/撤回同步，验证失败时不显示旧正文。
      const saved = fresh === true ? null : chat.store.load(this._cid)
      let result = null
      if (saved) {
        let items = saved.items, cursor = saved.cursor
        for (let batch = 0; batch < 10; batch++) {
          const update = await chat.call('/conversations/' + this._cid + '/events?after_seq=' + cursor)
          if (!this.current(g) || this._loadOperation !== operation) return
          if (update.reset) { chat.store.forget(this._cid); break }
          items = chat.store.merge(items, this.relevant(items, update.items), cursor); cursor = update.cursor
          if (!update.has_more) {
            const cropped = items.length > MAX_ROWS
            result = { items: items.slice(-MAX_ROWS), cursor, room: update.room,
              next_cursor: cropped ? items[items.length - MAX_ROWS].seq : saved.next_cursor, has_more: saved.has_more || cropped }
            break
          }
        }
      }
      if (!result) result = await chat.call('/conversations/' + this._cid + '/messages')
      if (!this.current(g) || this._loadOperation !== operation) return
      this._items = result.items; this._cursor = result.cursor; this._before = result.next_cursor
      this._olderWindow = false; this._atBottom = true
      this.setData({ selfId: cfg.self_id, loading: false, room: result.room, more: result.has_more, newMessages: false, error: '', canSend: this.writable(result.room) })
      this.render(true)
      this.sync()
    } catch (err) { if (this.current(g) && this._loadOperation === operation) { this._items = []; this.setData({ loading: false, error: err.detail || '聊天加载失败', rows: [], canSend: false }); chat.store.forget(this._cid) } }
  },
  relevant(items, events) {
    const ids = new Set(items.map(m => m.id)), last = items.length ? items[items.length - 1].seq : '0'
    return events.filter(e => ids.has(e.message_id) || (e.kind === 'message' && (!this._olderWindow || !e.message || chat.store.compare(e.message.seq, last) <= 0)))
  },
  writable(room) { return room.write_enabled && room.peer.available && !room.blocked && !room.muted_until },
  render(scroll) {
    if (this._items.length > MAX_ROWS) {
      this._items = this._items.slice(-MAX_ROWS); this._before = this._items[0].seq; this.setData({ more: true })
    }
    const ids = new Set(this._items.filter(m => m.sender_id === this.data.selfId).map(m => m.client_id))
    this._pending = this._pending.filter(m => !ids.has(m.client_id))
    let lastTime = null
    const rows = this._items.concat(this._pending).map(m => {
      const row = decorate(m, this.data.selfId), stamp = Date.parse(m.created_at)
      row.showTime = lastTime === null || !Number.isFinite(lastTime) || !Number.isFinite(stamp) || Math.abs(stamp - lastTime) >= 5 * 60 * 1000
      if (row.showTime) lastTime = stamp
      return row
    })
    const update = { rows }
    if (scroll && rows.length) { update.anchor = rows[rows.length - 1].elementId; update.newMessages = false }
    this.setData(update, () => this.observeRead())
    if (this._olderWindow) chat.store.forget(this._cid)
    else chat.store.save(this._cid, this._items, { cursor: this._cursor, next_cursor: this._before, has_more: this.data.more })
  },
  async sync() {
    if (!this._visible || this._cursor == null) return
    if (this._syncing) { this._syncAgain = true; return }
    const g = this._generation
    this._syncing = g
    try {
      let more = true, batches = 0
      while (more && batches++ < 10 && this.current(g)) {
        const result = await chat.call('/conversations/' + this._cid + '/events?after_seq=' + this._cursor)
        if (!this.current(g)) return
        if (result.reset) { chat.store.forget(this._cid); await this.load(); return }
        const relevant = this.relevant(this._items, result.items)
        this._items = chat.store.merge(this._items, relevant, this._cursor)
        this._cursor = result.cursor; more = result.has_more
        const hasNew = result.items.some(e => e.kind === 'message')
        this.setData({ room: result.room, canSend: this.writable(result.room), error: '', newMessages: this.data.newMessages || (!this._atBottom && hasNew) })
        if (relevant.length) this.render(this._atBottom && hasNew)
        else if (!this._olderWindow && result.items.length) chat.store.save(this._cid, this._items, { cursor: this._cursor, next_cursor: this._before, has_more: this.data.more })
      }
      if (more) this._syncAgain = true
    } catch (err) {
      if (this.current(g)) {
        if (err.unauthorized || [403, 404].includes(err.statusCode)) { this._items = []; chat.store.forget(this._cid); this.setData({ rows: [], canSend: false }) }
        this.setData({ error: err.detail || '同步暂时失败，正在重试' })
      }
    } finally {
      if (this._syncing === g) {
        this._syncing = null
        if (this._syncAgain) { this._syncAgain = false; if (this.current(g)) this.sync() }
      }
    }
  },
  async older() {
    if (!this.data.more || this.data.loadingMore) return
    const g = this._generation, first = this.data.rows[0]
    this.setData({ loadingMore: true })
    try {
      const result = await chat.call('/conversations/' + this._cid + '/messages?before_seq=' + this._before)
      if (!this.current(g)) return
      const existing = new Set(this._items.map(m => m.id))
      this._items = result.items.filter(m => !existing.has(m.id)).concat(this._items)
      if (this._items.length > MAX_ROWS) { this._items = this._items.slice(0, MAX_ROWS); this._olderWindow = true; this.setData({ newMessages: true }) }
      this._before = result.next_cursor
      this.setData({ more: result.has_more }); this.render(false)
      if (first) this.setData({ anchor: first.elementId })
      this.sync()
    } catch (err) { if (this.current(g)) wx.showToast({ title: err.detail || '加载失败', icon: 'none' }) }
    finally { if (this.current(g)) this.setData({ loadingMore: false }) }
  },
  input(e) { this.setData({ draft: e.detail.value }) },
  toggleRules() { this.setData({ showRules: !this.data.showRules }) },
  keyboard(e) { this.setData({ keyboardHeight: e.detail.height || 0 }); if (this._atBottom) this.bottom() },
  async send() {
    if (!this.data.canSend || !this.data.draft.trim()) return
    const content = this.data.draft.trim()
    if (Array.from(content).length > 1000) { wx.showToast({ title: '最多发送1000字', icon: 'none' }); return }
    if (this._pending.length >= MAX_PENDING) { wx.showToast({ title: '请先重试或长按移除发送失败的消息', icon: 'none' }); return }
    this.setData({ draft: '' })
    this.queue(chat.packet(content))
  },
  queue(packet) {
    if (this._pending.length >= MAX_PENDING) { wx.showToast({ title: '待发送消息较多，请稍后再试', icon: 'none' }); return }
    if (this._olderWindow) { this._olderWindow = false; this.load(true) }
    const pending = { id: 'p-' + packet.client_id, client_id: packet.client_id, sender_id: this.data.selfId, kind: packet.kind,
      content: packet.content, created_at: packet.client_created_at, state: 'active', pending: true, failed: false, packet }
    this._pending.push(pending); this._atBottom = true; this.render(true); this.deliver(pending)
  },
  async deliver(pending) {
    if (pending.busy) return
    const g = this._generation, token = this._token
    pending.busy = true; pending.failed = false; this.render(false)
    try {
      const result = await chat.call('/conversations/' + this._cid + '/messages', 'POST', pending.packet)
      if (!this.current(g) || token !== wx.getStorageSync('token') || !this._pending.includes(pending)) return
      this._pending = this._pending.filter(m => m.client_id !== pending.client_id)
      if (result.message.state !== 'purged' && !this._items.some(m => m.id === result.message.id)) {
        this._items.push(result.message); this._items.sort((a, b) => chat.store.compare(a.seq, b.seq))
      }
      if (this.current(g)) { this.render(this._atBottom); this.sync() }
    } catch (err) {
      pending.failed = true; pending.failure = err.detail || '发送失败'
      if (this.current(g)) { this.render(false); wx.showToast({ title: pending.failure, icon: 'none' }) }
    } finally {
      pending.busy = false
      if (!this.current(g)) pending.failed = true
    }
  },
  retry(e) {
    const pending = this._pending.find(m => m.id === e.currentTarget.dataset.id)
    if (pending && pending.failed) this.deliver(pending)
  },
  offerCard() {
    if (!this._card || !this.data.canSend) return
    wx.showModal({ title: this.data.cardOffer, content: '将真实的业务卡片发送给对方，操作权限仍以赛事规则为准。', success: r => {
      if (r.confirm && this._visible) { this.queue(chat.packet('', this._card.kind, this._card.id)); this.setData({ cardOffer: '' }) }
    } })
  },
  scroll(e) {
    this._atBottom = e.detail.scrollHeight - e.detail.scrollTop - (this._viewportHeight || 300) < 70
    clearTimeout(this._scrollTimer); this._scrollTimer = setTimeout(() => this.observeRead(), 250)
  },
  bottom() { this._atBottom = true; if (this._olderWindow) this.load(true); else this.setData({ anchor: '' }, () => this.render(true)) },
  observeRead() {
    if (!this._visible || !this.data.room || this._reading || !this.createSelectorQuery) return
    const g = this._generation
    const query = this.createSelectorQuery()
    query.select('#thread').boundingClientRect()
    query.selectAll('.incoming-unread').fields({ rect: true, dataset: true })
    query.exec(result => {
      if (!this.current(g) || this._reading || !result || !result[0]) return
      const viewport = result[0]; this._viewportHeight = viewport.height
      const ids = (result[1] || []).filter(r => r.bottom > viewport.top && r.top < viewport.bottom).map(r => Number(r.dataset.messageId)).filter(Number.isSafeInteger).slice(0, 100)
      if (!ids.length) return
      this._reading = g
      chat.call('/conversations/' + this._cid + '/read', 'PUT', { message_ids: ids }).then(() => {
        if (!this.current(g)) return
        this._items = this._items.map(m => ids.includes(Number(m.id)) ? Object.assign({}, m, { read_at: new Date().toISOString() }) : m)
        this.render(false); this.sync()
      }).catch(() => {}).finally(() => { if (this._reading === g) this._reading = null })
    })
  },
  menu() {
    const room = this.data.room
    if (!room) return
    const g = this._generation
    wx.showActionSheet({ itemList: [room.pinned ? '取消置顶' : '置顶聊天', room.muted ? '关闭免打扰' : '消息免打扰', room.blocked_by_me ? '解除拉黑' : '拉黑对方'], success: async r => {
      if (!this.current(g)) return
      try {
        if (r.tapIndex < 2) await chat.call('/conversations/' + this._cid + '/preferences', 'PATCH', r.tapIndex === 0 ? { pinned: !room.pinned } : { muted: !room.muted })
        else await chat.call('/blocks/' + room.peer.id, room.blocked_by_me ? 'DELETE' : 'PUT')
        if (this.current(g)) this.sync()
      } catch (err) { wx.showToast({ title: err.detail || '操作失败', icon: 'none' }) }
    } })
  },
  actions(e) {
    const pending = this._pending.find(m => m.id === e.currentTarget.dataset.id)
    if (pending) {
      if (!pending.failed || pending.busy) return
      const g = this._generation
      wx.showModal({ title: '移除失败消息', content: '移除本地待发送记录，不会删除已被服务器接收的消息。', success: r => {
        if (r.confirm && this.current(g)) { this._pending = this._pending.filter(m => m.id !== pending.id); this.render(false) }
      } })
      return
    }
    const msg = this._items.find(m => m.id === e.currentTarget.dataset.id)
    if (!msg || msg.state !== 'active') return
    const g = this._generation, mine = msg.sender_id === this.data.selfId
    wx.showActionSheet({ itemList: ['复制文字', mine ? '撤回消息' : '举报消息'], success: async r => {
      if (!this.current(g)) return
      if (r.tapIndex === 0) { wx.setClipboardData({ data: msg.content || '' }); return }
      try {
        if (mine) { await chat.call('/conversations/' + this._cid + '/messages/' + msg.id + '/recall', 'POST'); if (this.current(g)) this.sync() }
        else wx.showModal({ title: '举报消息', editable: true, placeholderText: '请说明原因，最多300字', success: async reply => {
          if (!reply.confirm || !reply.content || !this.current(g)) return
          try { await chat.call('/reports', 'POST', { message_id: Number(msg.id), reason: reply.content }); wx.showToast({ title: '举报已提交', icon: 'none' }) }
          catch (err) { wx.showToast({ title: err.detail || '举报失败', icon: 'none' }) }
        } })
      } catch (err) { wx.showToast({ title: err.detail || '操作失败', icon: 'none' }) }
    } })
  },
  openCard(e) {
    const msg = this.data.rows.find(m => m.id === e.currentTarget.dataset.id)
    if (!msg || !msg.card) return
    const c = msg.card
    if (c.type === 'round') wx.navigateTo({ url: '/pages/match/match?id=' + c.match_id + '&roundId=' + c.id })
    else if (c.type === 'team') wx.navigateTo({ url: '/pages/teams/teams?teamId=' + c.id })
  }
})
