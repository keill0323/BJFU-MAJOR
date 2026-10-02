const chat = require('../../utils/chat-client.js')
Component({
  data: { unread: 0 },
  lifetimes: { attached() { this.start() }, detached() { this.stop() } },
  pageLifetimes: { show() { this.start() }, hide() { this.stop() } },
  methods: {
    start() {
      if (this._off) return
      this._active = true; this._generation = (this._generation || 0) + 1
      this._token = wx.getStorageSync('token') || ''
      this.setData({ unread: 0 })
      this._off = chat.subscribe(e => {
        if (e.type === 'account') {
          this._generation++; this._request = null; this._again = false
          this._token = wx.getStorageSync('token') || ''; this.setData({ unread: 0 })
          if (this._active) this.refresh()
        } else if (['ready', 'dirty', 'tick'].includes(e.type)) this.refresh()
      })
      this.refresh()
    },
    stop() {
      this._active = false; this._generation = (this._generation || 0) + 1; this._request = null; this._again = false
      if (this._off) this._off(); this._off = null
    },
    async refresh() {
      if (!this._active) return
      const token = wx.getStorageSync('token') || ''
      if (token !== this._token) { this._generation++; this._request = null; this._token = token; this.setData({ unread: 0 }) }
      if (!token) { this.setData({ unread: 0 }); return }
      if (this._request) { this._again = true; return }
      const g = this._generation, op = {}; this._request = op
      const usable = () => this._active && this._generation === g && token === (wx.getStorageSync('token') || '') && this._request === op
      try {
        const result = await chat.call('/summary')
        if (usable()) this.setData({ unread: result.unread })
      } catch (_) { if (usable()) this.setData({ unread: 0 }) }
      finally {
        if (this._request === op) {
          this._request = null
          const again = this._again; this._again = false
          if (again && usableAfter(this, g, token)) this.refresh()
        }
      }
    },
    open() { wx.navigateTo({ url: '/pages/chats/chats' }) }
  }
})
function usableAfter(component, generation, token) {
  return component._active && component._generation === generation && token === (wx.getStorageSync('token') || '')
}
