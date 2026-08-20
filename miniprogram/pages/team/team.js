/**
 * 队伍管理页
 * 功能：
 *   - 没队伍：创建队伍 + 浏览全部队伍（可申请加入）
 *   - 有队伍：查看成员、队长踢人、队长审核申请、退队/解散
 */
const api = require('../../utils/api.js')
const { rankDisplay } = require('../../utils/rank.js')

Page({
  data: {
    myTeam: null,       // 我的队伍（null=没队伍）
    teamName: '',       // 创建队伍输入
    isCaptain: false,   // 我是不是队长
    members: [],        // 我的队伍成员
    applications: [],   // 待审核的入队申请（队长可见）
    teams: [],          // 全部队伍列表（没队伍时浏览）
    applyMessage: '',   // 申请留言
    // 人才市场
    showMarket: false,  // 是否显示人才市场
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

  // 每次进入页面刷新
  async onShow() {
    await this.refreshAll()
    await this.loadInvitations()
  },

  // 刷新所有数据
  async refreshAll() {
    const token = wx.getStorageSync('token')
    if (!token) {
      wx.redirectTo({ url: '/pages/login/login' })
      return
    }
    await this.loadMyTeam()
    if (!this.data.myTeam) {
      await this.loadTeams()
    }
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
    try {
      const team = await api.getMyTeam()
      this.setData({ myTeam: team || null })
      if (team) {
        // 用 /me 接口拿当前用户 id（比解析 JWT 更可靠）
        const me = await api.getMe()
        const userId = me ? me.id : null
        const members = (team.members || []).map(m => {
          const item = Object.assign({}, m, { display_rank: rankDisplay(m.rank) })
          if (item.avatar && !item.avatar.startsWith('http')) {
            item.avatar_full = api.BASE + item.avatar
          }
          return item
        })
        this.setData({
          isCaptain: team.captain_id === userId,
          members
        })
        if (team.captain_id === userId) {
          await this.loadApplications(team.id)
        }
      }
    } catch (err) {
      this.setData({ myTeam: null })
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
  async loadApplications(teamId) {
    try {
      const data = await api.getApplications(teamId)
      this.setData({ applications: data || [] })
    } catch (err) {
      this.setData({ applications: [] })
    }
  },

  // 打开人才市场：拉取所有赛事，默认选第一个
  async openMarket() {
    this.setData({ showMarket: true })
    try {
      const matches = await api.getMatches()
      this.setData({ marketMatches: matches || [] })
      if (matches && matches.length > 0) {
        this.setData({ selectedMatchId: matches[0].id })
        await this.loadFreePlayers(matches[0].id)
      }
    } catch (err) {
      this.setData({ marketMatches: [] })
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
    try {
      const data = await api.getTalentMarket(matchId)
      const players = (data || []).map(p => Object.assign({}, p, { display_rank: rankDisplay(p.rank) }))
      this.setData({ freePlayers: players })
    } catch (err) {
      this.setData({ freePlayers: [] })
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
    this.setData({ teamName: e.detail.value })
  },

  async createTeam() {
    const name = this.data.teamName.trim()
    if (!name) {
      wx.showToast({ title: '请输入队名', icon: 'none' })
      return
    }
    try {
      const team = await api.createTeam(name)
      this.setData({ myTeam: team, teamName: '' })
      wx.showToast({ title: '创建成功', icon: 'success' })
      await this.refreshAll()
    } catch (err) {
      wx.showToast({ title: err.detail || '创建失败', icon: 'error' })
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

  // 拉取信息 tab 数据（入队邀请 + 段位申请）+ 更新 tab 红点
  async loadInvitations() {
    let hasInvite = false
    let hasRankApp = false
    try {
      const invites = await api.getMyInvitations()
      this.setData({ invitations: invites || [] })
      hasInvite = (invites || []).length > 0
    } catch (err) {
      this.setData({ invitations: [] })
    }
    try {
      const apps = await api.getMyRankApplications()
      this.setData({ rankApplications: apps || [] })
      hasRankApp = (apps || []).some(a => a.status === 'pending' || a.status === 'rejected')
    } catch (err) {
      this.setData({ rankApplications: [] })
    }
    if (hasInvite || hasRankApp) {
      wx.showTabBarRedDot({ index: 1 })
    } else {
      wx.hideTabBarRedDot({ index: 1 })
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
    const teamId = e.currentTarget.dataset.id
    const teamName = e.currentTarget.dataset.name
    try {
      await api.applyJoin(teamId, this.data.applyMessage)
      wx.showToast({ title: `已向 ${teamName} 申请`, icon: 'success' })
      this.setData({ applyMessage: '' })
    } catch (err) {
      wx.showToast({ title: err.detail || '申请失败', icon: 'error' })
    }
  }
})
