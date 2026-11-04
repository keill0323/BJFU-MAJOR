const api = require('../../utils/api.js')
const { rankDisplay, rankBadge } = require('../../utils/rank.js')
const { showImageError } = require('../../utils/image-upload.js')
// 仅在本小程序进程中保留未决标记，跨同账号的页面实例复用；不进入文字助手或摘要。
const pendingUploads = new Map()

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

  onLoad(options = {}) {
    this._generation = 0
    this._focus = ['student', 'rank'].includes(options.focus) ? options.focus : ''
    this._fromAssistant = options.source === 'assistant'
  },

  onShow() {
    const token = wx.getStorageSync('token') || ''
    this._visible = true
    if (token !== this._token) {
      this.discardRankPicker()
      this._generation = (this._generation || 0) + 1
      this._selectionIntent = null
      this._activeUpload = null
      this._uncertainUpload = null
      this.setData({ user: null, uploading: false })
    }
    this._token = token
    this._uncertainUpload = pendingUploads.get(token) || null
    this.setData({ token })
    if (!token) {
      this.setData({ user: null })
      return
    }
    const rankUpload = this.resumeRankPicker()
    const userLoad = this.loadUser()
    return rankUpload ? Promise.all([userLoad, rankUpload]) : userLoad
  },
  onHide() {
    if (this._activeUpload) this._uncertainUpload = this._activeUpload
    this._activeUpload = null
    if (!this.currentRankPicker(this._rankPicker)) this._selectionIntent = null
    this._generation = (this._generation || 0) + 1
    this._visible = false
    this.setData({ uploading: false })
  },
  onUnload() { this.discardRankPicker(); this.onHide() },
  currentOperation(intent) {
    return !!intent && this._visible !== false && intent.generation === (this._generation || 0)
      && intent.token === (wx.getStorageSync('token') || '')
  },
  operationIntent(kind) {
    const token = wx.getStorageSync('token') || ''
    if (!token || this._visible === false) return null
    const user = this.data.user || {}
    return { token, generation: this._generation || 0, kind,
      oldMaterial: kind === 'student' ? user.verify_image : user.rank_image }
  },
  focusVerification() {
    if (!this._focus || this._focusDone || this._visible === false || !this.data.user || !wx.pageScrollTo) return
    this._focusDone = true
    wx.pageScrollTo({ selector: '#verify-' + this._focus, duration: 200 })
  },
  clearUploadPending(intent) {
    if (intent && pendingUploads.get(intent.token) === intent) pendingUploads.delete(intent.token)
    if (this._uncertainUpload === intent) this._uncertainUpload = null
  },
  confirmMaterialPurpose(kind, proceed) {
    if (this.data.uploading || this._selectionIntent || this._visible === false) return
    const intent = this.operationIntent(kind)
    if (!intent) { this.goLogin(); return }
    this._uncertainUpload = pendingUploads.get(intent.token) || null
    if (this._uncertainUpload && this._uncertainUpload.token === intent.token) {
      const uncertain = this._uncertainUpload
      wx.showModal({ title: '先核查上次上传结果', content: '上次上传结果尚未确认，请先查询当前状态，避免重复提交和识别。',
        confirmText: '查询状态', cancelText: '仍要重传', showCancel: true,
        success: result => {
          if (!this.currentOperation(intent)) return
          if (result.confirm) this.loadUser()
          else if (result.cancel) wx.showModal({ title: '确认重新上传',
            content: '旧请求可能仍在处理，重新提交可能重复消耗识别额度，且不会撤销旧请求。建议先查询当前状态；如已确认需要更换材料，可由你主动重新选图。',
            confirmText: '知情重传', cancelText: '暂不重传',
            success: choice => {
              if (!choice.confirm || !this.currentOperation(intent) || pendingUploads.get(intent.token) !== uncertain) return
              this.clearUploadPending(uncertain)
              this.confirmMaterialPurpose(kind, proceed)
            }
          })
        } })
      return
    }
    this._selectionIntent = intent
    if (!this._fromAssistant) { proceed(intent); return }
    const purpose = kind === 'student' ? '核对在校身份' : '核对历史游戏段位'
    wx.showModal({ title: '认证材料用途',
      content: '图片仅用于' + purpose + '，可能按现有审核配置发送给智谱视觉识别服务，并由管理员作必要复核；不会发送给文字赛事助手。请遮挡身份证号和证件照片。继续后仍需完成微信隐私授权并由你选图。',
      confirmText: '同意并选图', cancelText: '取消',
      success: result => {
        if (this._selectionIntent !== intent || !this.currentOperation(intent)) return
        if (result.confirm) proceed(intent)
        else this._selectionIntent = null
      },
      fail: () => { if (this._selectionIntent === intent) this._selectionIntent = null }
    })
  },

  async loadUser() {
    const intent = this.operationIntent('status'), request = this._loadRequest = {}
    if (!intent) return
    this.setData({ loadFailed: false })
    try {
      const user = await api.getMe()
      if (!this.currentOperation(intent) || this._loadRequest !== request) return
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
      const uncertain = this._uncertainUpload
      if (uncertain && uncertain.token === intent.token) {
        const material = user && (uncertain.kind === 'student' ? user.verify_image : user.rank_image)
        if ((material && material !== uncertain.oldMaterial) || (uncertain.kind === 'student' && user && user.is_verified)) this.clearUploadPending(uncertain)
        else if (uncertain.kind === 'rank') {
          // 待审段位保存在申请表，不一定更新/me中的有效段位或截图。
          // 仅核查本人状态，不重发图片或触发文字模型。
          try {
            const status = await api.getAssistantVerificationStatus()
            if (!this.currentOperation(intent) || this._loadRequest !== request || this._uncertainUpload !== uncertain) return
            if (status && status.data && status.data.rank && status.data.rank.application_status === 'pending') {
              this.clearUploadPending(uncertain)
              wx.showToast({ title: '材料已确认提交，等待段位审核', icon: 'none' })
            }
          } catch (_) { /* 仍未知，保留核查提示，不能自动重传。 */ }
        }
      }
      if (!this.currentOperation(intent) || this._loadRequest !== request) return
      if (wx.nextTick) wx.nextTick(() => { if (this.currentOperation(intent)) this.focusVerification() })
      else this.focusVerification()
    } catch (err) {
      if (!this.currentOperation(intent) || this._loadRequest !== request) return
      console.error('加载用户信息失败', err)
      this.setData({ loadFailed: true })
    }
  },

  goLogin() { wx.navigateTo({ url: '/pages/login/login' }) },
  goRules() { wx.navigateTo({ url: '/pages/rules/rules' }) },

  // 选择并上传学信网截图
  chooseVerifyImage() {
    return this.confirmMaterialPurpose('student', intent => this.selectVerifyImage(intent))
  },
  selectVerifyImage(intent) {
    wx.chooseMedia({
      count: 1,
      mediaType: ['image'],
      sourceType: ['album', 'camera'],
      success: (res) => {
        if (this._selectionIntent !== intent || !this.currentOperation(intent)) return
        const filePath = res && res.tempFiles && res.tempFiles[0] && res.tempFiles[0].tempFilePath
        if (!filePath) { this._selectionIntent = null; showImageError(); return }
        wx.compressImage({
          src: filePath,
          quality: 80,
          success: (cr) => {
            if (this._selectionIntent !== intent || !this.currentOperation(intent)) return
            const compressedPath = (cr && cr.tempFilePath) || filePath
            this._selectionIntent = null
            this.uploadWithPath(compressedPath, intent)
          },
          fail: () => {
            if (this._selectionIntent !== intent || !this.currentOperation(intent)) return
            this._selectionIntent = null
            this.uploadWithPath(filePath, intent)
          }
        })
      },
      fail: error => {
        if (!this.currentOperation(intent) || (this._selectionIntent && this._selectionIntent !== intent)) return
        this._selectionIntent = null
        showImageError(error)
      }
    })
  },
  uploadWithPath(filePath, intent = this.operationIntent('student')) {
    if (!this.currentOperation(intent) || this.data.uploading) return Promise.resolve()
    this._activeUpload = intent
    pendingUploads.set(intent.token, intent)
    this.setData({ uploading: true })
    return api.uploadVerify(filePath)
      .then(() => {
        this.clearUploadPending(intent)
        if (!this.currentOperation(intent) || this._activeUpload !== intent) return
        wx.showToast({ title: '上传成功，等待审核', icon: 'success' })
        return this.loadUser()
      })
      .catch(err => this.uploadFailed(err, intent))
      .finally(() => { if (this._activeUpload === intent) { this._activeUpload = null; if (this.currentOperation(intent)) this.setData({ uploading: false }) } })
  },

  // 选择并上传游戏段位截图（AI 识别段位）
  chooseRankImage() {
    return this.confirmMaterialPurpose('rank', intent => this.selectRankImage(intent))
  },
  currentRankPicker(picker) {
    if (!picker || this._rankPicker !== picker) return false
    if (picker.token !== (wx.getStorageSync('token') || '')) {
      this.discardRankPicker(picker)
      return false
    }
    return true
  },
  discardRankPicker(picker = this._rankPicker) {
    if (!picker || this._rankPicker !== picker) return
    this._rankPicker = null
    if (this._selectionIntent === picker.intent) this._selectionIntent = null
  },
  resumeRankPicker() {
    const picker = this._rankPicker
    if (!this.currentRankPicker(picker) || this._visible === false) return null
    if (picker.state === 'failed') {
      this.discardRankPicker(picker)
      showImageError(picker.error)
    } else {
      // 原生相册/相机可能暂时隐藏页面；只恢复这一次本人主动发起的段位选图。
      picker.intent = Object.assign({}, picker.intent, { generation: this._generation || 0 })
      this._selectionIntent = picker.intent
      if (picker.state === 'selected') {
        this.discardRankPicker(picker)
        return this.uploadRankWithPath(picker.filePath, picker.intent)
      }
    }
    return null
  },
  selectRankImage(intent) {
    if (this._selectionIntent !== intent || !this.currentOperation(intent)) return
    const picker = this._rankPicker = { token: intent.token, intent, state: 'selecting' }
    wx.chooseMedia({
      count: 1,
      mediaType: ['image'],
      sizeType: ['original'],
      sourceType: ['album', 'camera'],
      success: (res) => {
        if (!this.currentRankPicker(picker) || picker.state !== 'selecting') return
        const filePath = res && res.tempFiles && res.tempFiles[0] && res.tempFiles[0].tempFilePath
        picker.state = filePath ? 'selected' : 'failed'
        picker.filePath = filePath
        // 段位标签很小，保留原截图；体积仍由后端 10 MB 上限校验。
        this.resumeRankPicker()
      },
      fail: error => {
        if (!this.currentRankPicker(picker) || picker.state !== 'selecting') return
        picker.state = 'failed'
        picker.error = error
        if (this._selectionIntent === picker.intent) this._selectionIntent = null
        this.resumeRankPicker()
      }
    })
  },
  uploadRankWithPath(filePath, intent = this.operationIntent('rank')) {
    if (!this.currentOperation(intent) || this.data.uploading) return Promise.resolve()
    this._activeUpload = intent
    pendingUploads.set(intent.token, intent)
    this.setData({ uploading: true })
    return api.uploadRank(filePath)
      .then((res) => {
        this.clearUploadPending(intent)
        if (!this.currentOperation(intent) || this._activeUpload !== intent) return
        const feedbackMessage = res.rank_feedback && typeof res.rank_feedback.message === 'string'
          ? res.rank_feedback.message.trim() : ''
        const reviewReason = feedbackMessage || (typeof res.reason === 'string' ? res.reason.trim() : '')
        if (res.auto_applied) {
          wx.showToast({ title: '识别成功：' + res.rank, icon: 'success' })
        } else if (res.need_review) {
          wx.showModal({
            title: '已提交段位审核',
            content: [
              res.rank ? 'AI 识别为 ' + res.rank + '。' : '',
              reviewReason,
              '截图已提交，等待管理员审核，可在消息页查看审核结果。'
            ].filter(Boolean).join('\n'),
            showCancel: false
          })
        } else {
          const rankText = res.rank ? 'AI 识别为 ' + res.rank + '，但置信度不足。\n' : ''
          wx.showModal({
            title: 'AI 初审结果',
            content: rankText + (reviewReason || '请联系管理员手动设置段位'),
            showCancel: false
          })
        }
        return this.loadUser()
      })
      .catch(err => this.uploadFailed(err, intent))
      .finally(() => { if (this._activeUpload === intent) { this._activeUpload = null; if (this.currentOperation(intent)) this.setData({ uploading: false }) } })
  },
  uploadFailed(error, intent) {
    if (Number.isInteger(error && error.statusCode) && error.statusCode >= 400 && error.statusCode < 500) this.clearUploadPending(intent)
    if (!this.currentOperation(intent) || this._activeUpload !== intent) return
    if (!Number.isInteger(error && error.statusCode) || error.statusCode >= 500) {
      this._uncertainUpload = intent
      wx.showToast({ title: '上传结果尚未确认，正在查询状态', icon: 'none' })
      return this.loadUser()
    }
    wx.showToast({ title: error.detail || '上传失败', icon: 'none' })
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
