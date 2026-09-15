/**
 * 队伍管理页
 * 功能：
 *   - 没队伍：创建队伍 + 浏览全部队伍（可申请加入）
 *   - 有队伍：查看成员、队长踢人、队长审核申请、退队/解散
 */
const api = require('../../utils/api.js')
const { showImageError } = require('../../utils/image-upload.js')
const { rankDisplay } = require('../../utils/rank.js')
Page({
  data: {
  loading: true,
    loadFailed: false,
    loggedIn: false,
    rankedMemberCount: 0,
    myTeam: null,       // 我的队伍（null=没队伍）
    teamName: '',       // 创建队伍输入
    creatingTeam: false,
    createTeamError: '',
    isCaptain: false,   // 我是不是队长
    members: [],        // 我的队伍成员
    applications: [],   // 待审核的入队申请（队长可见）
    teams: [],          // 全部队伍列表（没队伍时浏览）
    applyMessage: '',   // 申请留言
    // 人才市场
    showMarket: false,  // 是否显示人才市场
    marketLoading: false,
    marketError: false,
    marketMatches: [],  // 可选赛事列表
    freePlayers: [],    // 当前选中的赛事的自由人
    selectedMatchId: 0, // 选中的赛事id
    tab: 'mine',        // 当前 tab: mine=我的队伍, all=所有队伍, info=信息
    invitations: [],    // 我收到的入队邀请
    showInvites: false, // 是否显示邀请面板
    showTeamDetail: false, // 是否显示队伍详情弹层
    detailTeam: null,      // 详情中的队伍
    detailMembers: [],     // 详情中的成员
    rankApplications: []   // 我的段位申请
  },

  goRecruitment() { wx.navigateTo({ url: '/pages/recruitment/recruitment' }) },
  goMessages() { wx.navigateTo({ url: '/pages/messages/messages' }) },

  onLoad(options) {
    this._unloaded = false
    if (options && options.tab !== undefined && ['mine', 'all', 'info'].indexOf(options.tab) >= 0) {
      this.setData({ tab: options.tab })
    }
  },

  onUnload() {
    this._unloaded = true
    this._myTeamLoadSeq = (this._myTeamLoadSeq || 0) + 1
    this._refreshSeq = (this._refreshSeq || 0) + 1
  },

  // 每次进入页面刷新
  async onShow() {
    await this.refreshAll()
  },

  // 下拉刷新
  async onPullDownRefresh() {
    await this.refreshAll()
    if (this.data.loggedIn) await this.loadInvitations()
    wx.stopPullDownRefresh()
  },

  // 刷新所有数据
  async refreshAll() {
    const token = wx.getStorageSync('token')
    const refreshSeq = this._refreshSeq = (this._refreshSeq || 0) + 1
    const isCurrent = () => !this._unloaded && this._refreshSeq === refreshSeq && wx.getStorageSync('token') === token
    if (this._sessionToken !== undefined && this._sessionToken !== token) {
      this.setData({ teamName: '', createTeamError: '', creatingTeam: false })
    }
    this._sessionToken = token
    this.setData({ loggedIn: !!token, loading: true, loadFailed: false })
    if (!token) {
      this.setData({ myTeam: null, members: [], isCaptain: false, rankedMemberCount: 0, applications: [], loading: false })
      return
    }
    // 已登录才拉我的队伍；未登录允许先浏览队伍列表，不强制登录
    if (token) {
      await this.loadMyTeam()
    }
    if (!isCurrent()) return
    if (!this.data.myTeam) {
      await this.loadTeams()
    }
    if (isCurrent()) this.setData({ loading: false })
  },

  // 分享队伍（右上角转发 + 分享按钮都会触发）
  onShareAppMessage() {
    const team = this.data.myTeam
    if (team) {
      return {
        title: '【' + team.name + '】正在招募队员，快来加入！',
        path: '/pages/team/team'
      }
    }
    return {
      title: '北林CS2校赛报名系统',
      path: '/pages/index/index'
    }
  },

  // 加载我的队伍 + 成员
  async loadMyTeam() {
    const token = wx.getStorageSync('token')
    const loadSeq = this._myTeamLoadSeq = (this._myTeamLoadSeq || 0) + 1
    const isCurrent = () => !this._unloaded && this._myTeamLoadSeq === loadSeq && wx.getStorageSync('token') === token
    try {
      const team = await api.getMyTeam()
      if (!isCurrent()) return
      const me = team ? await api.getMe() : null
      if (!isCurrent()) return
      const userId = me ? me.id : null
      const isCaptain = !!team && team.captain_id === userId
      if (team) {
        delete team.captain_qq // 忽略旧后端的停用联系字段。
        // 队伍 logo 完整地址 + 首字徽章（无 logo 时用）
        if (team.logo && team.logo.indexOf('http') !== 0) {
          team.logo_full = api.BASE + team.logo
        } else {
          team.logo_full = team.logo || ''
        }
        team.logo_initial = team.name ? team.name.trim().charAt(0) : '?'
        const status = String(team.status || '').toLowerCase()
        team.status_text = { approved: '队伍已通过审核', pending: '队伍待审核', rejected: '队伍审核未通过' }[status] || '审核状态待确认'
        team.status_class = status
        // 详情接口不返回总评分，按服务端同一规则汇总真实成员评分。
        team.display_rating = (team.members || []).map(m => Number(m.rating) || 0).sort((a, b) => b - a).slice(0, 5).reduce((sum, rating) => sum + rating, 0)
        team.rating_tier = team.display_rating >= 400 ? 'S' : team.display_rating >= 300 ? 'A' : team.display_rating >= 200 ? 'B' : team.display_rating >= 100 ? 'C' : team.display_rating > 0 ? 'D' : '未定'
      }
      this.setData({ myTeam: team || null, isCaptain, loadFailed: false })
      if (team) {
        const members = (team.members || []).map((m, index) => {
          const item = Object.assign({}, m, {
            display_rank: rankDisplay(m.rank),
            display_name: m.nickname || m.game_id || '玩家' + m.user_id,
            score_text: m.rating == null ? '—' : m.rating,
            initial: (m.nickname || m.game_id || '玩家').charAt(0),
            roster_no: String(index + 1).padStart(2, '0')
          })
          item.avatar_full = item.avatar ? (item.avatar.startsWith('http') ? item.avatar : api.BASE + item.avatar) : ''
          return item
        })
        this.setData({
          isCaptain: team.captain_id === userId,
          members,
          rankedMemberCount: members.filter(m => !!m.rank).length
        })
        if (team.captain_id === userId) {
          await this.loadApplications(team.id, isCurrent)
        } else {
          this.setData({ applications: [] })
        }
      } else {
        this.setData({ members: [], isCaptain: false, rankedMemberCount: 0, applications: [] })
      }
    } catch (err) {
      if (!isCurrent()) return
      this.setData({ myTeam: null, members: [], isCaptain: false, rankedMemberCount: 0, applications: [], loadFailed: true })
    }
  },

  // 从 JWT 里解出 user_id（payload 是 base64）
  getUserIdFromToken() {
    try {
      const token = wx.getStorageSync('token')
      const payload = token.split('.')[1]
      const data = JSON.parse(decodeURIComponent(escape(atob(payload))))
      return data.sub
    } catch (err) {
      return null
    }
  },

  // 队长加载待审核申请
  async loadApplications(teamId, isCurrent) {
    try {
      const data = await api.getApplications(teamId)
      if (isCurrent && !isCurrent()) return
      this.setData({ applications: (data || []).map(a => Object.assign({}, a, { displayRank: rankDisplay(a.rank), avatarFull: a.avatar ? (/^https?:\/\//.test(a.avatar) ? a.avatar : api.BASE + a.avatar) : '' })) })
    } catch (err) {
      if (isCurrent && !isCurrent()) return
      this.setData({ applications: [] })
    }
  },

  // 打开人才市场：拉取所有赛事，默认选第一个
  async openMarket() {
    this.setData({ showMarket: true, marketLoading: true, marketError: false, freePlayers: [] })
    try {
      const matches = await api.getMatches()
      this.setData({ marketMatches: matches || [] })
      if (matches && matches.length > 0) {
        this.setData({ selectedMatchId: matches[0].id })
        await this.loadFreePlayers(matches[0].id)
      }
    } catch (err) {
      this.setData({ marketMatches: [], marketError: true })
    } finally {
      this.setData({ marketLoading: false })
    }
  },

  // 切换人才市场的赛事
  async onMatchChange(e) {
    const matchId = Number(e.currentTarget.dataset.id)
    this.setData({ selectedMatchId: matchId })
    await this.loadFreePlayers(matchId)
  },

  // 加载某赛事的自由人（已报名但无队伍）
  async loadFreePlayers(matchId) {
    this.setData({ marketLoading: true, marketError: false, freePlayers: [] })
    try {
      const data = await api.getTalentMarket(matchId)
      const players = (data || []).map(p => Object.assign({}, p, { display_rank: rankDisplay(p.rank), initial: (p.nickname || p.game_id || '玩家').charAt(0), score_text: p.individual_rating == null ? '—' : p.individual_rating }))
      this.setData({ freePlayers: players })
    } catch (err) {
      this.setData({ freePlayers: [], marketError: true })
    } finally {
      this.setData({ marketLoading: false })
    }
  },

  // 队长拉人入队（发邀请，需对方同意）
  async pullPlayer(e) {
    const userId = e.currentTarget.dataset.uid
    const name = e.currentTarget.dataset.name || '该玩家'
    wx.showModal({
      title: '确认邀请',
      content: `邀请 ${name} 加入你的队伍?`,
      success: async (res) => {
        if (!res.confirm) return
        try {
          await api.invitePlayer(this.data.myTeam.id, userId)
          wx.showToast({ title: '邀请已发送', icon: 'success' })
        } catch (err) {
          wx.showToast({ title: err.detail || '失败', icon: 'error' })
        }
      }
    })
  },

  // 按学号邀请入队（直接加入；若队伍已报名赛事，自动为该用户补报名）
  openRecruit() {
    const team = this.data.myTeam
    if (!team) return
    wx.showModal({
      title: '邀请入队',
      editable: true,
      placeholderText: '输入对方学号',
      content: '入队后若队伍已报名赛事，将自动为该用户补报名',
      success: async (res) => {
        if (!res.confirm || !res.content) return
        try {
          await api.recruitByStudent(team.id, res.content.trim())
          wx.showToast({ title: '已邀请', icon: 'success' })
          await this.loadMyTeam()
        } catch (err) {
          const msg = (err && err.detail) || '邀请失败'
          wx.showModal({ title: '提示', content: msg, showCancel: false })
        }
      }
    })
  },

  // 关闭人才市场
  closeMarket() {
    this.setData({ showMarket: false })
  },

  noop() {},
  goTeams() { wx.reLaunch({ url: '/pages/teams/teams' }) },
  goMatches() { wx.reLaunch({ url: '/pages/index/index' }) },
  goLogin() { wx.navigateTo({ url: '/pages/login/login' }) },

  // 加载全部队伍（没队伍时浏览用）
  async loadTeams() {
    try {
      const data = await api.getTeams()
      this.setData({ teams: data || [] })
    } catch (err) {
      this.setData({ teams: [] })
    }
  },

  // 查看队伍详情
  async viewTeam(e) {
    const id = e.currentTarget.dataset.id
    try {
      const team = await api.getTeam(id)
      const members = (team.members || []).map(m => {
        const item = Object.assign({}, m, { display_rank: rankDisplay(m.rank) })
        if (item.avatar && !item.avatar.startsWith('http')) {
          item.avatar_full = api.BASE + item.avatar
        }
        return item
      })
      this.setData({ showTeamDetail: true, detailTeam: team, detailMembers: members })
    } catch (err) {
      wx.showToast({ title: '加载失败', icon: 'error' })
    }
  },

  // 关闭队伍详情弹层
  closeTeamDetail() {
    this.setData({ showTeamDetail: false })
  },

  // === 创建队伍 ===
  onNameInput(e) {
    this.setData({ teamName: e.detail.value, createTeamError: '' })
  },

  async createTeam() {
    if (this._createPending || this.data.creatingTeam || this.data.myTeam) return
    const token = wx.getStorageSync('token')
    if (!token) { this.goLogin(); return }
    const name = this.data.teamName.trim()
    if (!name) {
      this.setData({ createTeamError: '请输入队伍名称' })
      return
    }
    const pending = {}
    this._createPending = pending
    const isCurrent = () => !this._unloaded && this._createPending === pending && wx.getStorageSync('token') === token && !this.data.myTeam
    this.setData({ creatingTeam: true, createTeamError: '' })
    try {
      const team = await api.createTeam(name)
      if (!isCurrent()) return
      if (team) delete team.captain_qq
      this.setData({ myTeam: team, teamName: '', creatingTeam: false })
      wx.showToast({ title: '创建成功', icon: 'success' })
      await this.refreshAll()
    } catch (err) {
      if (isCurrent()) this.setData({ createTeamError: (err && err.detail) || '创建失败，请稍后重试' })
    } finally {
      if (this._createPending === pending) {
        this._createPending = null
        if (!this._unloaded && wx.getStorageSync('token') === token) this.setData({ creatingTeam: false })
      }
    }
  },

  // === 队长上传/更换队伍Logo ===
  uploadLogo() {
    const team = this.data.myTeam
    if (!team || !this.data.isCaptain) return
    wx.chooseMedia({
      count: 1,
      mediaType: ['image'],
      sourceType: ['album', 'camera'],
      success: (res) => {
        const filePath = res && res.tempFiles && res.tempFiles[0] && res.tempFiles[0].tempFilePath
        if (!filePath) { showImageError(); return }
        wx.compressImage({
          src: filePath,
          quality: 80,
          success: (cr) => {
            const compressedPath = (cr && cr.tempFilePath) || filePath
            this.uploadLogoPath(compressedPath)
          },
          fail: () => this.uploadLogoPath(filePath)
        })
      },
      fail: showImageError
    })
  },
  async uploadLogoPath(filePath) {
    if (!this.data.myTeam) return
    try {
      await api.uploadTeamLogo(this.data.myTeam.id, filePath)
      wx.showToast({ title: 'Logo 已更新', icon: 'success' })
      await this.loadMyTeam()
    } catch (err) {
      wx.showToast({ title: err.detail || '上传失败', icon: 'none' })
    }
  },

  // === 退队 / 解散 ===
  async leaveTeam() {
    if (!this.data.myTeam) return
    wx.showModal({
      title: '确认退队',
      content: '确定要退出队伍吗?',
      success: async (res) => {
        if (!res.confirm) return
        try {
          await api.leaveTeam(this.data.myTeam.id)
          this.setData({ myTeam: null })
          wx.showToast({ title: '已退出', icon: 'success' })
          await this.refreshAll()
        } catch (err) {
          wx.showToast({ title: err.detail || '失败', icon: 'error' })
        }
      }
    })
  },

  async disbandTeam() {
    if (!this.data.myTeam) return
    wx.showModal({
      title: '解散队伍',
      content: '解散后所有成员将被移除，确定?',
      success: async (res) => {
        if (!res.confirm) return
        try {
          await api.disbandTeam(this.data.myTeam.id)
          this.setData({ myTeam: null })
          wx.showToast({ title: '已解散', icon: 'success' })
          await this.refreshAll()
        } catch (err) {
          wx.showToast({ title: err.detail || '失败', icon: 'error' })
        }
      }
    })
  },

  // === 队长踢人 ===
  async kickMember(e) {
    const userId = e.currentTarget.dataset.uid
    const name = e.currentTarget.dataset.name || '该成员'
    wx.showModal({
      title: '确认踢人',
      content: `确定把 ${name} 踢出队伍吗?`,
      success: async (res) => {
        if (!res.confirm) return
        try {
          await api.kickMember(this.data.myTeam.id, userId)
          wx.showToast({ title: '已踢出', icon: 'success' })
          await this.loadMyTeam()
        } catch (err) {
          wx.showToast({ title: err.detail || '失败', icon: 'error' })
        }
      }
    })
  },

  // === 队长审核申请 ===
  async approveApp(e) {
    const appId = e.currentTarget.dataset.id
    try {
      await api.approveApplication(appId)
      wx.showToast({ title: '已同意', icon: 'success' })
      await this.loadMyTeam()
    } catch (err) {
      wx.showToast({ title: err.detail || '操作失败', icon: 'error' })
    }
  },

  async rejectApp(e) {
    const appId = e.currentTarget.dataset.id
    try {
      await api.rejectApplication(appId)
      wx.showToast({ title: '已拒绝', icon: 'none' })
      await this.loadMyTeam()
    } catch (err) {
      wx.showToast({ title: err.detail || '操作失败', icon: 'error' })
    }
  },

  // === 申请加入（浏览队伍列表时） ===
  onMessageInput(e) {
    this.setData({ applyMessage: e.detail.value })
  },

  // 切换 tab
  switchTab(e) {
    this.setData({ tab: e.currentTarget.dataset.tab })
    if (e.currentTarget.dataset.tab === 'all') {
      this.loadTeams()
    }
  },

  // 拉取信息 tab 数据（入队邀请 + 段位申请）
  async loadInvitations() {
    try {
      const invites = await api.getMyInvitations()
      this.setData({ invitations: invites || [] })
    } catch (err) {
      this.setData({ invitations: [] })
    }
    try {
      const apps = await api.getMyRankApplications()
      this.setData({ rankApplications: apps || [] })
    } catch (err) {
      this.setData({ rankApplications: [] })
    }
  },

  // 同意邀请入队
  async acceptInv(e) {
    const id = e.currentTarget.dataset.id
    try {
      await api.acceptInvitation(id)
      wx.showToast({ title: '已入队', icon: 'success' })
      await this.loadInvitations()
      await this.refreshAll()
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

  async applyJoin(e) {
    if (this._applying) return
    const teamId = e.currentTarget.dataset.id
    const teamName = e.currentTarget.dataset.name || '该队伍'
    wx.showModal({
      title: '申请加入 ' + teamName,
      editable: true,
      placeholderText: '自我介绍（选填，最多 200 字）：位置、在线时段、组队期望',
      confirmText: '提交申请',
      success: async r => {
        if (!r.confirm || this._applying) return
        const message = (r.content || '').trim()
        if (message.length > 200) { wx.showToast({ title: '自我介绍最多 200 字', icon: 'none' }); return }
        this._applying = true
        try {
          await api.applyJoin(teamId, message)
          wx.showToast({ title: '已提交申请，等待队长处理', icon: 'none' })
        } catch (err) { wx.showToast({ title: err.detail || '申请失败', icon: 'none' }) }
        finally { this._applying = false }
      }
    })
  }
})
