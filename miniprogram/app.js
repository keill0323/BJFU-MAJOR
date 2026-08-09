App({
  globalData: {
    token: '',
    baseUrl: 'http://127.0.0.1:8000'
  },

  onLaunch() {
    const token = wx.getStorageSync('token')
    if (token) {
      this.globalData.token = token
    }
  }
})
