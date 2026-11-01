const api = require('../../utils/api.js')

const PAGE_SIZE = 50
const cleanText = (value, maximum = 160) => typeof value === 'string' ? Array.from(value.trim()).slice(0, maximum).join('') : ''
const positiveId = value => Number.isSafeInteger(Number(value)) && Number(value) > 0 ? Number(value) : 0
const nonnegative = value => {
  if (value === null || value === undefined || value === '' || typeof value === 'boolean') return null
  const number = Number(value)
  return Number.isFinite(number) && number >= 0 ? number : null
}
const integer = value => {
  const number = nonnegative(value)
  return number !== null && Number.isSafeInteger(number) ? number : null
}
const countText = value => integer(value) === null ? '—' : String(integer(value))
const numberText = (value, precision) => nonnegative(value) === null ? '—' : nonnegative(value).toFixed(precision)

function playerRow(raw) {
  const key = cleanText(raw && raw.player_key, 100)
  if (!key) throw new Error('Invalid player key')
  const names = (Array.isArray(raw.teams) ? raw.teams : [])
    .map(team => cleanText(team && team.team_name)).filter(Boolean)
  const deaths = integer(raw.deaths)
  // Keep only public display fields; source IDs and unrelated account data stay out of setData.
  return {
    key,
    nickname: cleanText(raw.nickname) || '未命名选手',
    team_names: Array.from(new Set(names)).join(' / ') || '未关联战队',
    maps_text: countText(raw.maps_played),
    kda_text: [raw.kills, raw.deaths, raw.assists].map(countText).join(' / '),
    kd_text: deaths === null || deaths === 0 ? '—' : numberText(raw.kd, 2),
    adr_text: numberText(raw.adr, 1),
    rating_text: numberText(raw.average_rating, 2)
  }
}

function updateText(value) {
  const text = cleanText(value, 80)
  const match = /^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})(?::\d{2}(?:\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?$/.exec(text)
  if (!match) return ''
  // The API's time is a sync timestamp, never a game start-time estimate.
  const parsed = Date.parse(text)
  if (!Number.isFinite(parsed)) return ''
  if (!match[3]) return match[1] + ' ' + match[2]
  const beijing = new Date(parsed + 8 * 60 * 60 * 1000)
  const pad = number => String(number).padStart(2, '0')
  return beijing.getUTCFullYear() + '-' + pad(beijing.getUTCMonth() + 1) + '-' + pad(beijing.getUTCDate())
    + ' ' + pad(beijing.getUTCHours()) + ':' + pad(beijing.getUTCMinutes())
}

Component({
  properties: {
    matchId: {
      type: Number,
      value: 0,
      observer() {
        if (!this._attached) return
        this.setData({ keyword: '', searchDraft: '' })
        this.reloadStats()
      }
    }
  },
  data: {
    loading: true,
    loadingMore: false,
    error: '',
    moreError: '',
    rows: [],
    keyword: '',
    searchDraft: '',
    hasMore: false,
    total: 0,
    mapCountText: '—',
    playerCountText: '—',
    sourceLabel: '完美赛事平台',
    updatedText: '',
    updatedZone: '',
    notices: [],
    showMethod: false
  },
  lifetimes: {
    created() { this._generation = 0; this._attached = false; this._detached = false; this._nextOffset = 0 },
    attached() { this._attached = true; this._detached = false; this.reloadStats() },
    detached() { this._detached = true; this._attached = false; this._generation = (this._generation || 0) + 1 }
  },
  methods: {
    onSearchInput(e) { this.setData({ searchDraft: Array.from(String(e.detail.value || '')).slice(0, 80).join('') }) },
    submitSearch() {
      this.setData({ keyword: this.data.searchDraft.trim() })
      return this.reloadStats()
    },
    clearSearch() {
      this.setData({ searchDraft: '', keyword: '' })
      return this.reloadStats()
    },
    toggleMethod() { this.setData({ showMethod: !this.data.showMethod }) },
    reloadStats() { return this.loadStats(false) },
    loadMore() {
      if (!this.data.hasMore || this.data.loading || this.data.loadingMore) return
      return this.loadStats(true)
    },
    async loadStats(append) {
      if (!this._attached || this._detached) return
      const matchId = positiveId(this.data.matchId)
      const keyword = this.data.keyword
      const offset = append ? this._nextOffset : 0
      const generation = this._generation = (this._generation || 0) + 1
      const isCurrent = () => this._attached && !this._detached && generation === this._generation
        && matchId === positiveId(this.data.matchId) && keyword === this.data.keyword
      if (!append) {
        this._nextOffset = 0
        this.setData({ loading: !!matchId, loadingMore: false, error: matchId ? '' : '尚未选择赛事', moreError: '',
          rows: [], hasMore: false, total: 0, mapCountText: '—', playerCountText: '—', updatedText: '', updatedZone: '', notices: [] })
      } else this.setData({ loadingMore: true, moreError: '' })
      if (!matchId) return
      try {
        const result = await api.getMatchPlayerStats(matchId, { keyword, offset, limit: PAGE_SIZE })
        if (!isCurrent()) return
        if (!result || result.source !== 'perfect_match' || positiveId(result.match_id) !== matchId || !Array.isArray(result.items)
          || integer(result.offset) !== offset || integer(result.total) === null || typeof result.has_more !== 'boolean'
          || integer(result.total) < offset + result.items.length
          || (result.has_more && !result.items.length)) throw new Error('Invalid stats response')
        const rows = append ? this.data.rows.slice() : []
        const indices = new Map(rows.map((row, index) => [row.key, index]))
        for (const item of result.items) {
          const row = playerRow(item)
          if (indices.has(row.key)) rows[indices.get(row.key)] = row
          else { indices.set(row.key, rows.length); rows.push(row) }
        }
        this._nextOffset = offset + result.items.length
        this.setData({ loading: false, loadingMore: false, error: '', moreError: '', rows,
          total: integer(result.total), hasMore: result.has_more,
          mapCountText: countText(result.map_count), playerCountText: countText(result.player_count),
          sourceLabel: cleanText(result.source_label, 60) || '完美赛事平台', updatedText: updateText(result.updated_at),
          updatedZone: /(?:Z|[+-]\d{2}:?\d{2})$/.test(cleanText(result.updated_at, 80)) ? '北京时间' : '来源时间',
          notices: (Array.isArray(result.notices) ? result.notices : []).map(value => cleanText(value, 500)).filter(Boolean).slice(0, 6) })
      } catch (err) {
        if (!isCurrent()) return
        const error = err && err.code === 'BACKEND_UPGRADE_REQUIRED' ? '选手数据功能尚未上线，请稍后重试'
          : '选手数据加载失败，请检查网络后重试'
        this.setData(append ? { loadingMore: false, moreError: error } : { loading: false, error, rows: [], hasMore: false })
      }
    }
  }
})
