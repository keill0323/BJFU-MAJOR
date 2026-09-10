const api = require('../../utils/api.js')

Page({
  data: {
    token: '',
    isManager: false
  },

  onShow() {
    const token = wx.getStorageSync('token') || ''
    this.setData({ token, isManager: false })
    if (token) this.loadUser()
  },

  async loadUser() {
    try {
      const user = await api.getMe()
      this.setData({ isManager: !!(user && (user.role === 'admin' || user.role === 'reviewer')) })
    } catch (e) {
      this.setData({ isManager: false })
    }
  },

  goAdmin() {
    wx.navigateTo({ url: '/pages/admin/admin' })
  },

  goRules() {
    wx.navigateTo({ url: '/pages/rules/rules' })
  },

  goLogin() { wx.reLaunch({ url: '/pages/login/login' }) },

  logout() {
    wx.showModal({
      title: '退出登录',
      content: '确定要退出吗?',
      success: (res) => {
        if (!res.confirm) return
        wx.removeStorageSync('token')
        wx.reLaunch({ url: '/pages/login/login' })
      }
    })
  }
})
