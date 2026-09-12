const api = require('../../utils/api.js')
const { rankDisplay } = require('../../utils/rank.js')

const scheduleTitles = {
  proposed: '对方发来约赛时间，请确认', updated: '对方修改了约赛时间，请确认',
  confirmed: '对方已同意，比赛时间已确定', rejected: '对方拒绝了约赛时间',
  cancelled: '对方已撤回或作废约定时间', window_changed: '管理者调整时段，原约定已作废'
}
function decorateNotice(item) {
  return Object.assign({}, item, {
    title: scheduleTitles[item.kind] || '约赛时间通知',
    timeText: String(item.scheduled_time || '').replace('T', ' ').slice(0, 16),
    createdText: String(item.created_at || '').replace('T', ' ').slice(0, 16)
  })
}

Page({
  data: {
    token: '', loading: false, loadError: false,
    invitations: [], teamApplications: [],
    rankApplications: [],
    scheduleNotices: [], unreadSchedules: 0, noticeCursor: null,
    hasMoreNotices: false, loadingMore: false, noticeError: '', openingNotice: null
  },

  onLoad(options) { this._section = options && ['invitations', 'applications', 'schedules'].includes(options.section) ? options.section : '' },

  onShow() {
    const token = wx.getStorageSync('token') || ''
    if (token !== this.data.token) {
      this.setData({ invitations: [], teamApplications: [], rankApplications: [], scheduleNotices: [], unreadSchedules: 0,
        noticeCursor: null, hasMoreNotices: false, openingNotice: null })
    }
    this.setData({ token })
    this.loadMessages()
  },

  onUnload() { this._requestId = (this._requestId || 0) + 1 },

  onPullDownRefresh() {
    this.loadMessages().finally(() => wx.stopPullDownRefresh())
  },

  async loadMessages() {
    if (this.data.openingNotice) return
    const requestId = this._requestId = (this._requestId || 0) + 1
    const token = this.data.token
    if (!this.data.token) {
      this.setData({ invitations: [], teamApplications: [], rankApplications: [], scheduleNotices: [], unreadSchedules: 0,
        loading: false, loadError: false, loadingMore: false, noticeError: '', hasMoreNotices: false })
      return
    }
    this.setData({ loading: true, loadError: false, noticeError: '', loadingMore: false })
    let loadError = false
    let noticeError = ''
    const results = await Promise.all([
      api.getMyInvitations().catch(() => { loadError = true; return [] }),
      api.getMyRankApplications().catch(() => { loadError = true; return [] }),
      api.getScheduleNotifications().catch(err => {
        loadError = true
        noticeError = err.detail || '约赛通知加载失败，请重试'
        return null
      }),
      api.getMyTeamApplications().catch(() => { loadError = true; return [] })
    ])
    if (!this.isCurrent(requestId, token)) return
    const apps = (results[1] || []).map(a => Object.assign({}, a, {
        statusText: a.status === 'approved' ? '已通过' : a.status === 'rejected' ? '已驳回' : '待审批',
        statusCls: a.status === 'approved' ? 'ok' : a.status === 'rejected' ? 'reject' : 'pending'
    }))
    const updates = { teamApplications: (results[3] || []).map(a => Object.assign({}, a, { displayRank: rankDisplay(a.rank), avatarFull: a.avatar ? (/^https?:\/\//.test(a.avatar) ? a.avatar : api.BASE + a.avatar) : '' })), invitations: results[0] || [], rankApplications: apps, loading: false, loadError, noticeError }
    if (results[2]) Object.assign(updates, {
      scheduleNotices: results[2].items.map(decorateNotice), unreadSchedules: results[2].unread_count,
      noticeCursor: results[2].next_cursor, hasMoreNotices: results[2].has_more
    })
    this.setData(updates, () => {
      if (this._section && this.isCurrent(requestId, token)) {
        wx.pageScrollTo({ selector: '#notice-' + this._section, duration: 250, offsetTop: -100 })
        this._section = ''
      }
    })
  },

  isCurrent(requestId, token) {
    return requestId === this._requestId && token === this.data.token && token === wx.getStorageSync('token')
  },

  onReachBottom() { this.loadMoreNotices() },
  async loadMoreNotices() {
    if (this.data.loading || this.data.loadingMore || this.data.openingNotice || !this.data.hasMoreNotices || this.data.noticeError) return
    const requestId = this._requestId, token = this.data.token
    this.setData({ loadingMore: true })
    try {
      const page = await api.getScheduleNotifications(this.data.noticeCursor)
      if (!this.isCurrent(requestId, token)) return
      const existing = new Set(this.data.scheduleNotices.map(n => n.id))
      this.setData({ scheduleNotices: this.data.scheduleNotices.concat(page.items.filter(n => !existing.has(n.id)).map(decorateNotice)),
        noticeCursor: page.next_cursor, hasMoreNotices: page.has_more, unreadSchedules: page.unread_count })
    } catch (err) {
      if (this.isCurrent(requestId, token)) wx.showToast({ title: err.detail || '加载失败，请重试', icon: 'none' })
    } finally {
      if (this.isCurrent(requestId, token)) this.setData({ loadingMore: false })
    }
  },

  async openScheduleNotice(e) {
    if (this.data.openingNotice || this.data.loading || this.data.loadingMore) return
    const notice = this.data.scheduleNotices.find(n => n.id === Number(e.currentTarget.dataset.id))
    if (!notice) return
    const requestId = this._requestId, token = this.data.token
    this.setData({ openingNotice: notice.id })
    try {
      if (!notice.is_read) {
        await api.readScheduleNotification(notice.id)
        if (!this.isCurrent(requestId, token)) return
        this.setData({ scheduleNotices: this.data.scheduleNotices.map(n => n.id === notice.id ? Object.assign({}, n, { is_read: true }) : n),
          unreadSchedules: Math.max(0, this.data.unreadSchedules - 1) })
      }
      if (!this.isCurrent(requestId, token)) return
      if (!notice.round_available) {
        wx.showToast({ title: '该对阵已移除，仅保留历史通知', icon: 'none' })
        return
      }
      wx.navigateTo({ url: '/pages/match/match?id=' + notice.match_id + '&roundId=' + notice.round_id,
        fail: () => wx.showToast({ title: '打开失败，请重试', icon: 'none' }) })
    } catch (err) {
      if (this.isCurrent(requestId, token)) wx.showToast({ title: err.detail || '打开通知失败，请重试', icon: 'none' })
    } finally {
      if (this.isCurrent(requestId, token)) this.setData({ openingNotice: null })
    }
  },

  async handleApplication(e) {
    if (this._handlingApplication) return
    const { id, action } = e.currentTarget.dataset
    if (!['approve', 'reject'].includes(action)) return
    this._handlingApplication = true
    try {
      if (action === 'approve') await api.approveApplication(id)
      else await api.rejectApplication(id)
      wx.showToast({ title: action === 'approve' ? '已同意入队' : '已拒绝申请', icon: 'none' })
      await this.loadMessages()
    } catch (err) { wx.showToast({ title: err.detail || '操作失败', icon: 'none' }) }
    finally { this._handlingApplication = false }
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
