const api = require('../../utils/api.js')
const { rankDisplay } = require('../../utils/rank.js')

Page({
  data: { tab: 'players', players: [], playersFailed: false, playersMore: false, playersCursor: null,
    keyword: '', query: '', inviting: null,
    posts: [], mine: null, myTeam: null, token: '', loading: true, failed: false, accountFailed: false,
    more: false, cursor: null, loadingMore: false, editing: false, draft: '', saving: false, applying: null },
  onShow() { this.load() },
  onUnload() { this._requestId = (this._requestId || 0) + 1 },
  onPullDownRefresh() { return this.load().finally(() => wx.stopPullDownRefresh()) },
  onReachBottom() { return this.loadMore() },
  current(id, token) { return id === this._requestId && token === (wx.getStorageSync('token') || '') },
  decorate(items) { return items.map(p => Object.assign({}, p, {
    initial: (p.team_name || '队').slice(0, 1), timeText: String(p.updated_at || '').replace('T', ' ').slice(0, 16),
    logoFull: p.logo ? (/^https?:\/\//.test(p.logo) ? p.logo : api.BASE + p.logo) : ''
  })) },
  decoratePlayers(items) { return items.map(p => Object.assign({}, p, {
    displayName: p.nickname || p.game_id || '选手' + p.id, displayRank: rankDisplay(p.rank) || '段位待认证',
    identityText: !p.is_verified ? '身份待确认 · 学籍未认证' : p.identity === 'new_student' ? '新生' : p.identity === 'senior' ? '老生' : '新老生身份待确认',
    initial: (p.nickname || p.game_id || '选手').slice(0, 1),
    avatarFull: p.avatar ? (/^https?:\/\//.test(p.avatar) ? p.avatar : api.BASE + p.avatar) : ''
  })) },
  switchTab(e) { if (['players', 'posts'].includes(e.currentTarget.dataset.tab)) this.setData({ tab: e.currentTarget.dataset.tab }) },
  onKeyword(e) { this.setData({ keyword: e.detail.value }) },
  searchPlayers() { this.setData({ query: this.data.keyword.trim() }); return this.load() },
  async load() {
    const id = this._requestId = (this._requestId || 0) + 1
    const token = wx.getStorageSync('token') || ''
    this.setData({ token, loading: true, failed: false, accountFailed: false, loadingMore: false,
      mine: null, myTeam: null, editing: false })
    const results = await Promise.allSettled([api.getRecruitmentPosts(), token ? api.getMyRecruitment() : null, token ? api.getMyTeam() : null,
      api.getRecruitmentPlayers(null, this.data.query)])
    if (!this.current(id, token)) return
    const update = { loading: false, accountFailed: results[1].status === 'rejected' || results[2].status === 'rejected' }
    if (results[0].status === 'fulfilled') {
      const page = results[0].value
      Object.assign(update, { posts: this.decorate(page.items), more: page.has_more, cursor: page.next_cursor })
    } else Object.assign(update, { posts: [], failed: true, more: false })
    if (results[1].status === 'fulfilled') update.mine = results[1].value
    if (results[2].status === 'fulfilled') update.myTeam = results[2].value
    if (results[3].status === 'fulfilled') Object.assign(update, {
      players: this.decoratePlayers(results[3].value.items), playersMore: results[3].value.has_more,
      playersCursor: results[3].value.next_cursor, playersFailed: false
    })
    else Object.assign(update, { players: [], playersFailed: true, playersMore: false })
    this.setData(update)
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
    if (!this.data.playersMore || this.data.loading || this.data.loadingMore || this.data.playersFailed) return
    const id = this._requestId, token = this.data.token
    this.setData({ loadingMore: true })
    try {
      const page = await api.getRecruitmentPlayers(this.data.playersCursor, this.data.query)
      if (!this.current(id, token)) return
      const seen = new Set(this.data.players.map(p => p.id))
      this.setData({ players: this.data.players.concat(this.decoratePlayers(page.items.filter(p => !seen.has(p.id)))),
        playersMore: page.has_more, playersCursor: page.next_cursor })
    } catch (err) { wx.showToast({ title: err.detail || '选手加载失败，请重试', icon: 'none' }) }
    finally { if (this.current(id, token)) this.setData({ loadingMore: false }) }
  },
  invite(e) {
    const player = this.data.players.find(p => p.id === Number(e.currentTarget.dataset.id))
    if (!player || player.has_team || !this.data.mine || this.data.accountFailed || this.data.inviting) return
    const token = this.data.token, teamId = this.data.mine.team_id
    wx.showModal({ title: '邀请 ' + player.displayName, content: '邀请这位选手加入「' + this.data.mine.team_name + '」？', success: async r => {
      if (!r.confirm || this.data.inviting || token !== wx.getStorageSync('token')) return
      this.setData({ inviting: player.id })
      try { await api.invitePlayer(teamId, player.id); wx.showToast({ title: '邀请已发送，等待对方同意', icon: 'none' }) }
      catch (err) { wx.showToast({ title: err.detail || '邀请失败', icon: 'none' }) }
      finally { this.setData({ inviting: null }) }
    } })
  },
  edit() { this.setData({ editing: true, draft: this.data.mine.content || '' }) },
  cancelEdit() { if (!this.data.saving) this.setData({ editing: false }) },
  onDraft(e) { this.setData({ draft: e.detail.value }) },
  async save() {
    if (this.data.saving || !this.data.mine) return
    const content = this.data.draft.trim()
    if (!content) { wx.showToast({ title: '请填写招募内容', icon: 'none' }); return }
    this.setData({ saving: true })
    try {
      await api.saveRecruitment(this.data.mine.team_id, content)
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
    const post = this.data.posts.find(p => p.team_id === teamId)
    if (!post) return
    wx.showModal({ title: '申请加入 ' + post.team_name, editable: true, placeholderText: '自我介绍（选填，最多 200 字）：位置、在线时段、组队期望', success: async r => {
      if (!r.confirm || this.data.applying) return
      const message = (r.content || '').trim()
      if (message.length > 200) { wx.showToast({ title: '自我介绍最多 200 字', icon: 'none' }); return }
      this.setData({ applying: teamId })
      try { await api.applyJoin(teamId, message); wx.showToast({ title: '已申请，等待队长处理', icon: 'none' }) }
      catch (err) { wx.showToast({ title: err.detail || '申请失败', icon: 'none' }) }
      finally { this.setData({ applying: null }) }
    } })
  },
  goTeam() { wx.navigateTo({ url: '/pages/team/team' }) },
  goLogin() { wx.navigateTo({ url: '/pages/login/login' }) },
  onShareAppMessage() { return { title: '北林 CS2 · 招募大厅，一起找到队友', path: '/pages/recruitment/recruitment' } }
})
