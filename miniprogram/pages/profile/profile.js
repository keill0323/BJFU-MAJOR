/**
 * 个人资料页（侧边栏「个人资料」入口）
 * 功能：用户卡 + 个人信息 + 修改资料
 */
const api = require('../../utils/api.js')
const { rankDisplay } = require('../../utils/rank.js')

Page({
  data: {
    token: '',
    user: null,
    isManager: false,
    editMode: false,
    nickname: '',
    gameId: ''
  },

  onShow() {
    const token = wx.getStorageSync('token') || ''
    this.setData({ token })
    if (token) this.loadUser()
  },

  async onPullDownRefresh() {
    const token = wx.getStorageSync('token') || ''
    this.setData({ token })
    if (token) await this.loadUser()
    wx.stopPullDownRefresh()
  },

  async loadUser() {
    try {
      const user = await api.getMe()
      if (user) user.display_rank = rankDisplay(user.rank)
      if (user && user.avatar && !user.avatar.startsWith('http')) user.avatar_full = api.BASE + user.avatar
      this.setData({ user, isManager: !!(user && (user.role === 'admin' || user.role === 'reviewer')) })
    } catch (err) {
      console.error('加载用户信息失败', err)
      this.setData({ isManager: false })
    }
  },

  onChooseAvatar(e) {
    const avatarUrl = e.detail.avatarUrl
    if (!avatarUrl) return
    this.uploadAvatar(avatarUrl)
  },

  async uploadAvatar(filePath) {
    try {
      const user = await api.uploadAvatar(filePath)
      if (user && user.avatar && !user.avatar.startsWith('http')) user.avatar_full = api.BASE + user.avatar
      this.setData({ user })
      wx.showToast({ title: '头像已更新', icon: 'success' })
    } catch (err) {
      wx.showToast({ title: err.detail || '上传失败', icon: 'error' })
    }
  },

  goAdmin() {
    const user = this.data.user
    if (user && user.role !== 'admin' && user.role !== 'reviewer') {
      wx.showToast({ title: '仅管理员/审核员可进入', icon: 'none' })
      return
    }
    wx.navigateTo({ url: '/pages/admin/admin' })
  },

  goRules() {
    wx.navigateTo({ url: '/pages/rules/rules' })
  },

  enableEdit() {
    const user = this.data.user || {}
    this.setData({ editMode: true, nickname: user.nickname || '', gameId: user.game_id || '' })
  },

  onNickInput(e) { this.setData({ nickname: e.detail.value }) },
  onGameIdInput(e) { this.setData({ gameId: e.detail.value }) },

  async saveProfile() {
    try {
      await api.updateProfile({ nickname: this.data.nickname, game_id: this.data.gameId })
      wx.showToast({ title: '已更新', icon: 'success' })
      this.setData({ editMode: false })
      await this.loadUser()
    } catch (err) {
      wx.showToast({ title: err.detail || '更新失败', icon: 'error' })
    }
  },

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
