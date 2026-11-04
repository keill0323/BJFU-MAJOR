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
    operationFeedback: null,
    rankReview: { status: 'unknown', pending: false, currentRank: '', pendingRank: '', reason: '', label: '' },
    rankStatusLoading: false,
    rankStatusError: '',
    rankCanSubmit: true,
    verifiedCount: 0,
    rankTiers: ['D', 'C', 'B', 'A', 'S'],
    rankTier: '',
    rankStars: null,
    rankProgress: 0
  },

  AI_STATUS_MAP: {
    auto_pass: { text: 'AI 已自动通过', cls: 'ai-pass' },
    auto_reject: { text: 'AI 初审未通过，待人工复核', cls: 'ai-reject' },
    pending: { text: '材料已提交，等待核对', cls: 'ai-pending' }
  },

  onLoad(options = {}) {
    this._generation = 0
    this._feedbackEpoch = 0
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
      this._rankApplicationKey = null
      this._pendingRankReceipt = null
      this.invalidateFeedbackRequests()
      this.setData({ user: null, uploading: false, operationFeedback: null,
        rankReview: { status: 'unknown', pending: false, currentRank: '', pendingRank: '', reason: '', label: '' },
        rankStatusError: '', rankCanSubmit: true })
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
    if (this._activeUpload) {
      this._uncertainUpload = this._activeUpload
      this.feedback(this._activeUpload.kind, 'unknown', '提交结果待确认', '页面已离开，材料可能仍在处理中。返回后请查询状态，不必重复上传。', 'refresh')
    } else if (!this.currentRankPicker(this._rankPicker) && this.data.operationFeedback && this.data.operationFeedback.state === 'selecting') this.setData({ operationFeedback: null })
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
      oldRank: user.rank || null,
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
  invalidateFeedbackRequests() {
    this._feedbackEpoch = (this._feedbackEpoch || 0) + 1
    this._rankStatusRequest = null
    this.setData({ rankStatusLoading: false })
  },
  feedback(kind, state, title, message, action = '', progress = null) {
    this.setData({ operationFeedback: { kind, state, title, message, action, progress } })
  },
  expiredLogin(error, intent, ownsRequest) {
    const storedToken = wx.getStorageSync('token') || ''
    if (!error || !(error.statusCode === 401 || error.unauthorized === true) || !ownsRequest
        || this._visible === false || intent.generation !== (this._generation || 0)
        || this._token !== intent.token || (storedToken && storedToken !== intent.token)) return false
    if (storedToken && wx.removeStorageSync) wx.removeStorageSync('token')
    this.invalidateFeedbackRequests()
    this._activeUpload = null; this._uncertainUpload = null; this._token = ''
    this._generation = (this._generation || 0) + 1
    this.setData({ token: '', user: null, uploading: false, rankStatusError: '', rankCanSubmit: true,
      rankReview: { status: 'unknown', pending: false, currentRank: '', pendingRank: '', reason: '', label: '' } })
    this.feedback(intent.kind === 'student' ? 'student' : 'rank', 'error', '登录已失效', '请重新登录后再提交认证材料。', 'login')
    return true
  },
  uploadProgress(intent, event) {
    if (!this.currentOperation(intent) || this._activeUpload !== intent) return
    const previous = this.data.operationFeedback
    if (!previous || previous.kind !== intent.kind || !['uploading', 'processing'].includes(previous.state)) return
    const value = event && event.progress
    if (typeof value !== 'number' || !Number.isFinite(value)) return
    const progress = Math.max(previous.progress || 0, Math.min(100, Math.max(0, Math.floor(value))))
    if (previous.state === 'processing' || progress === 100) {
      this.feedback(intent.kind, 'processing', intent.kind === 'rank' ? '正在识别段位截图' : '正在处理认证材料',
        '截图已上传，正在处理。完成后会在这里显示结果，请稍候。')
    } else this.feedback(intent.kind, 'uploading', '正在上传截图', '请保持网络连接，上传完成后会继续处理。', '', progress)
  },
  selectionFailed(kind, error) {
    const message = String(error && error.errMsg || '')
    if (/cancel/i.test(message)) {
      if (this.data.operationFeedback && this.data.operationFeedback.kind === kind && this.data.operationFeedback.state === 'selecting') this.setData({ operationFeedback: null })
    } else {
      this.feedback(kind, 'error', '未能选择图片', /privacy/i.test(message) ? '尚未完成隐私授权，请重新选择并按微信提示授权。'
        : /auth deny|auth denied|authorize|permission/i.test(message) ? '请检查微信和手机的相册、相机权限，开启后重新选择。' : '没有取得可用图片，请重新选择。', 'retry')
    }
    showImageError(error)
  },
  refreshVerificationStatus() {
    if (this.data.rankStatusLoading) return Promise.resolve()
    if (!this.operationIntent('status')) { this.goLogin(); return Promise.resolve() }
    this.setData({ rankStatusLoading: true, rankStatusError: '' })
    return this.loadUser()
  },
  async loadRankReview(intent, epoch, loadRequest) {
    const request = this._rankStatusRequest = {}
    const current = () => this.currentOperation(intent) && this._feedbackEpoch === epoch
      && this._rankStatusRequest === request && this._loadRequest === loadRequest
    this.setData({ rankStatusLoading: true, rankStatusError: '' })
    try {
      const result = await api.getAssistantVerificationStatus()
      if (!current()) return
      const rank = result && result.data && result.data.rank
      if (!rank || typeof rank.status !== 'string') throw { detail: '认证状态响应不完整' }
      const pending = rank.application_status === 'pending' || rank.status === 'pending_review'
      const rejected = rank.application_status === 'rejected' || ['rejected', 'update_rejected'].includes(rank.status)
      const applicationKey = String(rank.application_status || '') + ':' + String(rank.submitted_at || '')
      // 已收到本次提交的待审回执时，旧申请快照不能撤销这份回执。
      if (this._pendingRankReceipt && !pending && applicationKey === this._pendingRankReceipt.previousKey) return
      this._pendingRankReceipt = null
      this._rankApplicationKey = applicationKey
      const previousReview = this.data.rankReview
      const review = { status: pending ? 'pending_review' : rejected ? 'rejected' : rank.status === 'verified' ? 'verified' : rank.status === 'not_submitted' ? 'none' : 'unknown',
        pending, currentRank: rankDisplay(rank.current_rank || (this.data.user && this.data.user.rank)) || '',
        pendingRank: rank.pending_rank ? rankDisplay(rank.pending_rank) : '',
        label: typeof rank.label === 'string' ? rank.label : '', reason: typeof rank.reason === 'string' ? rank.reason : '' }
      this.setData({ rankReview: review, rankCanSubmit: !pending && rank.can_upload !== false })
      const uncertain = this._uncertainUpload
      if (pending) {
        if (uncertain && uncertain.kind === 'rank' && uncertain.token === intent.token) {
          this.clearUploadPending(uncertain)
          wx.showToast({ title: '材料已确认提交，等待段位审核', icon: 'none' })
        }
        const feedback = this.data.operationFeedback
        if ((!feedback || feedback.kind === 'rank') && (!this.data.uploading || (feedback && ['pending', 'unknown'].includes(feedback.state)))) {
          this.feedback('rank', 'pending', '段位申请已提交', review.reason || '已收到截图，等待管理员审核。审核通过前当前有效段位保持不变。', 'refresh')
        }
      } else if (uncertain && uncertain.kind === 'rank' && uncertain.token === intent.token) {
        const material = this.data.user && this.data.user.rank_image
        if (review.status === 'verified' && material && material !== uncertain.oldMaterial) {
          this.clearUploadPending(uncertain)
          this.feedback('rank', 'success', '段位资料已更新', '已核查到更新后的认证资料，当前有效段位：' + review.currentRank + '。')
        } else this.feedback('rank', 'unknown', '提交结果仍待确认', '目前查到的有效段位不能证明刚才的上传已处理完成。可稍后再查询，请勿重复提交。', 'refresh')
      } else if (previousReview && previousReview.pending && (!this.data.operationFeedback || this.data.operationFeedback.kind === 'rank')) {
        if (review.status === 'verified') this.feedback('rank', 'success', '段位审核已通过', '当前有效段位：' + review.currentRank + '。')
        else if (rejected) this.feedback('rank', 'error', '段位申请未通过', review.reason || '请核对截图要求后重新提交。', 'retry')
      }
    } catch (error) {
      if (this.expiredLogin(error, intent, this._rankStatusRequest === request && this._loadRequest === loadRequest && this._feedbackEpoch === epoch)) return
      if (!current()) return
      const detail = Number(error && error.statusCode) === 404 ? '段位申请状态查询暂不可用，请稍后刷新。'
        : typeof (error && error.detail) === 'string' ? error.detail : '段位申请状态暂时无法查询，请稍后重试。'
      this.setData({ rankStatusError: detail })
    } finally { if (current()) this.setData({ rankStatusLoading: false }) }
  },
  confirmMaterialPurpose(kind, proceed) {
    if (this.data.uploading || this._selectionIntent || this._visible === false) return
    const intent = this.operationIntent(kind)
    if (!intent) { this.goLogin(); return }
    if (kind === 'rank' && this.data.rankCanSubmit === false) return
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
    const epoch = this._feedbackEpoch || 0
    const current = () => this.currentOperation(intent) && this._loadRequest === request && this._feedbackEpoch === epoch
    this.setData({ loadFailed: false })
    try {
      const user = await api.getMe()
      if (!current()) return
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
        if (uncertain.kind === 'student' && ((material && material !== uncertain.oldMaterial) || (user && user.is_verified))) this.clearUploadPending(uncertain)
      }
      const feedback = this.data.operationFeedback
      if (feedback && feedback.kind === 'student' && ['pending', 'unknown'].includes(feedback.state) && !this._uncertainUpload) {
        if (user && user.is_verified) this.feedback('student', 'success', '在校身份已确认', '当前账号已通过在校认证。')
        else if (user && user.verify_reject_reason) this.feedback('student', 'error', '在校认证未通过', user.verify_reject_reason, 'retry')
        else if (user && user.verify_image) this.feedback('student', 'pending', '认证材料已收到', '材料正在按原流程核对，尚未通过认证；可刷新状态或等待管理员处理。', 'refresh')
      }
      if (!current()) return
      if (wx.nextTick) wx.nextTick(() => { if (this.currentOperation(intent)) this.focusVerification() })
      else this.focusVerification()
      const working = this.data.uploading && this.data.operationFeedback && ['uploading', 'processing'].includes(this.data.operationFeedback.state)
      if (!working) await this.loadRankReview(intent, epoch, request)
    } catch (err) {
      if (this.expiredLogin(err, intent, this._loadRequest === request && this._feedbackEpoch === epoch)) return
      if (!current()) return
      console.error('加载用户信息失败', err)
      this.setData({ loadFailed: true, rankStatusLoading: false, rankStatusError: '认证资料暂时无法刷新，请稍后重试。' })
    }
  },

  goLogin() { wx.navigateTo({ url: '/pages/login/login' }) },
  goRules() { wx.navigateTo({ url: '/pages/rules/rules' }) },

  // 选择并上传学信网截图
  chooseVerifyImage() {
    return this.confirmMaterialPurpose('student', intent => this.selectVerifyImage(intent))
  },
  selectVerifyImage(intent) {
    this.feedback('student', 'selecting', '正在选择认证图片', '请从相册选择截图，或拍摄清晰的认证材料。')
    wx.chooseMedia({
      count: 1,
      mediaType: ['image'],
      sourceType: ['album', 'camera'],
      success: (res) => {
        if (this._selectionIntent !== intent || !this.currentOperation(intent)) return
        const filePath = res && res.tempFiles && res.tempFiles[0] && res.tempFiles[0].tempFilePath
        if (!filePath) { this._selectionIntent = null; this.selectionFailed('student'); return }
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
        this.selectionFailed('student', error)
      }
    })
  },
  uploadWithPath(filePath, intent = this.operationIntent('student')) {
    if (!this.currentOperation(intent) || this.data.uploading) return Promise.resolve()
    this.invalidateFeedbackRequests()
    this._activeUpload = intent
    pendingUploads.set(intent.token, intent)
    this.setData({ uploading: true })
    this.feedback('student', 'uploading', '正在上传认证截图', '请保持网络连接，上传完成后会继续处理。', '', 0)
    return api.uploadVerify(filePath, event => this.uploadProgress(intent, event))
      .then(() => {
        this.clearUploadPending(intent)
        if (!this.currentOperation(intent) || this._activeUpload !== intent) return
        this.invalidateFeedbackRequests()
        this.feedback('student', 'pending', '认证材料已提交', '材料已收到，正在按原流程核对；是否通过请以认证状态为准。', 'refresh')
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
      this.selectionFailed('rank', picker.error)
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
    this.feedback('rank', 'selecting', '正在选择段位截图', '请从相册选择包含历史段位信息的 PC 原截图。')
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
    if (!this.currentOperation(intent) || this.data.uploading || this.data.rankCanSubmit === false) return Promise.resolve()
    this.invalidateFeedbackRequests()
    this._activeUpload = intent
    pendingUploads.set(intent.token, intent)
    this.setData({ uploading: true })
    this.feedback('rank', 'uploading', '正在上传段位截图', '请保持网络连接，上传完成后会继续识别。', '', 0)
    return api.uploadRank(filePath, event => this.uploadProgress(intent, event))
      .then((res) => {
        if (!this.currentOperation(intent) || this._activeUpload !== intent) return
        this.invalidateFeedbackRequests()
        const feedbackMessage = res.rank_feedback && typeof res.rank_feedback.message === 'string'
          ? res.rank_feedback.message.trim() : ''
        const reviewReason = feedbackMessage || (typeof res.reason === 'string' ? res.reason.trim() : '')
        if (res.auto_applied === true) {
          this.clearUploadPending(intent)
          this._pendingRankReceipt = null
          this.feedback('rank', 'success', '段位认证已更新', '结果已生效，正在刷新当前有效段位。')
          wx.showToast({ title: '识别成功：' + res.rank, icon: 'success' })
        } else if (res.need_review === true) {
          this.clearUploadPending(intent)
          this._pendingRankReceipt = { previousKey: this._rankApplicationKey }
          this.setData({ rankCanSubmit: false, rankReview: {
            status: 'pending_review', pending: true,
            currentRank: rankDisplay(this.data.user && this.data.user.rank) || '',
            pendingRank: res.rank ? rankDisplay(res.rank) : '',
            label: '段位申请已提交，等待审核', reason: reviewReason || '截图已收到，正在等待管理员审核。'
          } })
          this.feedback('rank', 'pending', '段位申请已提交', reviewReason || '截图已收到，等待管理员审核；新的识别候选尚未生效。', 'refresh')
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
          this._uncertainUpload = intent
          this.feedback('rank', 'unknown', '认证结果待确认', reviewReason || '本次响应没有明确的生效或待审结果，请查询最新状态。', 'refresh')
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
    if (this.expiredLogin(error, intent, this._activeUpload === intent)) return
    if (!this.currentOperation(intent) || this._activeUpload !== intent) return
    this.invalidateFeedbackRequests()
    if (!Number.isInteger(error && error.statusCode) || error.statusCode >= 500) {
      this._uncertainUpload = intent
      this.feedback(intent.kind, 'unknown', '提交结果待确认', '未收到明确的处理结果，材料可能仍在处理。正在核查当前状态，不会自动重复上传。', 'refresh')
      wx.showToast({ title: '上传结果尚未确认，正在查询状态', icon: 'none' })
      return this.loadUser()
    }
    this.feedback(intent.kind, 'error', '本次提交未完成', typeof error.detail === 'string' ? error.detail : '请检查图片后重新提交。', 'retry')
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
