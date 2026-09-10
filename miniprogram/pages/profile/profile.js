/**
 * 个人资料页（侧边栏「个人资料」入口）
 * 功能：用户卡 + 个人信息 + 修改资料
 */
const api = require('../../utils/api.js')
const { rankDisplay, rankBadge } = require('../../utils/rank.js')

Page({
  data: {
    token: '',
    user: null,
    isManager: false,
    editMode: false,
    loadFailed: false,
    saving: false,
    avatarUploading: false,
    nickname: '',
    gameId: ''
  },

  onShow() {
    const token = wx.getStorageSync('token') || ''
    this.setData({ token })
    if (token) this.loadUser()
    else this.setData({ user: null, editMode: false, isManager: false })
  },

  async onPullDownRefresh() {
    const token = wx.getStorageSync('token') || ''
    this.setData({ token })
    if (token) await this.loadUser()
    wx.stopPullDownRefresh()
  },

  async loadUser() {
    this.setData({ loadFailed: false })
    try {
      const user = await api.getMe()
      this.decorateUser(user)
      this.setData({ user, isManager: !!(user && (user.role === 'admin' || user.role === 'reviewer')) })
    } catch (err) {
      console.error('加载用户信息失败', err)
      this.setData({ isManager: false, loadFailed: true })
    }
  },

  decorateUser(user) {
    if (!user) return
    user.display_rank = rankDisplay(user.rank)
    user.rank_badge = rankBadge(user.rank)
    if (user.avatar) user.avatar_full = user.avatar.startsWith('http') ? user.avatar : api.BASE + user.avatar
  },

  goLogin() { wx.navigateTo({ url: '/pages/login/login' }) },
  goTeam() { wx.navigateTo({ url: '/pages/team/team' }) },
  goVerify() { wx.navigateTo({ url: '/pages/verify/verify' }) },
  goMessages() { wx.navigateTo({ url: '/pages/messages/messages' }) },

  onChooseAvatar(e) {
    if (this.data.avatarUploading || this.data.saving) return
    const avatarUrl = e.detail.avatarUrl
    if (!avatarUrl) return
    this.uploadAvatar(avatarUrl)
  },

  async uploadAvatar(filePath) {
    this.setData({ avatarUploading: true })
    try {
      const user = await api.uploadAvatar(filePath)
      this.decorateUser(user)
      this.setData({ user })
      wx.showToast({ title: '头像已更新', icon: 'success' })
    } catch (err) {
      wx.showToast({ title: err.detail || '上传失败', icon: 'error' })
    } finally {
      this.setData({ avatarUploading: false })
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

  cancelEdit() {
    if (this.data.saving || this.data.avatarUploading) return
    this.setData({ editMode: false })
  },

  onNickInput(e) { this.setData({ nickname: e.detail.value }) },
  onGameIdInput(e) { this.setData({ gameId: e.detail.value }) },

  async saveProfile() {
    if (this.data.saving || this.data.avatarUploading) return
    this.setData({ saving: true })
    try {
      await api.updateProfile({ nickname: this.data.nickname, game_id: this.data.gameId })
      wx.showToast({ title: '已更新', icon: 'success' })
      this.setData({ editMode: false })
      await this.loadUser()
    } catch (err) {
      wx.showToast({ title: err.detail || '更新失败', icon: 'error' })
    } finally {
      this.setData({ saving: false })
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
