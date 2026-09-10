const api = require('../../utils/api.js')
const { rankDisplay, rankBadge } = require('../../utils/rank.js')

Page({
  data: {
    token: '',
    user: null,
    uploading: false,
    loadFailed: false,
    verifiedCount: 0,
    rankTiers: ['D', 'C', 'B', 'A', 'S'],
    rankTier: '',
    rankStars: null,
    rankProgress: 0
  },

  AI_STATUS_MAP: {
    auto_pass: { text: 'AI 已自动通过', cls: 'ai-pass' },
    auto_reject: { text: 'AI 已自动驳回', cls: 'ai-reject' },
    pending: { text: 'AI 初审中，待人工复核', cls: 'ai-pending' }
  },

  onShow() {
    const token = wx.getStorageSync('token') || ''
    this.setData({ token })
    if (!token) {
      this.setData({ user: null })
      return
    }
    this.loadUser()
  },

  async loadUser() {
    this.setData({ loadFailed: false })
    try {
      const user = await api.getMe()
      if (user) user.display_rank = rankDisplay(user.rank)
      if (user) user.rank_badge = rankBadge(user.rank)
      if (user && user.verify_image) user.verify_image_full = user.verify_image.startsWith('http') ? user.verify_image : api.BASE + user.verify_image
      if (user && user.rank_image) user.rank_image_full = user.rank_image.startsWith('http') ? user.rank_image : api.BASE + user.rank_image
      if (user && user.ai_review_status) {
        const st = this.AI_STATUS_MAP[user.ai_review_status] || { text: '', cls: '' }
        user.ai_review_text = st.text
        user.ai_review_class = st.cls
      }
      const starMatch = user && /^S(\d+)$/.exec(user.rank || '')
      const rankStars = starMatch ? Math.min(50, Math.max(0, Number(starMatch[1]))) : null
      this.setData({
        user,
        rankTier: user && user.rank ? user.rank.charAt(0) : '',
        rankStars,
        rankProgress: rankStars === null ? 0 : rankStars * 2,
        verifiedCount: user ? Number(!!user.is_verified) + Number(!!user.rank) : 0
      })
    } catch (err) {
      console.error('加载用户信息失败', err)
      this.setData({ loadFailed: true })
    }
  },

  goLogin() { wx.navigateTo({ url: '/pages/login/login' }) },
  goRules() { wx.navigateTo({ url: '/pages/rules/rules' }) },

  // 选择并上传学信网截图
  chooseVerifyImage() {
    if (this.data.uploading) return
    wx.chooseMedia({
      count: 1,
      mediaType: ['image'],
      sourceType: ['album', 'camera'],
      success: (res) => {
        const filePath = res.tempFiles[0].tempFilePath
        wx.compressImage({
          src: filePath,
          quality: 80,
          success: (cr) => {
            const compressedPath = (cr && cr.tempFilePath) || filePath
            this.uploadWithPath(compressedPath)
          },
          fail: (err) => this.uploadWithPath(filePath)
        })
      }
    })
  },
  uploadWithPath(filePath) {
    this.setData({ uploading: true })
    api.uploadVerify(filePath)
      .then(() => {
        wx.showToast({ title: '上传成功，等待审核', icon: 'success' })
        return this.loadUser()
      })
      .catch(err => wx.showToast({ title: err.detail || '上传失败', icon: 'none' }))
      .finally(() => this.setData({ uploading: false }))
  },

  // 选择并上传游戏段位截图（AI 识别段位）
  chooseRankImage() {
    if (this.data.uploading) return
    wx.chooseMedia({
      count: 1,
      mediaType: ['image'],
      sourceType: ['album', 'camera'],
      success: (res) => {
        const filePath = res.tempFiles[0].tempFilePath
        wx.compressImage({
          src: filePath,
          quality: 80,
          success: (cr) => {
            const compressedPath = (cr && cr.tempFilePath) || filePath
            this.uploadRankWithPath(compressedPath)
          },
          fail: () => this.uploadRankWithPath(filePath)
        })
      }
    })
  },
  uploadRankWithPath(filePath) {
    this.setData({ uploading: true })
    return api.uploadRank(filePath)
      .then((res) => {
        if (res.auto_applied) {
          wx.showToast({ title: '识别成功：' + res.rank, icon: 'success' })
        } else if (res.need_review) {
          wx.showModal({
            title: '已提交段位审核',
            content: (res.rank ? 'AI 识别为 ' + res.rank + '。' : '') + '截图已提交，等待管理员审核，可在消息页查看审核结果。',
            showCancel: false
          })
        } else {
          const rankText = res.rank ? 'AI 识别为 ' + res.rank + '，但置信度不足。\n' : ''
          wx.showModal({
            title: 'AI 初审结果',
            content: rankText + (res.reason || '请联系管理员手动设置段位'),
            showCancel: false
          })
        }
        return this.loadUser()
      })
      .catch(err => wx.showToast({ title: err.detail || '上传失败', icon: 'none' }))
      .finally(() => this.setData({ uploading: false }))
  },
  previewVerifyImage() {
    const url = this.data.user && this.data.user.verify_image_full
    if (url) wx.previewImage({ urls: [url], current: url })
  },
  previewRankImage() {
    const url = this.data.user && this.data.user.rank_image_full
    if (url) wx.previewImage({ urls: [url], current: url })
  }
})
