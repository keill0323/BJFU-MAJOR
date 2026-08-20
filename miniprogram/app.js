const api = require('./utils/api.js')

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

  // 每次进入前台：更新队伍 tab 红点（有待处理入队邀请时显示）
  onShow() {
    this.updateTeamRedDot()
  },

  async updateTeamRedDot() {
    const token = wx.getStorageSync('token')
    if (!token) return
    try {
      const invites = await api.getMyInvitations()
      const hasInvite = (invites || []).length > 0
      let hasRankApp = false
      try {
        const apps = await api.getMyRankApplications()
        hasRankApp = (apps || []).some(a => a.status === 'pending' || a.status === 'rejected')
      } catch (e) {}
      if (hasInvite || hasRankApp) {
        wx.showTabBarRedDot({ index: 1 })   // tabBar 第2个是「队伍」
      } else {
        wx.hideTabBarRedDot({ index: 1 })
      }
    } catch (e) {
      // 网络异常忽略，不阻断启动
    }
  },

  // 注册隐私授权监听：收集微信信息、学号、学信网截图等敏感信息前，必须弹窗征得用户同意
  _registerPrivacy() {
    if (wx.onNeedPrivacyAuthorization) {
      wx.onNeedPrivacyAuthorization((resolve) => {
        wx.showModal({
          title: '隐私保护提示',
          content: '本小程序将收集你的微信信息、学号及学信网/校园卡截图，仅用于赛事报名与身份认证。请阅读并同意《用户隐私保护指引》后再使用。',
          confirmText: '同意',
          cancelText: '拒绝',
          success: (res) => {
            if (res.confirm) {
              // 用户同意后，通知微信继续执行原本要调用的隐私接口
              resolve({ event: 'agree' })
            }
          }
        })
      })
    }
  }
})
