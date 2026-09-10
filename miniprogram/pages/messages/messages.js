const api = require('../../utils/api.js')

Page({
  data: {
    token: '', loading: false, loadError: false,
    invitations: [],
    rankApplications: []
  },

  onShow() {
    this.setData({ token: wx.getStorageSync('token') || '' })
    this.loadMessages()
  },

  onPullDownRefresh() {
    this.loadMessages().finally(() => wx.stopPullDownRefresh())
  },

  async loadMessages() {
    if (!this.data.token) {
      this.setData({ invitations: [], rankApplications: [], loading: false, loadError: false })
      return
    }
    this.setData({ loading: true, loadError: false })
    let loadError = false
    const results = await Promise.all([
      api.getMyInvitations().catch(() => { loadError = true; return [] }),
      api.getMyRankApplications().catch(() => { loadError = true; return [] })
    ])
    const apps = (results[1] || []).map(a => Object.assign({}, a, {
        statusText: a.status === 'approved' ? '已通过' : a.status === 'rejected' ? '已驳回' : '待审批',
        statusCls: a.status === 'approved' ? 'ok' : a.status === 'rejected' ? 'reject' : 'pending'
    }))
    this.setData({ invitations: results[0] || [], rankApplications: apps, loading: false, loadError })
  },

  goLogin() { wx.reLaunch({ url: '/pages/login/login' }) },

  async acceptInv(e) {
    const id = e.currentTarget.dataset.id
    try {
      await api.acceptInvitation(id)
      wx.showToast({ title: '已入队', icon: 'success' })
      this.loadMessages()
    } catch (err) {
      wx.showToast({ title: err.detail || '操作失败', icon: 'error' })
    }
  },

  async rejectInv(e) {
    const id = e.currentTarget.dataset.id
    try {
      await api.rejectInvitation(id)
      wx.showToast({ title: '已拒绝', icon: 'none' })
      this.loadMessages()
    } catch (err) {
      wx.showToast({ title: err.detail || '操作失败', icon: 'error' })
    }
  }
})
