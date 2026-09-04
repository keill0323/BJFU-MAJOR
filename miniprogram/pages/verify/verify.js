const api = require('../../utils/api.js')
const { rankDisplay } = require('../../utils/rank.js')

Page({
  data: {
    user: null,
    uploading: false
  },

  AI_STATUS_MAP: {
    auto_pass: { text: 'AI 已自动通过', cls: 'ai-pass' },
    auto_reject: { text: 'AI 已自动驳回', cls: 'ai-reject' },
    pending: { text: 'AI 初审中，待人工复核', cls: 'ai-pending' }
  },

  onShow() {
    if (!wx.getStorageSync('token')) return
    this.loadUser()
  },

  async loadUser() {
    try {
      const user = await api.getMe()
      if (user) user.display_rank = rankDisplay(user.rank)
      if (user && user.verify_image && !user.verify_image.startsWith('http')) user.verify_image_full = api.BASE + user.verify_image
      if (user && user.rank_image && !user.rank_image.startsWith('http')) user.rank_image_full = api.BASE + user.rank_image
      if (user && user.ai_review_status) {
        const st = this.AI_STATUS_MAP[user.ai_review_status] || { text: '', cls: '' }
        user.ai_review_text = st.text
        user.ai_review_class = st.cls
      }
      this.setData({ user })
    } catch (err) {
      console.error('加载用户信息失败', err)
    }
  },

  // 选择并上传学信网截图
  chooseVerifyImage() {
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
    api.uploadRank(filePath)
      .then((res) => {
        if (res.auto_applied) {
          wx.showToast({ title: '识别成功：' + res.rank, icon: 'success' })
        } else if (res.need_review) {
          wx.showModal({
            title: '已提交更新申请',
            content: (res.rank ? 'AI 识别为 ' + res.rank + '。' : '') + '已提交段位更新申请，等待管理员审批。',
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
