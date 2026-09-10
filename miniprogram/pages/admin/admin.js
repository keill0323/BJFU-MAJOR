/**
 * 管理后台页（admin/reviewer 共用）
 * 功能：用户管理 / 队伍审核 / 赛事管理
 * 入口：个人中心 → 管理后台（仅管理员/审核员可见）
 */
const api = require('../../utils/api.js')
const { rankDisplay } = require('../../utils/rank.js')

Page({
  data: {
    tab: 'overview',
    tabs: [
      { id: 'overview', label: '待办总览' }, { id: 'verify', label: '在校认证', countKey: 'verification_count' },
      { id: 'rankapps', label: '段位审核', countKey: 'rank_application_count' }, { id: 'teams', label: '队伍管理', countKey: 'team_count' },
      { id: 'matches', label: '赛事管理', countKey: 'registration_count' }, { id: 'users', label: '用户管理' }
    ],
    todos: null,
    todoCards: [
      { tab: 'verify', key: 'verification_count', title: '在校认证', subtitle: '审核在校证明', icon: 'shield' },
      { tab: 'rankapps', key: 'rank_application_count', title: '段位审核', subtitle: '确认选手段位', icon: 'honor' },
      { tab: 'teams', key: 'team_count', title: '队伍审核', subtitle: '审核新建队伍', icon: 'team' },
      { tab: 'matches', key: 'registration_count', title: '赛事报名', subtitle: '查看各赛事报名', icon: 'grid' }
    ],
    todoLoading: false,
    todoError: false,
    listLoading: {},
    listErrors: {},
    listReady: {},
    permissionLoading: true,
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
    newMatch: { name: '', max_teams: 16, team_size: 5, match_type: 'major', description: '' },
    creatingMatch: false,
    showCreateMatch: false,
    registrationStartDate: '',
    registrationStartTime: '',
    registrationEndDate: '',
    registrationEndTime: ''
  },

  onLoad(options = {}) {
    this._unloaded = false
    const tab = this.data.tabs.some(item => item.id === options.tab) ? options.tab : 'overview'
    this.setData({ tab })
    return this.checkPermission()
  },

  onShow() {
    // onLoad already starts the first request. Returning from a review/detail page refreshes current work.
    if (!this._hasShown) { this._hasShown = true; return }
    if (this.data.user) return this.refreshDashboard()
  },

  onUnload() {
    this._unloaded = true
  },

  // 进入页面先校验权限：仅 admin/reviewer 可进入（后端已有鉴权，前端兜底拦截）
  async checkPermission() {
    try {
      const user = await api.getMe()
      if (this._unloaded) return
      if (user && (user.role === 'admin' || user.role === 'reviewer')) {
        this.setData({ user, permissionLoading: false })
        await this.refreshDashboard()
      } else {
        wx.showModal({
          title: '权限不足',
          content: '只有管理员或审核员才能进入管理后台',
          showCancel: false,
          success: () => wx.navigateBack({ fail: () => wx.redirectTo({ url: '/pages/profile/profile' }) })
        })
      }
    } catch (err) {
      if (this._unloaded) return
      this.showErr(err, '权限校验失败')
      wx.navigateBack({ fail: () => wx.redirectTo({ url: '/pages/profile/profile' }) })
    } finally {
      if (!this._unloaded) this.setData({ permissionLoading: false })
    }
  },

  // 切换 tab
  switchTab(e) {
    const tab = e.currentTarget.dataset.tab
    if (!this.data.tabs.some(item => item.id === tab)) return
    this.setData({ tab })
    if (this.data.user) return this.loadActiveTab()
  },

  // 页面恢复和下拉刷新只更新计数及当前列表，避免首页下载全部用户与截图。
  refreshDashboard() {
    return Promise.all([this.loadTodos(true), this.loadActiveTab(true)])
  },

  async onPullDownRefresh() {
    try { if (this.data.user) await this.refreshDashboard() }
    finally { wx.stopPullDownRefresh() }
  },

  loadTodos(force = false) {
    // Forced refresh consults the shared store again: another page may have invalidated
    // the old request while this page was hidden. The store coalesces current API reads.
    if (this._todoRequest && !force) return this._todoRequest
    const version = (this._todoVersion || 0) + 1
    this._todoVersion = version
    this.setData({ todoLoading: true })
    const request = Promise.resolve().then(() => require('../../utils/admin-todos.js').refresh(force))
      .then(todos => {
        if (todos && !this._unloaded && version === (this._todoVersion || 0)) this.setData({ todos, todoError: false })
      }).catch(() => {
        // Keep the last successful numbers visible, but explicitly mark them as stale.
        if (!this._unloaded && version === (this._todoVersion || 0)) this.setData({ todoError: true })
      }).finally(() => {
        if (this._todoRequest === request) {
          this._todoRequest = null
          if (!this._unloaded) this.setData({ todoLoading: false })
        }
      })
    this._todoRequest = request
    return request
  },

  retryTodos() { return this.loadTodos(true) },

  loadActiveTab(force = false) {
    const tab = this.data.tab
    if (!force && this.data.listReady[tab]) return Promise.resolve()
    const loaders = { users: () => this.loadUsers(this.data.userKeyword), verify: () => this.loadVerifyList(), rankapps: () => this.loadRankApplications(), teams: () => this.loadTeams(), matches: () => this.loadMatches() }
    return loaders[tab] ? loaders[tab]() : Promise.resolve()
  },

  retryList() { return this.loadActiveTab(true) },

  loadList(tab, fetcher, field, transform, requestKey = '') {
    this._listRequests = this._listRequests || {}
    this._listVersions = this._listVersions || {}
    const previous = this._listRequests[tab]
    if (previous && previous.key === requestKey) return previous.promise
    const version = (this._listVersions[tab] || 0) + 1
    this._listVersions[tab] = version
    this.setData({ ['listLoading.' + tab]: true, ['listErrors.' + tab]: false })
    const promise = Promise.resolve().then(fetcher).then(data => {
      if (this._unloaded || this._listVersions[tab] !== version) return
      const values = transform ? transform(data || []) : data || []
      this.setData({ [field]: values, ['listReady.' + tab]: true })
      if (tab === 'teams') this.setData({ pendingTeamCount: values.filter(team => team.status === 'pending').length })
    }).catch(() => {
      if (!this._unloaded && this._listVersions[tab] === version) this.setData({ ['listErrors.' + tab]: true })
    }).finally(() => {
      if (this._listRequests[tab] && this._listRequests[tab].promise === promise) {
        delete this._listRequests[tab]
        if (!this._unloaded) this.setData({ ['listLoading.' + tab]: false })
      }
    })
    this._listRequests[tab] = { key: requestKey, promise }
    return promise
  },

  refreshAfterReview() {
    if (!this.data.user) return Promise.resolve()
    require('../../utils/admin-todos.js').invalidate()
    this._todoVersion = (this._todoVersion || 0) + 1
    this._todoRequest = null
    Object.keys(this._listVersions || {}).forEach(tab => { this._listVersions[tab]++ })
    this._listRequests = {}
    this.setData({ listReady: {}, listLoading: {} })
    return this.refreshDashboard()
  },

  toggleCreateMatch() {
    const showCreateMatch = !this.data.showCreateMatch
    this.setData({ showCreateMatch }, () => {
      if (showCreateMatch && wx.pageScrollTo) wx.pageScrollTo({ selector: '#create-match-form', duration: 240 })
    })
  },

  goChampion(e) {
    if (!this.data.user || this.data.user.role !== 'admin') return
    const id = e.currentTarget.dataset.id
    const match = this.data.matches.find(item => item.id == id)
    if (!match || match.status !== 'finished') return
    wx.navigateTo({ url: '/pages/admin/champion/champion?matchId=' + match.id })
  },

  openHonors() {
    if (!this.data.user || this.data.user.role !== 'admin') return
    this.setData({ tab: 'matches' })
    wx.showToast({ title: '请在已结束赛事中选择「补录冠军」', icon: 'none' })
    return this.loadActiveTab()
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

  loadUsers(keyword) {
    return this.loadList('users', () => api.adminListUsers(keyword), 'users', data =>
      data.map(u => Object.assign({}, u, { display_rank: rankDisplay(u.rank) })), keyword || '')
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
          await this.refreshAfterReview()
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
                await this.refreshAfterReview()
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
            await this.refreshAfterReview()
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
                await this.refreshAfterReview()
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
    if (!this.data.user || this.data.user.role !== 'admin') return
    const uid = e.currentTarget.dataset.id
    wx.showActionSheet({
      itemList: ['设为普通用户', '设为审核员', '设为管理员'],
      success: async (res) => {
        const roles = ['user', 'reviewer', 'admin']
        try {
          await api.adminUpdateRole(uid, roles[res.tapIndex])
          wx.showToast({ title: '权限已更新', icon: 'success' })
          await this.refreshAfterReview()
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
          await this.refreshAfterReview()
        } catch (err) {
          this.showErr(err, '设置失败')
        }
      }
    })
  },

  // ===== 认证审核 =====
  loadVerifyList() {
    return this.loadList('verify', () => api.getVerifyList(), 'verifyUsers', data => {
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
      return list
    })
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
      await this.refreshAfterReview()
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
          await this.refreshAfterReview()
        } catch (err) {
          this.showErr(err, '操作失败')
        }
      }
    })
  },

  // ===== 段位更新申请 =====
  loadRankApplications() {
    return this.loadList('rankapps', () => api.getRankApplications(), 'rankApplications', data => {
      const list = (data || []).map(a => Object.assign({}, a, {
        rank_image_full: a.rank_image
          ? (a.rank_image.startsWith('http') ? a.rank_image : api.BASE + a.rank_image)
          : '',
        ai_conf_text: (a.ai_confidence != null) ? Math.round(a.ai_confidence * 100) + '%' : '',
        display_ai_rank: a.ai_rank ? rankDisplay(a.ai_rank) : '-'
      }))
      return list
    })
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
          await this.refreshAfterReview()
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
          await this.refreshAfterReview()
        } catch (err) {
          this.showErr(err, '操作失败')
        }
      }
    })
  },

  // ===== 队伍管理 =====
  loadTeams() {
    return this.loadList('teams', () => api.getAdminTeams(), 'teams', data =>
      data.slice().sort((a, b) => (b.status === 'pending') - (a.status === 'pending')))
  },

  async approveTeam(e) {
    const id = e.currentTarget.dataset.id
    try {
      await api.approveTeam(id)
      wx.showToast({ title: '已通过', icon: 'success' })
      await this.refreshAfterReview()
    } catch (err) {
      this.showErr(err, '审核失败')
    }
  },

  async rejectTeam(e) {
    const id = e.currentTarget.dataset.id
    try {
      await api.rejectTeam(id)
      wx.showToast({ title: '已驳回', icon: 'none' })
      await this.refreshAfterReview()
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
          await this.refreshAfterReview()
        } catch (err) {
          this.showErr(err, '删除失败')
        }
      }
    })
  },

  // ===== 赛事管理 =====
  loadMatches() {
    return this.loadList('matches', () => api.getMatches(), 'matches', data =>
      data.map(m => Object.assign({}, m, { statusText: this.statusText(m.status) })))
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

  onRegistrationInput(e) {
    const field = e.currentTarget.dataset.field
    if (['registrationStartDate', 'registrationStartTime', 'registrationEndDate', 'registrationEndTime'].indexOf(field) >= 0) {
      this.setData({ [field]: e.detail.value })
    }
  },
  clearRegistrationLimit(e) {
    const side = e.currentTarget.dataset.side
    if (side === 'start') this.setData({ registrationStartDate: '', registrationStartTime: '' })
    if (side === 'end') this.setData({ registrationEndDate: '', registrationEndTime: '' })
  },

  // 日期选择器提供北京时间文本，不转换成设备时区或 UTC。
  registrationPayload() {
    const d = this.data
    if (!!d.registrationStartDate !== !!d.registrationStartTime || !!d.registrationEndDate !== !!d.registrationEndTime) {
      wx.showToast({ title: '请补全日期和时间，或清除该限制', icon: 'none' })
      return null
    }
    const start = d.registrationStartDate ? d.registrationStartDate + 'T' + d.registrationStartTime + ':00' : null
    const end = d.registrationEndDate ? d.registrationEndDate + 'T' + d.registrationEndTime + ':00' : null
    if (start && end && start >= end) {
      wx.showToast({ title: '报名开始时间需早于截止时间', icon: 'none' })
      return null
    }
    return { register_start: start, register_end: end }
  },

  async createMatch() {
    if (this.data.creatingMatch) return
    const m = this.data.newMatch
    if (!m.name) {
      wx.showToast({ title: '请输入赛事名', icon: 'none' })
      return
    }
    const registration = this.registrationPayload()
    if (!registration) return
    this.setData({ creatingMatch: true })
    try {
      await api.createMatch(Object.assign({}, m, registration))
      wx.showToast({ title: '创建成功', icon: 'success' })
      this.setData({
        showCreateMatch: false,
        newMatch: { name: '', max_teams: 16, team_size: 5, match_type: 'major', description: '' },
        registrationStartDate: '', registrationStartTime: '', registrationEndDate: '', registrationEndTime: ''
      })
      await this.loadMatches()
    } catch (err) {
      this.showErr(err, '创建失败')
    } finally {
      this.setData({ creatingMatch: false })
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
          await this.refreshAfterReview()
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
