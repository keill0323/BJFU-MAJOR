const api = require('../../utils/api.js')
const { rankBadge } = require('../../utils/rank.js')

const boardState = () => ({ items: [], total: 0, nextOffset: 0, hasMore: false, loading: false, attempted: false, error: '' })
const fullImage = value => value ? (/^https?:\/\//i.test(value) ? value : api.BASE + '/' + String(value).replace(/^\/+/, '')) : ''
const initial = value => String(value || '北林').slice(0, 1)

function decoratePlayer(player) {
  const nickname = player.nickname || '北林选手'
  return Object.assign({}, player, { nickname, initial: initial(nickname), avatarFull: fullImage(player.avatar), badge: rankBadge(player.rank) })
}

function decorateChampion(champion) {
  return Object.assign({}, champion, {
    champion_name: champion.champion_name || '冠军队伍',
    initial: initial(champion.champion_name), logoFull: fullImage(champion.champion_logo),
    eventLabel: String(champion.event_date || champion.awarded_at || '').slice(0, 10).replace(/-/g, '.'),
    typeLabel: champion.match_type === 'freshman' ? '新生赛' : '公开大赛',
    hasScore: champion.champion_score !== null && champion.champion_score !== undefined && champion.runner_up_score !== null && champion.runner_up_score !== undefined,
    matchAvailable: champion.match_available !== false,
    sourceLabel: champion.snapshot_source === 'manual' ? '管理员补录' : champion.snapshot_source === 'backfill' ? '历史归档' : '',
    expanded: false, roster: (champion.roster || []).map((member, index) => Object.assign(decoratePlayer(member), {
      rosterKey: 'member-' + index, hasRankRecord: rankBadge(member.rank).key !== 'unranked'
    }))
  })
}

Page({
  data: {
    activeTab: 'champions',
    tabs: [{ value: 'champions', title: '赛事冠军' }, { value: 'players', title: '选手段位' }],
    champions: boardState(), players: boardState(), highestPlayer: null
  },
  onLoad(options = {}) {
    const activeTab = options.tab === 'players' ? 'players' : 'champions'
    this.setData({ activeTab })
    return this.loadBoard(activeTab)
  },
  onUnload() {
    this._unloaded = true
    this._requestIds = {}
  },
  switchTab(e) {
    const tab = e.currentTarget.dataset.tab
    if (tab !== 'champions' && tab !== 'players') return
    this.setData({ activeTab: tab })
    if (!this.data[tab].attempted) return this.loadBoard(tab)
  },
  async onPullDownRefresh() {
    const refreshId = (this._refreshId || 0) + 1
    this._refreshId = refreshId
    await this.loadBoard(this.data.activeTab, true)
    if (!this._unloaded && refreshId === this._refreshId) wx.stopPullDownRefresh()
  },
  onReachBottom() { return this.loadMore() },
  loadMore() {
    const tab = this.data.activeTab
    if (!this.data[tab].hasMore || this.data[tab].error) return
    return this.loadBoard(tab)
  },
  retry() {
    const tab = this.data.activeTab
    return this.loadBoard(tab, !!(this._retryReset && this._retryReset[tab]))
  },
  loadBoard(tab, reset = false) {
    if (this._unloaded || (tab !== 'champions' && tab !== 'players')) return Promise.resolve()
    this._pending = this._pending || {}
    if (this.data[tab].loading && !reset) return this._pending[tab]
    this._requestIds = this._requestIds || {}
    const requestId = (this._requestIds[tab] || 0) + 1
    this._requestIds[tab] = requestId
    const offset = reset ? 0 : this.data[tab].nextOffset
    this._updateBoard(tab, { loading: true, attempted: true, error: '' })
    const request = this._fetchBoard(tab, requestId, offset, reset)
    this._pending[tab] = request
    return request
  },
  async _fetchBoard(tab, requestId, offset, reset) {
    try {
      const result = tab === 'champions' ? await api.getHallChampions(offset, 20) : await api.getHallPlayers(offset, 50)
      if (this._unloaded || this._requestIds[tab] !== requestId) return
      const received = result.items || []
      const decorate = tab === 'champions' ? decorateChampion : decoratePlayer
      const key = tab === 'champions' ? 'match_id' : 'user_id'
      const items = reset ? [] : this.data[tab].items.slice()
      const seen = new Set(items.map(item => item[key]))
      received.forEach(item => { if (!seen.has(item[key])) { items.push(decorate(item)); seen.add(item[key]) } })
      if (tab === 'players') {
        const counts = {}
        items.forEach(item => { counts[item.position] = (counts[item.position] || 0) + 1 })
        items.forEach(item => { item.tied = counts[item.position] > 1 })
        this.setData({ highestPlayer: items.length ? items[0] : null })
      }
      this._updateBoard(tab, { items, total: result.total || 0, nextOffset: offset + received.length, hasMore: !!result.has_more && received.length > 0 })
    } catch (err) {
      if (this._unloaded || this._requestIds[tab] !== requestId) return
      this._retryReset = this._retryReset || {}
      this._retryReset[tab] = reset
      this._updateBoard(tab, { error: err && err.detail || '暂时无法加载，请稍后重试' })
    } finally {
      if (!this._unloaded && this._requestIds[tab] === requestId) this._updateBoard(tab, { loading: false })
    }
  },
  _updateBoard(tab, update) { this.setData({ [tab]: Object.assign({}, this.data[tab], update) }) },
  toggleRoster(e) {
    const id = Number(e.currentTarget.dataset.id)
    this._updateBoard('champions', { items: this.data.champions.items.map(item => item.match_id === id ? Object.assign({}, item, { expanded: !item.expanded }) : item) })
  },
  goMatch(e) {
    const id = Number(e.currentTarget.dataset.id)
    const champion = this.data.champions.items.find(item => item.match_id === id)
    if (!champion || !champion.matchAvailable) return
    if (Number.isInteger(id) && id > 0) wx.navigateTo({ url: '/pages/match/match?id=' + id })
  }
})
