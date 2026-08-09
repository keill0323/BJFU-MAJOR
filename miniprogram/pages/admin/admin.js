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
    // 队伍管理
    teams: [],
    // 赛事管理
    matches: [],
    // 创建赛事表单
    newMatch: { name: '', max_teams: 16, team_size: 5, description: '' }
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
      success: (res) => {
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
        } else {
          // D/C/B/A：选小段
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

  // ===== 认证审核 =====
  async loadVerifyList() {
    try {
      const data = await api.getVerifyList()
      const list = (data || []).map(u => Object.assign({}, u, {
        verify_image_full: u.verify_image
          ? (u.verify_image.startsWith('http') ? u.verify_image : api.BASE + u.verify_image)
          : ''
      }))
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

  // 认证驳回：清除截图并保持未认证
  async verifyReject(e) {
    const id = e.currentTarget.dataset.id
    wx.showModal({
      title: '驳回认证',
      content: '确定驳回该用户的认证申请吗？截图将被清除。',
      success: async (res) => {
        if (!res.confirm) return
        try {
          await api.adminUpdateUser(id, { is_verified: false, verify_image: '' })
          wx.showToast({ title: '已驳回', icon: 'none' })
          await this.loadVerifyList()
          await this.loadUsers(this.data.userKeyword)
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
      this.setData({ teams: data || [] })
    } catch (err) {
      this.setData({ teams: [] })
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

  async createMatch() {
    const m = this.data.newMatch
    if (!m.name) {
      wx.showToast({ title: '请输入赛事名', icon: 'none' })
      return
    }
    try {
      await api.createMatch(m)
      wx.showToast({ title: '创建成功', icon: 'success' })
      this.setData({ newMatch: { name: '', max_teams: 16, team_size: 5, description: '' } })
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
