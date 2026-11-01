const subscriptions = require('../../utils/wechat-subscriptions.js')

Component({
  data: { loggedIn: false, ready: false, configured: false, busy: false, failed: false, stateFailed: false, label: '', needsResubscribe: false, deliveryUnknown: false, deliveryFailed: false },
  lifetimes: {
    attached() { this._alive = true; this.loadConfig() },
    detached() { this._alive = false; this._generation = (this._generation || 0) + 1 }
  },
  pageLifetimes: { show() { this.loadConfig() } },
  methods: {
    async loadConfig() {
      const generation = this._generation = (this._generation || 0) + 1, token = wx.getStorageSync('token') || ''
      this._token = token
      this.setData({ loggedIn: !!token, ready: false, configured: false, failed: false })
      const state = await subscriptions.prefetch()
      if (this._alive && generation === this._generation && token === (wx.getStorageSync('token') || '')) this.setData(state)
    },
    subscribe() {
      if (this.data.busy || !this.data.configured) return
      if (this._token !== (wx.getStorageSync('token') || '')) { this.loadConfig(); return }
      this.setData({ busy: true })
      // prompt invokes WeChat immediately from this tap using prefetched config.
      return subscriptions.prompt('all').finally(() => {
        if (this._alive) { this.setData({ busy: false }); return this.loadConfig() }
      })
    }
  }
})
