/**
 * 个人中心页
 * 功能：查看登录状态、完整资料（含 user id）、修改资料、入队邀请、退出登录
 */
const api = require('../../utils/api.js')
const { rankDisplay } = require('../../utils/rank.js')

Page({
  data: {
    token: '',           // 登录令牌，空表示未登录
    user: null,          // 当前用户完整信息（含 id/学号/段位/评分/认证）
    isManager: false,    // 是否管理员/审核员（控制管理入口显示，wxml 只能用 data 字段）
    editMode: false,     // 是否处于编辑资料模式
    nickname: '',        // 新昵称输入
    gameId: '',          // 新游戏ID输入
    showInvites: false,  // 是否显示邀请面板
    invitations: [],     // 我收到的入队邀请
    uploading: false     // 是否正在上传截图
  },

  // 每次进入页面刷新用户信息
  onShow() {
    const token = wx.getStorageSync('token') || ''
    this.setData({ token })
    if (token) {
      this.loadUser()
      this.loadInvitations()
    }
  },

  // AI 审核状态 → 展示文本和样式
  AI_STATUS_MAP: {
    auto_pass: { text: 'AI 已自动通过', cls: 'ai-pass' },
    auto_reject: { text: 'AI 已自动驳回', cls: 'ai-reject' },
    pending: { text: 'AI 初审中，待人工复核', cls: 'ai-pending' }
  },

  // 拉取当前用户完整信息（后端 /api/auth/me）
  async loadUser() {
    try {
      const user = await api.getMe()
      if (user) user.display_rank = rankDisplay(user.rank)
      // 截图相对路径拼成完整 URL（image 组件需要）
      if (user && user.verify_image && !user.verify_image.startsWith('http')) {
        user.verify_image_full = api.BASE + user.verify_image
      }
      // AI 审核结果 → 生成显示文本（置信度只给管理后台看）
      if (user && user.ai_review_status) {
        const st = this.AI_STATUS_MAP[user.ai_review_status] || { text: '', cls: '' }
        user.ai_review_text = st.text
        user.ai_review_class = st.cls
      }
      this.setData({
        user,
        isManager: !!(user && (user.role === 'admin' || user.role === 'reviewer'))
      })
    } catch (err) {
      console.error('加载用户信息失败', err)
      this.setData({ isManager: false })
    }
  },

  // 判断当前用户是否管理员/审核员（点击入口时校验用）
  isManager() {
    const user = this.data.user
    return user && (user.role === 'admin' || user.role === 'reviewer')
  },

  // 拉取我收到的待处理邀请
  async loadInvitations() {
    try {
      const data = await api.getMyInvitations()
      this.setData({ invitations: data || [] })
    } catch (err) {
      this.setData({ invitations: [] })
    }
  },

  // 打开邀请面板
  openInvites() {
    this.setData({ showInvites: true })
    this.loadInvitations()
  },

  // 关闭邀请面板
  closeInvites() {
    this.setData({ showInvites: false })
  },

  // 跳转管理后台（admin/reviewer 共用）
  goAdmin() {
    // user 已加载且不是管理员/审核员：拦截（未加载完成时放行，由 admin 页兜底校验）
    const user = this.data.user
    if (user && user.role !== 'admin' && user.role !== 'reviewer') {
      wx.showToast({ title: '仅管理员/审核员可进入', icon: 'none' })
      return
    }
    wx.navigateTo({ url: '/pages/admin/admin' })
  },

  // 同意邀请入队
  async acceptInv(e) {
    const id = e.currentTarget.dataset.id
    try {
      await api.acceptInvitation(id)
      wx.showToast({ title: '已入队', icon: 'success' })
      await this.loadInvitations()
    } catch (err) {
      wx.showToast({ title: err.detail || '操作失败', icon: 'error' })
    }
  },

  // 拒绝邀请
  async rejectInv(e) {
    const id = e.currentTarget.dataset.id
    try {
      await api.rejectInvitation(id)
      wx.showToast({ title: '已拒绝', icon: 'none' })
      await this.loadInvitations()
    } catch (err) {
      wx.showToast({ title: err.detail || '操作失败', icon: 'error' })
    }
  },

  // 选择并上传学信网截图（仅未认证用户可见入口）
  // 真机照片通常好几 MB，直接上传容易失败，所以先压缩再上传
  chooseVerifyImage() {
    wx.chooseMedia({
      count: 1,
      mediaType: ['image'],
      sourceType: ['album', 'camera'],
      success: (res) => {
        // 注意：wx.chooseMedia 返回的字段是 tempFilePath（不是 filePath！）
        const filePath = res.tempFiles[0].tempFilePath
        console.log('【调试】原图路径:', filePath)
        wx.compressImage({
          src: filePath,
          quality: 80,          // 压缩质量 80%，肉眼几乎无差别
          success: (cr) => {
            // 压缩结果可能为空，为空就退回原图（避免传 undefined）
            const compressedPath = (cr && cr.tempFilePath) || filePath
            console.log('【调试】压缩后路径:', compressedPath)
            this.uploadWithPath(compressedPath)
          },
          fail: (err) => {
            console.error('【调试】压缩失败，改用原图:', err)
            this.uploadWithPath(filePath)   // 压缩失败就用原图
          }
        })
      }
    })
  },

  // 真正的上传动作（把指定路径的图片传上去）
  uploadWithPath(filePath) {
    this.setData({ uploading: true })
    api.uploadVerify(filePath)
      .then(() => {
        wx.showToast({ title: '上传成功，等待审核', icon: 'success' })
        return this.loadUser()
      })
      .catch(err => {
        wx.showToast({ title: err.detail || '上传失败', icon: 'none' })
      })
      .finally(() => {
        this.setData({ uploading: false })
      })
  },

  // 点击预览已上传的截图
  previewVerifyImage() {
    const url = this.data.user && this.data.user.verify_image_full
    if (url) {
      wx.previewImage({ urls: [url], current: url })
    }
  },

  // 点击进入编辑模式（回显当前昵称和游戏ID，而不是空）
  enableEdit() {
    const user = this.data.user || {}
    this.setData({
      editMode: true,
      nickname: user.nickname || '',
      gameId: user.game_id || ''
    })
  },

  // 昵称输入同步
  onNickInput(e) {
    this.setData({ nickname: e.detail.value })
  },

  // 游戏ID输入同步
  onGameIdInput(e) {
    this.setData({ gameId: e.detail.value })
  },

  // 保存修改资料
  async saveProfile() {
    try {
      await api.updateProfile({
        nickname: this.data.nickname,
        game_id: this.data.gameId   // 后端字段是下划线 game_id
      })
      wx.showToast({ title: '已更新', icon: 'success' })
      this.setData({ editMode: false })
      await this.loadUser()   // 保存后刷新用户信息
    } catch (err) {
      wx.showToast({ title: err.detail || '更新失败', icon: 'error' })
    }
  },

  // 退出登录：清 token + 跳登录页
  logout() {
    wx.showModal({
      title: '退出登录',
      content: '确定要退出吗?',
      success: (res) => {
        if (!res.confirm) return
        wx.removeStorageSync('token')
        wx.redirectTo({ url: '/pages/login/login' })
      }
    })
  }
})
