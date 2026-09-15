const AGREE_BUTTON_ID = 'agree-privacy-images'

Component({
  data: {
    visible: false,
    loading: false,
    ready: false,
    error: '',
    contractName: '《用户隐私保护指引》'
  },
  lifetimes: {
    attached() {
      this._alive = true
      this._resolvers = new Set()
      this._generation = 0
    },
    detached() {
      this._alive = false
      this._generation++
      this.resolveRequests({ event: 'disagree' })
    }
  },
  methods: {
    requestAuthorization(resolve) {
      if (!this._alive) { resolve({ event: 'disagree' }); return }
      this._resolvers.add(resolve)
      if (this.data.visible) return
      this.setData({ visible: true })
      this.loadContract()
    },
    loadContract() {
      const generation = ++this._generation
      this.setData({ loading: true, ready: false, error: '' })
      if (!wx.getPrivacySetting) {
        this.setData({ loading: false, error: '请更新微信后重试。' })
        return
      }
      wx.getPrivacySetting({
        success: res => {
          if (!this._alive || generation !== this._generation) return
          this.setData({ loading: false, ready: true,
            contractName: (res && res.privacyContractName) || '《用户隐私保护指引》' })
        },
        fail: () => {
          if (this._alive && generation === this._generation) {
            this.setData({ loading: false, error: '隐私指引暂时无法加载，请重试。' })
          }
        }
      })
    },
    openContract() {
      if (!wx.openPrivacyContract) {
        wx.showToast({ title: '请更新微信后查看隐私指引', icon: 'none' })
        return
      }
      wx.openPrivacyContract({
        fail: () => {
          if (this._alive) wx.showToast({ title: '隐私指引打开失败，请重试', icon: 'none' })
        }
      })
    },
    agree(event) {
      if (!this._alive || !this.data.visible || !this.data.ready ||
          !event || !event.currentTarget || event.currentTarget.id !== AGREE_BUTTON_ID) return
      // 仅绑定 bindagreeprivacyauthorization。先回传真实按钮 ID，再隐藏按钮，
      // 让微信核验用户点击并自动恢复原 chooseMedia / chooseAvatar，勿重复调用选图。
      this.resolveRequests({ event: 'agree', buttonId: AGREE_BUTTON_ID })
      this.close()
    },
    disagree() {
      this.resolveRequests({ event: 'disagree' })
      this.close()
    },
    resolveRequests(result) {
      const pending = Array.from(this._resolvers || [])
      if (this._resolvers) this._resolvers.clear()
      pending.forEach(resolve => {
        try { resolve(result) } catch (_) { console.warn('Privacy authorization callback failed') }
      })
    },
    close() {
      this._generation++
      if (this._alive) this.setData({ visible: false, loading: false, ready: false, error: '' })
    },
    stopMove() {}
  }
})
