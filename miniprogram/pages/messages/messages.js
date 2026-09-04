const api = require('../../utils/api.js')

Page({
  data: {
    invitations: [],
    rankApplications: []
  },

  onShow() {
    this.loadMessages()
  },

  onPullDownRefresh() {
    this.loadMessages().finally(() => wx.stopPullDownRefresh())
  },

  async loadMessages() {
    try {
      const invites = await api.getMyInvitations()
      this.setData({ invitations: invites || [] })
    } catch (err) { this.setData({ invitations: [] }) }
    try {
      const apps = (await api.getMyRankApplications() || []).map(a => Object.assign({}, a, {
        statusText: a.status === 'approved' ? '已通过' : a.status === 'rejected' ? '已驳回' : '待审批',
        statusCls: a.status === 'approved' ? 'ok' : a.status === 'rejected' ? 'reject' : 'pending'
      }))
      this.setData({ rankApplications: apps })
    } catch (err) { this.setData({ rankApplications: [] }) }
  },

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
