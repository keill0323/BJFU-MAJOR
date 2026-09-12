const api = require('../../utils/api.js')

Component({
  data: { loggedIn: false, ready: false, configured: false, busy: false, failed: false, label: '' },
  lifetimes: {
    attached() { this._alive = true; this.loadConfig() },
    detached() { this._alive = false; this._generation = (this._generation || 0) + 1 }
  },
  pageLifetimes: { show() { this.loadConfig() } },
  methods: {
    async loadConfig() {
      const generation = this._generation = (this._generation || 0) + 1
      const token = wx.getStorageSync('token') || ''
      this._token = token
      this._templateIds = []
      this.setData({ loggedIn: !!token, ready: false, configured: false, failed: false })
      if (!token) return
      try {
        const config = await api.getWechatConfig()
        if (!this._alive || generation !== this._generation || token !== wx.getStorageSync('token')) return
        this._templateIds = [...new Set((config.templates || []).map(t => t.template_id))].slice(0, 3)
        this.setData({ ready: true, configured: config.enabled && this._templateIds.length > 0,
          label: (config.templates || []).map(t => t.label).join('、') })
      } catch (err) {
        if (this._alive && generation === this._generation) this.setData({ failed: true })
      }
    },
    subscribe() {
      if (this.data.busy || !this.data.configured) return
      const token = this._token
      if (token !== wx.getStorageSync('token')) { this.loadConfig(); return }
      if (!wx.requestSubscribeMessage) { wx.showToast({ title: '请更新微信后再试', icon: 'none' }); return }
      const ids = this._templateIds.slice()
      this.setData({ busy: true })
      // 必须直接从用户点击调用，不能先 await 网络请求。
      wx.requestSubscribeMessage({
        tmplIds: ids,
        success: async result => {
          if (token !== wx.getStorageSync('token')) { if (this._alive) this.setData({ busy: false }); return }
          const choices = {}
          ids.forEach(id => { if (['accept', 'reject', 'ban'].includes(result[id])) choices[id] = result[id] })
          try {
            if (!Object.keys(choices).length) throw new Error('empty')
            await api.saveWechatSubscriptions(choices)
            if (token === wx.getStorageSync('token')) wx.showToast({ title: Object.values(choices).includes('accept') ? '已订阅，可随时再次授权' : '未开启，仍可查看站内消息', icon: 'none' })
          } catch (err) {
            wx.showToast({ title: '订阅记录未保存，请重试', icon: 'none' })
          } finally { if (this._alive) this.setData({ busy: false }) }
        },
        fail: () => {
          wx.showToast({ title: '未能订阅，请检查微信通知设置', icon: 'none' })
          if (this._alive) this.setData({ busy: false })
        }
      })
    }
  }
})
