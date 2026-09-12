const api = require('../../utils/api.js')
const { rankDisplay } = require('../../utils/rank.js')

// 队伍水平分（0-500，为 top5 队员 individual_rating 之和）→ 分级徽章
function ratingTier(rating) {
  const r = rating || 0
  if (r >= 400) return { label: 'S', cls: 's' }
  if (r >= 300) return { label: 'A', cls: 'a' }
  if (r >= 200) return { label: 'B', cls: 'b' }
  if (r >= 100) return { label: 'C', cls: 'c' }
  if (r > 0)   return { label: 'D', cls: 'd' }
  return { label: '未定', cls: 'x' }
}

// 队伍徽章用首字（可退化为 '?'）
function teamInitial(name) {
  const t = (name || '').trim()
  return t ? t.charAt(0) : '?'
}

Page({
  data: {
    teams: [],        // 装饰后的全量队伍
    filtered: [],     // 搜索过滤结果
    keyword: '',
    total: 0,
    maxRating: 0,     // 最高队伍评分
    loading: true,
    loadFailed: false,
    // 详情弹层
    showTeamDetail: false,
    detailLoggedIn: false,
    detailTeam: null,
    detailMembers: []
  },

  onShow() {
    this.closeTeamDetail()
    this.loadTeams()
  },

  onHide() { this.closeTeamDetail() },
  onUnload() { this.closeTeamDetail() },

  onPullDownRefresh() {
    this.loadTeams().finally(() => wx.stopPullDownRefresh())
  },

  // 装饰：首字徽章 + 分级 + 人数 + logo 完整地址
  decorateTeam(t) {
    const tier = ratingTier(t.rating)
    return Object.assign({}, t, {
      initial: teamInitial(t.name),
      tierLabel: tier.label,
      tierCls: tier.cls,
      memberCount: t.member_count || 0,
      statusText: { approved: '已通过审核', pending: '待审核', rejected: '审核未通过' }[String(t.status || '').toLowerCase()] || '待确认',
      statusCls: String(t.status || '').toLowerCase(),
      captainLabel: t.captain_name || '玩家' + t.captain_id,
      logo_full: t.logo ? (t.logo.indexOf('http') === 0 ? t.logo : api.BASE + t.logo) : ''
    })
  },

  async loadTeams() {
    this.setData({ loading: true, loadFailed: false })
    try {
      const data = await api.getTeams()
      const teams = (data || []).map(t => this.decorateTeam(t))
      teams.sort((a, b) => (b.rating || 0) - (a.rating || 0))  // 默认按评分降序
      const maxRating = teams.length ? teams[0].rating || 0 : 0
      this.setData({ teams, total: teams.length, maxRating })
      this.applyFilter()
    } catch (err) {
      this.setData({ teams: [], total: 0, maxRating: 0, loadFailed: true })
      this.applyFilter()
    } finally {
      this.setData({ loading: false })
    }
  },

  onSearchInput(e) {
    this.setData({ keyword: e.detail.value })
    this.applyFilter()
  },

  clearSearch() {
    this.setData({ keyword: '' })
    this.applyFilter()
  },

  // 客户端过滤：按队名/队长模糊匹配
  applyFilter() {
    const kw = (this.data.keyword || '').trim().toLowerCase()
    const filtered = !kw
      ? this.data.teams
      : this.data.teams.filter(t =>
          (t.name || '').toLowerCase().indexOf(kw) >= 0 ||
          (t.captain_name || '').toLowerCase().indexOf(kw) >= 0
        )
    this.setData({ filtered })
  },

  async viewTeam(e) {
    const id = e.currentTarget.dataset.id
    const token = wx.getStorageSync('token')
    const generation = this._detailGeneration = (this._detailGeneration || 0) + 1
    const isCurrent = () => generation === this._detailGeneration && token === wx.getStorageSync('token')
    this.setData({ showTeamDetail: false, detailTeam: null, detailMembers: [] })
    try {
      const team = await api.getTeam(id)
      if (!isCurrent()) return
      if (!team || Number(team.id) !== Number(id)) throw new Error('Invalid team response')
      this._detailToken = token
      // TeamInfo 不含总评分和队长名，从本次返回的成员数据派生。
      const rating = (team.members || []).map(m => Number(m.rating) || 0).sort((a, b) => b - a).slice(0, 5).reduce((sum, value) => sum + value, 0)
      const captain = (team.members || []).find(m => m.user_id === team.captain_id)
      const tier = ratingTier(rating)
      const members = (team.members || []).map(m => {
        const item = Object.assign({}, m, { display_rank: rankDisplay(m.rank), initial: (m.nickname || m.game_id || '玩家').charAt(0), score_text: m.rating == null ? '—' : m.rating })
        item.avatar_full = item.avatar ? (item.avatar.startsWith('http') ? item.avatar : api.BASE + item.avatar) : ''
        return item
      })
      this.setData({
        showTeamDetail: true,
        detailLoggedIn: !!token,
        detailTeam: Object.assign({}, team, {
          captain_qq: token && typeof team.captain_qq === 'string' && /^[1-9][0-9]{4,11}$/.test(team.captain_qq.trim()) ? team.captain_qq.trim() : '',
          rating,
          captainLabel: captain ? (captain.nickname || captain.game_id || '玩家' + captain.user_id) : '玩家' + team.captain_id,
          statusText: { approved: '已通过审核', pending: '待审核', rejected: '审核未通过' }[String(team.status || '').toLowerCase()] || '待确认',
          statusCls: String(team.status || '').toLowerCase(),
          initial: teamInitial(team.name),
          tierLabel: tier.label,
          tierCls: tier.cls,
          logo_full: team.logo ? (team.logo.indexOf('http') === 0 ? team.logo : api.BASE + team.logo) : ''
        }),
        detailMembers: members
      })
    } catch (err) {
      if (!isCurrent()) return
      wx.showToast({ title: '加载失败', icon: 'error' })
    }
  },

  closeTeamDetail() {
    this._detailGeneration = (this._detailGeneration || 0) + 1
    this._detailToken = ''
    this.setData({ showTeamDetail: false, detailLoggedIn: false, detailTeam: null, detailMembers: [] })
  },

  copyCaptainQq() {
    const token = wx.getStorageSync('token')
    const team = this.data.detailTeam
    if (!token || token !== this._detailToken) { this.closeTeamDetail(); return }
    if (!this.data.showTeamDetail || !team || !/^[1-9][0-9]{4,11}$/.test(team.captain_qq || '')) return
    wx.setClipboardData({ data: team.captain_qq })
  },

  goLogin() { wx.navigateTo({ url: '/pages/login/login' }) },

  noop() {},   // 阻止面板内点击冒泡到遮罩
  goMyTeam() { wx.reLaunch({ url: '/pages/team/team' }) },

  // 申请加入（弹确认，二次确认防误触）
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
