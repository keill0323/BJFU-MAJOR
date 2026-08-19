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
  }
})
