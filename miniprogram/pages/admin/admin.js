/**
 * 管理后台页（admin/reviewer 共用）
 * 功能：用户管理 / 队伍审核 / 赛事管理
 * 入口：个人中心 → 管理后台（仅管理员/审核员可见）
 */
const api = require('../../utils/api.js')
const { rankDisplay } = require('../../utils/rank.js')

Page({
  data: {
    tab: 'users',         // 当前 tab: users/teams/matches
    user: null,           // 当前用户（用于权限校验）
    // 用户管理
    users: [],
    userKeyword: '',
    // 认证审核
    verifyUsers: [],
    // 段位更新申请
    rankApplications: [],
    // 队伍管理
    teams: [],
    pendingTeamCount: 0,
    // 详情弹层
    showUserDetailPanel: false,
    detailUser: null,
    showTeamDetailPanel: false,
    detailTeam: null,
    detailMembers: [],
    // 赛事管理
    matches: [],
    // 创建赛事表单
    newMatch: { name: '', max_teams: 16, team_size: 5, match_type: 'major', description: '' }
  },

  onLoad() {
    this.checkPermission()
  },

  // 进入页面先校验权限：仅 admin/reviewer 可进入（后端已有鉴权，前端兜底拦截）
  async checkPermission() {
    try {
      const user = await api.getMe()
      if (user && (user.role === 'admin' || user.role === 'reviewer')) {
        this.setData({ user })
        this.loadUsers()
        this.loadTeams()
        this.loadMatches()
        this.loadVerifyList()
        this.loadRankApplications()
      } else {
        wx.showModal({
          title: '权限不足',
          content: '只有管理员或审核员才能进入管理后台',
          showCancel: false,
          success: () => wx.navigateBack({ fail: () => wx.redirectTo({ url: '/pages/profile/profile' }) })
        })
      }
    } catch (err) {
      this.showErr(err, '权限校验失败')
      wx.navigateBack({ fail: () => wx.redirectTo({ url: '/pages/profile/profile' }) })
    }
  },

  // 切换 tab
  switchTab(e) {
    this.setData({ tab: e.currentTarget.dataset.tab })
  },

  // 下拉刷新：重新拉取当前各 tab 数据
  async onPullDownRefresh() {
    await this.loadUsers(this.data.userKeyword)
    await this.loadTeams()
    await this.loadMatches()
    await this.loadVerifyList()
    await this.loadRankApplications()
    wx.stopPullDownRefresh()
  },

  // 统一错误提示：toast 会截断长文本，改用弹窗完整显示
  showErr(err, fallback) {
    const msg = (err && err.detail) || fallback || '操作失败'
    wx.showModal({ title: '提示', content: msg, showCancel: false })
  },

  // ===== 用户管理 =====
  onUserKeyword(e) {
    this.setData({ userKeyword: e.detail.value })
  },

  async searchUsers() {
    await this.loadUsers(this.data.userKeyword)
  },

  async loadUsers(keyword) {
    try {
      const data = await api.adminListUsers(keyword)
      const users = (data || []).map(u => Object.assign({}, u, { display_rank: rankDisplay(u.rank) }))
      this.setData({ users })
    } catch (err) {
      this.showErr(err, '加载用户失败')
    }
  },

  // 点击用户卡片看详情
  showUserDetail(e) {
    const id = e.currentTarget.dataset.id
    const user = this.data.users.find(u => u.id == id)
    if (!user) return
    const u = Object.assign({}, user)
    if (u.verify_image && !u.verify_image.startsWith('http')) {
      u.verify_image_full = api.BASE + u.verify_image
    }
    if (u.avatar && !u.avatar.startsWith('http')) {
      u.avatar_full = api.BASE + u.avatar
    }
    this.setData({ detailUser: u, showUserDetailPanel: true })
  },

  closeUserDetail() {
    this.setData({ showUserDetailPanel: false })
  },

  // 点击队伍卡片看详情（拉成员列表）
  async showTeamDetail(e) {
    const id = e.currentTarget.dataset.id
    try {
      const team = await api.getTeam(id)
      const members = (team.members || []).map(m => Object.assign({}, m, { display_rank: rankDisplay(m.rank) }))
      this.setData({ detailTeam: team, detailMembers: members, showTeamDetailPanel: true })
    } catch (err) {
      this.showErr(err, '加载队伍详情失败')
    }
  },

  closeTeamDetail() {
    this.setData({ showTeamDetailPanel: false })
  },

  // 空处理器：阻止弹层内点击冒泡到遮罩
  noop() {},

  // 设置学号 + 认证
  setStudentId(e) {
    const uid = e.currentTarget.dataset.id
    wx.showModal({
      title: '设置学号',
      editable: true,
      placeholderText: '输入学号',
      success: async (res) => {
        if (!res.confirm || !res.content) return
        try {
          await api.adminUpdateUser(uid, { student_id: res.content, is_verified: true })
          wx.showToast({ title: '已设置并认证', icon: 'success' })
          await this.loadUsers(this.data.userKeyword)
        } catch (err) {
          this.showErr(err, '设置失败')
        }
      }
    })
  },

  // 设置段位（两步：选主段 D/C/B/A/S → 选小段或输入星数；水平分自动挂钩）
  setRank(e) {
    const uid = e.currentTarget.dataset.id
    const majors = ['D', 'C', 'B', 'A', 'S']
    wx.showActionSheet({
      itemList: majors,
      success: async (res) => {
        const major = majors[res.tapIndex]
        if (major === 'S') {
          // S 段：输入星数 1-50
          wx.showModal({
            title: 'S 段星数',
            editable: true,
            placeholderText: '输入星数 1-50',
            success: async (r2) => {
              if (!r2.confirm) return
              const stars = parseInt(r2.content, 10)
              if (isNaN(stars) || stars < 1 || stars > 50) {
                wx.showToast({ title: '星数需在 1-50 之间', icon: 'none' })
                return
              }
              try {
                await api.adminUpdateUser(uid, { rank: 'S' + stars })
                wx.showToast({ title: '段位已更新，水平分已同步', icon: 'success' })
                await this.loadUsers(this.data.userKeyword)
              } catch (err) {
                this.showErr(err, '设置失败')
              }
            }
          })
        } else if (major === 'D') {
          // D 段只有 D（无 D+/D++），直接设置
          try {
            await api.adminUpdateUser(uid, { rank: 'D' })
            wx.showToast({ title: '段位已更新，水平分已同步', icon: 'success' })
            await this.loadUsers(this.data.userKeyword)
          } catch (err) {
            this.showErr(err, '设置失败')
          }
        } else {
          // C/B/A：选小段
          const subs = [major, major + '+', major + '++']
          wx.showActionSheet({
            itemList: subs,
            success: async (r2) => {
              try {
                await api.adminUpdateUser(uid, { rank: subs[r2.tapIndex] })
                wx.showToast({ title: '段位已更新，水平分已同步', icon: 'success' })
                await this.loadUsers(this.data.userKeyword)
              } catch (err) {
                this.showErr(err, '设置失败')
              }
            }
          })
        }
      }
    })
  },

  // 修改用户权限
  setRole(e) {
    const uid = e.currentTarget.dataset.id
    wx.showActionSheet({
      itemList: ['设为普通用户', '设为审核员', '设为管理员'],
      success: async (res) => {
        const roles = ['user', 'reviewer', 'admin']
        try {
          await api.adminUpdateRole(uid, roles[res.tapIndex])
          wx.showToast({ title: '权限已更新', icon: 'success' })
          await this.loadUsers(this.data.userKeyword)
        } catch (err) {
          this.showErr(err, '设置失败')
        }
      }
    })
  },

  // 设置用户身份（新生/老登，研1/博1由管理员认证为新生）
  setIdentity(e) {
    const uid = e.currentTarget.dataset.id
    wx.showActionSheet({
      itemList: ['设为新生', '设为老登'],
      success: async (res) => {
        const identity = res.tapIndex === 0 ? 'new_student' : 'senior'
        try {
          await api.adminUpdateUser(uid, { identity })
          wx.showToast({ title: '身份已更新', icon: 'success' })
          await this.loadUsers(this.data.userKeyword)
        } catch (err) {
          this.showErr(err, '设置失败')
        }
      }
    })
  },

  // ===== 认证审核 =====
  async loadVerifyList() {
    try {
      const data = await api.getVerifyList()
      const AI_MAP = {
        auto_pass: { text: '自动通过', cls: 'ai-pass' },
        auto_reject: { text: '自动驳回', cls: 'ai-reject' },
        pending: { text: '初审待人工', cls: 'ai-pending' }
      }
      const list = (data || []).map(u => {
        const st = AI_MAP[u.ai_review_status] || { text: '', cls: '' }
        return Object.assign({}, u, {
          verify_image_full: u.verify_image
            ? (u.verify_image.startsWith('http') ? u.verify_image : api.BASE + u.verify_image)
            : '',
          ai_review_text: st.text,
          ai_review_class: st.cls,
          ai_review_conf_text: (u.ai_review_confidence != null)
            ? Math.round(u.ai_review_confidence * 100) + '%' : ''
        })
      })
      this.setData({ verifyUsers: list })
    } catch (err) {
      this.setData({ verifyUsers: [] })
    }
  },

  // 点击截图看大图
  previewVerify(e) {
    const url = e.currentTarget.dataset.url
    if (url) wx.previewImage({ urls: [url], current: url })
  },

  // 认证通过
  async verifyPass(e) {
    const id = e.currentTarget.dataset.id
    try {
      await api.adminUpdateUser(id, { is_verified: true })
      wx.showToast({ title: '已认证通过', icon: 'success' })
      await this.loadVerifyList()
      await this.loadUsers(this.data.userKeyword)
    } catch (err) {
      this.showErr(err, '操作失败')
    }
  },

  // 认证驳回：填写原因（用户可见），清除截图并保持未认证
  async verifyReject(e) {
    const id = e.currentTarget.dataset.id
    wx.showModal({
      title: '驳回认证',
      editable: true,
      placeholderText: '填写驳回原因（用户可见，可选）',
      success: async (res) => {
        if (!res.confirm) return
        const reason = (res.content || '').trim()
        try {
          await api.adminUpdateUser(id, { is_verified: false, verify_image: '', verify_reject_reason: reason || '' })
          wx.showToast({ title: '已驳回', icon: 'none' })
          await this.loadVerifyList()
          await this.loadUsers(this.data.userKeyword)
        } catch (err) {
          this.showErr(err, '操作失败')
        }
      }
    })
  },

  // ===== 段位更新申请 =====
  async loadRankApplications() {
    try {
      const data = await api.getRankApplications()
      const list = (data || []).map(a => Object.assign({}, a, {
        rank_image_full: a.rank_image
          ? (a.rank_image.startsWith('http') ? a.rank_image : api.BASE + a.rank_image)
          : '',
        ai_conf_text: (a.ai_confidence != null) ? Math.round(a.ai_confidence * 100) + '%' : '',
        display_ai_rank: a.ai_rank ? rankDisplay(a.ai_rank) : '-'
      }))
      this.setData({ rankApplications: list })
    } catch (err) {
      this.setData({ rankApplications: [] })
    }
  },

  // 预览段位截图
  previewRankShot(e) {
    const url = e.currentTarget.dataset.url
    if (url) wx.previewImage({ urls: [url], current: url })
  },

  // 通过段位更新申请（可修改 AI 识别段位）
  async approveRankApp(e) {
    const id = e.currentTarget.dataset.id
    const aiRank = e.currentTarget.dataset.rank
    wx.showModal({
      title: '确认段位',
      editable: true,
      placeholderText: '输入段位，如 A++ 或 S20',
      content: aiRank || '',
      success: async (res) => {
        if (!res.confirm) return
        const rank = (res.content || '').trim()
        if (!rank) return
        try {
          await api.approveRankApplication(id, rank)
          wx.showToast({ title: '已通过，段位已更新', icon: 'success' })
          await this.loadRankApplications()
          await this.loadUsers(this.data.userKeyword)
        } catch (err) {
          this.showErr(err, '操作失败')
        }
      }
    })
  },

  // 驳回段位更新申请（可填写驳回原因）
  async rejectRankApp(e) {
    const id = e.currentTarget.dataset.id
    wx.showModal({
      title: '驳回原因',
      editable: true,
      placeholderText: '填写驳回原因（可选）',
      success: async (res) => {
        if (!res.confirm) return
        const reason = (res.content || '').trim()
        try {
          await api.rejectRankApplication(id, reason)
          wx.showToast({ title: '已驳回', icon: 'none' })
          await this.loadRankApplications()
        } catch (err) {
          this.showErr(err, '操作失败')
        }
      }
    })
  },

  // ===== 队伍管理 =====
  async loadTeams() {
    try {
      const data = await api.getTeams()
      const pendingTeamCount = (data || []).filter(t => t.status === 'pending').length
      this.setData({ teams: data || [], pendingTeamCount })
    } catch (err) {
      this.setData({ teams: [], pendingTeamCount: 0 })
    }
  },

  async approveTeam(e) {
    const id = e.currentTarget.dataset.id
    try {
      await api.approveTeam(id)
      wx.showToast({ title: '已通过', icon: 'success' })
      await this.loadTeams()
    } catch (err) {
      this.showErr(err, '审核失败')
    }
  },

  async rejectTeam(e) {
    const id = e.currentTarget.dataset.id
    try {
      await api.rejectTeam(id)
      wx.showToast({ title: '已驳回', icon: 'none' })
      await this.loadTeams()
    } catch (err) {
      this.showErr(err, '驳回失败')
    }
  },

  async deleteTeam(e) {
    const id = e.currentTarget.dataset.id
    wx.showModal({
      title: '删除队伍',
      content: '确定删除吗?',
      success: async (res) => {
        if (!res.confirm) return
        try {
          await api.deleteTeam(id)
          wx.showToast({ title: '已删除', icon: 'success'})
          await this.loadTeams()
        } catch (err) {
          this.showErr(err, '删除失败')
        }
      }
    })
  },

  // ===== 赛事管理 =====
  async loadMatches() {
    try {
      const data = await api.getMatches()
      const matches = (data || []).map(m => Object.assign({}, m, { statusText: this.statusText(m.status) }))
      this.setData({ matches })
    } catch (err) {
      this.setData({ matches: [] })
    }
  },

  // 创建赛事表单
  onMatchName(e) {
    this.setData({ 'newMatch.name': e.detail.value })
  },
  onMatchMax(e) {
    this.setData({ 'newMatch.max_teams': Number(e.detail.value) })
  },
  onMatchSize(e) {
    this.setData({ 'newMatch.team_size': Number(e.detail.value) })
  },
  onMatchDesc(e) {
    this.setData({ 'newMatch.description': e.detail.value })
  },

  // 选择赛事类型
  onMatchType(e) {
    this.setData({ 'newMatch.match_type': e.currentTarget.dataset.type })
  },

  async createMatch() {
    const m = this.data.newMatch
    if (!m.name) {
      wx.showToast({ title: '请输入赛事名', icon: 'none' })
      return
    }
    try {
      await api.createMatch(m)
      wx.showToast({ title: '创建成功', icon: 'success' })
      this.setData({ newMatch: { name: '', max_teams: 16, team_size: 5, match_type: 'major', description: '' } })
      await this.loadMatches()
    } catch (err) {
      this.showErr(err, '创建失败')
    }
  },

  // 改赛事状态
  changeMatchStatus(e) {
    const id = e.currentTarget.dataset.id
    const name = e.currentTarget.dataset.name
    wx.showActionSheet({
      itemList: ['草稿', '报名中', '进行中', '已结束'],
      success: async (res) => {
        const statuses = ['draft', 'registering', 'in_progress', 'finished']
        try {
          await api.updateMatchStatus(id, statuses[res.tapIndex])
          wx.showToast({ title: `「${name}」状态已更新`, icon: 'success' })
          await this.loadMatches()
        } catch (err) {
          this.showErr(err, '更新失败')
        }
      }
    })
  },

  // 进入赛事详情页（点击赛事卡片）
  goMDetail(e) {
    const id = e.currentTarget.dataset.id
    wx.navigateTo({ url: '/pages/admin/mdetail/mdetail?matchId=' + id })
  },

  // 状态文字
  statusText(s) {
    const map = { draft: '草稿', registering: '报名中', in_progress: '进行中', finished: '已结束' }
    return map[s] || s
  }
})
