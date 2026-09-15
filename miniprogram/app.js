App({
  globalData: {
    token: '',
    baseUrl: 'https://bjfumajor.com'
  },

  onLaunch() {
    const token = wx.getStorageSync('token')
    if (token) {
      this.globalData.token = token
    }
    this._registerPrivacy()
  },

  // 原隐私接口会等待 resolve；必须由页面上的微信隐私授权按钮完成同意。
  _registerPrivacy() {
    if (wx.onNeedPrivacyAuthorization) {
      wx.onNeedPrivacyAuthorization((resolve) => {
        const pages = getCurrentPages()
        const page = pages[pages.length - 1]
        const dialog = page && page.selectComponent && page.selectComponent('#privacy-dialog')
        if (dialog && typeof dialog.requestAuthorization === 'function') {
          dialog.requestAuthorization(resolve)
        } else {
          // 未挂载/已离开的页面不能将选图接口一直留在等待状态。
          resolve({ event: 'disagree' })
          wx.showToast({ title: '隐私授权页面未就绪，请重试', icon: 'none' })
        }
      })
    }
  }
})
