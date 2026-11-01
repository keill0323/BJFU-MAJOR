const api = require('../../utils/api.js')
const { rankDisplay } = require('../../utils/rank.js')

function wechatSubscriptions(scene) {
  try { const flow = require('../../utils/wechat-subscriptions.js'); return scene ? flow.prompt(scene) : flow.prefetch() }
  catch (_) { return Promise.resolve({ status: 'unavailable' }) }
}

Page({
  data: { tab: 'players', players: [], playersFailed: false, playersMore: false, playersCursor: null,
    keyword: '', query: '', inviting: null, expandedPlayerId: null, expandedPostId: null, appliedTeamIds: [],
    rankFilter: '', identityFilter: '', rankIndex: 0, identityIndex: 0,
    rankOptions: [{ value: '', label: '全部段位' },
      ...['D', 'C', 'C+', 'C++', 'B', 'B+', 'B++', 'A', 'A+', 'A++'].map(value => ({ value, label: value })),
      { value: 's', label: '普通 S · 0–9 星' }, { value: 's_gold', label: '金 S · 10–24 星' },
      { value: 's_diamond', label: '钻 S · 25–49 星' }, { value: 's_demon', label: '魔王 S · 50 星' },
      { value: 'unranked', label: '段位待认证' }],
    identityOptions: [{ value: '', label: '全部身份' }, { value: 'new_student', label: '新生' },
      { value: 'senior', label: '老生' }, { value: 'unknown', label: '身份待确认' }],
    playersLoading: true, playersLoadingMore: false, playersError: '',
    posts: [], mine: null, myTeam: null, token: '', loading: true, failed: false, accountFailed: false,
    more: false, cursor: null, loadingMore: false, editing: false, draft: '', saving: false, applying: null },
  onLoad(options = {}) {
    if (['players', 'posts', 'publish'].includes(options.tab)) this.setData({ tab: options.tab === 'publish' ? 'posts' : options.tab })
    this._openPublish = options.tab === 'publish'
  },
  onShow() { this._chatVisible = true; this._chatGeneration = (this._chatGeneration || 0) + 1; wechatSubscriptions().catch(() => {}); this.load() },
  onHide() { this.stopContact() },
  onUnload() { this.stopContact(); this._requestId = (this._requestId || 0) + 1; this._playersRequestId = (this._playersRequestId || 0) + 1 },
  onPullDownRefresh() { return this.load().finally(() => wx.stopPullDownRefresh()) },
  onReachBottom() { return this.loadMore() },
  current(id, token) { return id === this._requestId && token === (wx.getStorageSync('token') || '') },
  stopContact() { this._chatVisible = false; this._chatGeneration = (this._chatGeneration || 0) + 1; this._contactAttempt = null },
  contactPlayer(e) {
    const id = Number(e.currentTarget.dataset.id)
    if (!this.data.players.some(player => player.id === id)) return
    return this.startContact(id, 'user', false)
  },
  contactTeam(e) {
    const id = Number(e.currentTarget.dataset.id)
    if (!this.data.posts.some(post => post.team_id === id)) return
    return this.startContact(id, 'team', true)
  },
  async startContact(id, source, card) {
    if (!Number.isSafeInteger(id) || id <= 0 || this._contactAttempt) return
    const generation = this._chatGeneration, token = wx.getStorageSync('token') || ''
    const attempt = this._contactAttempt = {}
    const isCurrent = () => this._chatVisible && generation === this._chatGeneration && token === (wx.getStorageSync('token') || '')
    try {
      await wechatSubscriptions(this.data.mine && this.data.mine.can_publish && source === 'user' ? 'captain_recruitment' : 'personal_registration').catch(() => {})
      if (!isCurrent()) return
      await require('../../utils/chat-client.js').contact(id, source, card, isCurrent)
    }
    finally { if (this._contactAttempt === attempt) this._contactAttempt = null }
  },
  decorate(items) { return items.map(p => Object.assign({}, p, {
    applied: this.data.appliedTeamIds.includes(Number(p.team_id)),
    descriptionLong: String(p.content || '').length > 60 || String(p.content || '').split('\n').length > 3,
    initial: (p.team_name || '队').slice(0, 1), timeText: String(p.updated_at || '').replace('T', ' ').slice(0, 16),
    logoFull: p.logo ? (/^https?:\/\//.test(p.logo) ? p.logo : api.BASE + p.logo) : ''
  })) },
  decoratePlayers(items) { return items.map(p => Object.assign({}, p, {
    descriptionLong: String(p.user_description || '').length > 60 || String(p.user_description || '').split('\n').length > 3,
    displayName: p.nickname || p.game_id || '选手' + p.id, displayRank: rankDisplay(p.rank) || '段位待认证',
    identityText: !p.is_verified ? '身份待确认 · 学籍未认证' : p.identity === 'new_student' ? '新生' : p.identity === 'senior' ? '老生' : '新老生身份待确认',
    initial: (p.nickname || p.game_id || '选手').slice(0, 1),
    avatarFull: p.avatar ? (/^https?:\/\//.test(p.avatar) ? p.avatar : api.BASE + p.avatar) : ''
  })) },
  switchTab(e) { if (['players', 'posts'].includes(e.currentTarget.dataset.tab)) this.setData({ tab: e.currentTarget.dataset.tab }) },
  toggleDescription(e) {
    const { kind, id } = e.currentTarget.dataset
    const field = kind === 'player' ? 'expandedPlayerId' : kind === 'post' ? 'expandedPostId' : ''
    if (field) this.setData({ [field]: this.data[field] === Number(id) ? null : Number(id) })
  },
  onKeyword(e) { this.setData({ keyword: e.detail.value }) },
  searchPlayers() { this.setData({ query: this.data.keyword.trim() }); return this.loadPlayers() },
  onRankFilter(e) { return this.changePlayerFilter('rank', e.detail.value) },
  onIdentityFilter(e) { return this.changePlayerFilter('identity', e.detail.value) },
  changePlayerFilter(kind, value) {
    const index = Number(value), option = this.data[kind + 'Options'][index]
    if (!Number.isInteger(index) || !option || option.value === this.data[kind + 'Filter']) return
    this.setData({ [kind + 'Index']: index, [kind + 'Filter']: option.value })
    return this.loadPlayers()
  },
  clearPlayerFilters() {
    if (!this.data.rankFilter && !this.data.identityFilter) return
    this.setData({ rankFilter: '', identityFilter: '', rankIndex: 0, identityIndex: 0 })
    return this.loadPlayers()
  },
  playerFilters() { return { rank: this.data.rankFilter, identity: this.data.identityFilter } },
  playersCurrent(id, token) { return id === this._playersRequestId && token === (wx.getStorageSync('token') || '') },
  checkPlayerFilters(page, filters) {
    // 旧接口会忽略未知查询参数，不能把未筛选结果标成筛选成功。
    if ((filters.rank || filters.identity) && (!page.filters || page.filters.rank !== filters.rank || page.filters.identity !== filters.identity)) {
      throw { detail: '当前服务器尚未支持选手筛选，请更新后端后重试' }
    }
  },
  async loadPlayers() {
    const id = this._playersRequestId = (this._playersRequestId || 0) + 1
    const token = this._playersToken = wx.getStorageSync('token') || ''
    const query = this.data.query, filters = this.playerFilters()
    this.setData({ players: [], playersMore: false, playersCursor: null, playersFailed: false, playersError: '', playersLoading: true, playersLoadingMore: false })
    try {
      const page = await api.getRecruitmentPlayers(null, query, filters)
      if (!this.playersCurrent(id, token)) return
      this.checkPlayerFilters(page, filters)
      this.setData({ players: this.decoratePlayers(page.items), playersMore: page.has_more, playersCursor: page.next_cursor })
    } catch (err) {
      if (this.playersCurrent(id, token)) this.setData({ playersFailed: true, playersError: err.detail || '选手列表加载失败，请重试' })
    } finally {
      if (this.playersCurrent(id, token)) this.setData({ playersLoading: false })
    }
  },
  async load() {
    const id = this._requestId = (this._requestId || 0) + 1
    const token = wx.getStorageSync('token') || ''
    if (this.data.token !== token) this.setData({ appliedTeamIds: [], expandedPlayerId: null, expandedPostId: null })
    this.setData({ token, loading: true, failed: false, accountFailed: false, loadingMore: false,
      mine: null, myTeam: null, editing: false, applying: null })
    const results = await Promise.allSettled([api.getRecruitmentPosts(), token ? api.getMyRecruitment() : null, token ? api.getMyTeam() : null,
      this.loadPlayers()])
    if (!this.current(id, token)) return
    const update = { loading: false, accountFailed: results[1].status === 'rejected' || results[2].status === 'rejected' }
    if (results[0].status === 'fulfilled') {
      const page = results[0].value
      Object.assign(update, { posts: this.decorate(page.items), more: page.has_more, cursor: page.next_cursor })
    } else Object.assign(update, { posts: [], failed: true, more: false })
    if (results[1].status === 'fulfilled') update.mine = results[1].value
    if (results[2].status === 'fulfilled') update.myTeam = results[2].value
    this.setData(update)
    if (this._openPublish && !update.accountFailed) {
      this._openPublish = false
      if (this.data.mine && this.data.mine.can_publish) this.edit()
    }
  },
  async loadMore() {
    if (this.data.tab === 'players') return this.loadMorePlayers()
    if (!this.data.more || this.data.loading || this.data.loadingMore || this.data.failed) return
    const id = this._requestId, token = this.data.token
    this.setData({ loadingMore: true })
    try {
      const page = await api.getRecruitmentPosts(this.data.cursor)
      if (!this.current(id, token)) return
      const seen = new Set(this.data.posts.map(p => p.team_id))
      this.setData({ posts: this.data.posts.concat(this.decorate(page.items.filter(p => !seen.has(p.team_id)))), more: page.has_more, cursor: page.next_cursor })
    } catch (err) { wx.showToast({ title: err.detail || '加载失败，请重试', icon: 'none' }) }
    finally { if (this.current(id, token)) this.setData({ loadingMore: false }) }
  },
  async loadMorePlayers() {
    if (!this.data.playersMore || this.data.playersLoading || this.data.playersLoadingMore || this.data.playersFailed) return
    const id = this._playersRequestId, token = this._playersToken, filters = this.playerFilters()
    const query = this.data.query, cursor = this.data.playersCursor
    this.setData({ playersLoadingMore: true })
    try {
      const page = await api.getRecruitmentPlayers(cursor, query, filters)
      if (!this.playersCurrent(id, token)) return
      this.checkPlayerFilters(page, filters)
      const seen = new Set(this.data.players.map(p => p.id))
      this.setData({ players: this.data.players.concat(this.decoratePlayers(page.items.filter(p => !seen.has(p.id)))),
        playersMore: page.has_more, playersCursor: page.next_cursor })
    } catch (err) {
      if (this.playersCurrent(id, token)) wx.showToast({ title: err.detail || '选手加载失败，请重试', icon: 'none' })
    } finally { if (this.playersCurrent(id, token)) this.setData({ playersLoadingMore: false }) }
  },
  invite(e) {
    const player = this.data.players.find(p => p.id === Number(e.currentTarget.dataset.id))
    if (!player || player.has_team || !this.data.mine || this.data.accountFailed || this.data.inviting) return
    const token = this.data.token, teamId = this.data.mine.team_id
    wx.showModal({ title: '邀请 ' + player.displayName, content: '邀请这位选手加入「' + this.data.mine.team_name + '」？', success: async r => {
      if (!r.confirm || this.data.inviting || token !== wx.getStorageSync('token')) return
      this.setData({ inviting: player.id })
      try {
        await wechatSubscriptions('captain_recruitment').catch(() => {})
        if (token !== (wx.getStorageSync('token') || '')) return
        await api.invitePlayer(teamId, player.id); wx.showToast({ title: '邀请已发送，等待对方同意', icon: 'none' })
      }
      catch (err) { wx.showToast({ title: err.detail || '邀请失败', icon: 'none' }) }
      finally { this.setData({ inviting: null }) }
    } })
  },
  edit() { if (this.data.mine && this.data.mine.can_publish && !this.data.accountFailed) this.setData({ editing: true, draft: this.data.mine.content || '' }) },
  cancelEdit() { if (!this.data.saving) this.setData({ editing: false }) },
  onDraft(e) { this.setData({ draft: e.detail.value }) },
  async save() {
    if (this.data.saving || !this.data.mine) return
    const content = this.data.draft.trim()
    const teamId = this.data.mine.team_id, token = wx.getStorageSync('token') || ''
    if (!content) { wx.showToast({ title: '请填写招募内容', icon: 'none' }); return }
    this.setData({ saving: true })
    try {
      await wechatSubscriptions('captain_recruitment').catch(() => {})
      if (token !== (wx.getStorageSync('token') || '') || !this.data.mine || this.data.mine.team_id !== teamId) return
      await api.saveRecruitment(teamId, content)
      wx.showToast({ title: '招募已发布', icon: 'success' })
      await this.load()
    } catch (err) { wx.showToast({ title: err.detail || '发布失败', icon: 'none' }) }
    finally { this.setData({ saving: false }) }
  },
  closePost() {
    if (this.data.saving || !this.data.mine) return
    wx.showModal({ title: '关闭招募', content: '关闭后将不再展示，已有入队申请会保留。', success: async r => {
      if (!r.confirm || this.data.saving) return
      this.setData({ saving: true })
      try { await api.closeRecruitment(this.data.mine.team_id); await this.load() }
      catch (err) { wx.showToast({ title: err.detail || '关闭失败', icon: 'none' }) }
      finally { this.setData({ saving: false }) }
    } })
  },
  apply(e) {
    if (!this.data.token) { this.goLogin(); return }
    if (this.data.myTeam || this.data.accountFailed || this.data.applying) return
    const teamId = Number(e.currentTarget.dataset.id)
    if (this.data.appliedTeamIds.includes(teamId)) return
    const post = this.data.posts.find(p => p.team_id === teamId)
    if (!post) return
    const token = this.data.token, request = this._requestId
    const usable = () => this.current(request, token) && this._chatVisible !== false
    wx.showModal({ title: '申请加入 ' + post.team_name, editable: true, placeholderText: '自我介绍（选填，最多 200 字）：位置、在线时段、组队期望', success: async r => {
      if (!r.confirm || this.data.applying || !usable()) return
      const message = (r.content || '').trim()
      if (message.length > 200) { wx.showToast({ title: '自我介绍最多 200 字', icon: 'none' }); return }
      this.setData({ applying: teamId })
      try {
        await wechatSubscriptions('personal_registration').catch(() => {})
        if (!usable()) return
        await api.applyJoin(teamId, message)
        if (!usable()) return
        this.setData({ appliedTeamIds: this.data.appliedTeamIds.concat(teamId), posts: this.data.posts.map(item => item.team_id === teamId ? Object.assign({}, item, { applied: true }) : item) })
        wx.showToast({ title: '已申请，等待队长处理', icon: 'none' })
      }
      catch (err) { if (usable()) wx.showToast({ title: err.detail || '申请失败', icon: 'none' }) }
      finally { if (usable()) this.setData({ applying: null }) }
    } })
  },
  goTeam() { wx.navigateTo({ url: '/pages/team/team' }) },
  goLogin() { wx.navigateTo({ url: '/pages/login/login' }) },
  onShareAppMessage() { return { title: '北林 CS2 · 招募大厅，一起找到队友', path: '/pages/recruitment/recruitment' } }
})
