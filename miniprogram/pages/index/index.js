const api = require('../../utils/api.js')

Page({
  data: {
    matches: [], keyword: '', groups: [], total: 0, registeringCount: 0, inProgressCount: 0,
    activeFilter: 'all', loading: true, loadError: false, user: null,
    personalNotice: null, personalNoticeError: false, personalNoticeLoading: false,
    filters: [{ value: 'all', label: '全部' }, { value: 'registering', label: '报名中' }, { value: 'in_progress', label: '进行中' }, { value: 'finished', label: '已结束' }],
    shortcuts: [
      { title: '我的队伍', icon: 'team', url: '/pages/team/team' },
      { title: '招募大厅', icon: 'team', url: '/pages/recruitment/recruitment' },
      { title: '所有队伍', icon: 'grid', url: '/pages/teams/teams' },
      { title: '名人堂', icon: 'honor', url: '/pages/hall/hall' },
      { title: '消息', icon: 'mail', url: '/pages/messages/messages' },
      { title: '认证中心', icon: 'shield', url: '/pages/verify/verify' }
    ]
  },
  onLoad() { this.loadMatches() },
  onShow() {
    this.loadPersonalNotices()
    // 导航栏提供同一份用户信息，首页不额外请求 /me。
    const nav = this.selectComponent('#main-nav')
    if (nav) nav.loadUser()
  },
  onUserChange(e) { this.setData({ user: e.detail.user || null }) },
  onUnload() { this._noticeRequest = (this._noticeRequest || 0) + 1 },
  async loadPersonalNotices() {
    const id = this._noticeRequest = (this._noticeRequest || 0) + 1
    const token = wx.getStorageSync('token') || ''
    this.setData({ personalNotice: null, personalNoticeError: false, personalNoticeLoading: !!token })
    if (!token) return
    try {
      const summary = await api.getPersonalNoticeSummary()
      if (id === this._noticeRequest && token === wx.getStorageSync('token')) this.setData({ personalNotice: summary })
    } catch (err) {
      if (id === this._noticeRequest && token === wx.getStorageSync('token')) this.setData({ personalNoticeError: true })
    } finally {
      if (id === this._noticeRequest) this.setData({ personalNoticeLoading: false })
    }
  },
  goNotice(e) { wx.navigateTo({ url: '/pages/messages/messages?section=' + (e.currentTarget.dataset.section || '') }) },
  async onPullDownRefresh() {
    const nav = this.selectComponent('#main-nav')
    await Promise.all([this.loadMatches(), this.loadPersonalNotices(), nav ? nav.loadUser() : Promise.resolve()])
    wx.stopPullDownRefresh()
  },
  async loadMatches() {
    const requestId = (this._requestId || 0) + 1
    this._requestId = requestId
    const keyword = this.data.keyword.trim()
    this.setData({ loading: true, loadError: false })
    try {
      const list = keyword ? await api.searchMatches(keyword) : await api.getMatches()
      if (requestId === this._requestId) this.applyData(list)
    } catch (err) {
      if (requestId === this._requestId) this.setData({ loadError: true })
    } finally {
      if (requestId === this._requestId) this.setData({ loading: false })
    }
  },
  onSearchInput(e) { this.setData({ keyword: e.detail.value }) },
  onSearch() { return this.loadMatches() },
  clearSearch() { this.setData({ keyword: '', activeFilter: 'all' }); return this.loadMatches() },
  setFilter(e) {
    const activeFilter = e.currentTarget.dataset.value
    this.setData({ activeFilter, groups: this.groupMatches(this.data.matches, activeFilter) })
  },
  goMatch(e) { wx.navigateTo({ url: '/pages/match/match?id=' + e.currentTarget.dataset.id }) },
  goShortcut(e) {
    const url = e.currentTarget.dataset.url
    if (url === '/pages/hall/hall') wx.navigateTo({ url })
    else wx.reLaunch({ url })
  },
  goIdentity() { wx.reLaunch({ url: this.data.user ? '/pages/profile/profile' : '/pages/login/login' }) },
  showRules() { wx.navigateTo({ url: '/pages/rules/rules' }) },
  _decorate(m) {
    const statusText = { draft: '筹备中', registering: '报名中', in_progress: '进行中', finished: '已结束' }[m.status] || m.status
    const typeLabel = m.match_type === 'freshman' ? '新生赛' : '公开大赛'
    const max = m.max_teams || 16
    const progress = Math.max(0, Math.min(100, Math.round(((m.registered_count || 0) / max) * 100)))
    // 展示服务端提供的赛事本地日期，不依赖平台各异的 Date 字符串解析。
    const deadline = m.register_end ? String(m.register_end).replace('T', ' ').slice(5, 16).replace('-', '.') : ''
    const timing = m.status === 'registering' ? (deadline ? '报名截止 ' + deadline : '报名时间待公布')
      : m.status === 'in_progress' ? '分组、赛程与比分持续更新'
      : m.status === 'finished' ? '赛事已结束 · 查看比赛结果' : '赛事筹备中 · 敬请关注'
    const actionText = m.status === 'registering' ? '查看报名' : m.status === 'in_progress' ? '查看赛程' : '赛事详情'
    return Object.assign({}, m, { statusText, typeLabel, progress, timing, actionText })
  },
  groupMatches(list, filter) {
    const titles = { registering: '正在招募', in_progress: '正在开赛', draft: '即将登场', finished: '往期赛事' }
    return ['registering', 'in_progress', 'draft', 'finished']
      .filter(s => (!filter || filter === 'all' || s === filter) && list.some(m => m.status === s))
      .map(s => ({ status: s, title: titles[s], matches: list.filter(m => m.status === s) }))
  },
  applyData(data) {
    const list = (data || []).map(m => this._decorate(m))
    this.setData({
      matches: list, groups: this.groupMatches(list, this.data.activeFilter), total: list.length,
      registeringCount: list.filter(m => m.status === 'registering').length,
      inProgressCount: list.filter(m => m.status === 'in_progress').length
    })
  }
})
