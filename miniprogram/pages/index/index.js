/**
 * 首页 - 赛事中心
 * 功能：展示赛事统计概览、按状态分组展示赛事、搜索
 */
const api = require('../../utils/api.js')

Page({
  data: {
    matches: [],   // 赛事列表数据（装饰后）
    keyword: '',
    groups: [],    // 按状态分组
    total: 0,
    registeringCount: 0,
    inProgressCount: 0
  },

  async onLoad() {
    await this.loadMatches()
  },

  async onPullDownRefresh() {
    await this.loadMatches()
    wx.stopPullDownRefresh()
  },

  async loadMatches() {
    try {
      const data = await api.getMatches()
      this.applyData(data)
    } catch (err) {
      wx.showToast({ title: '加载失败', icon: 'error' })
    }
  },

  onSearchInput(e) {
    this.setData({ keyword: e.detail.value })
  },

  async onSearch() {
    const kw = this.data.keyword.trim()
    if (!kw) {
      await this.loadMatches()
      return
    }
    try {
      const data = await api.searchMatches(kw)
      this.applyData(data)
    } catch (err) {
      wx.showToast({ title: '搜索失败', icon: 'error' })
    }
  },

  goMatch(e) {
    const id = e.currentTarget.dataset.id
    wx.navigateTo({ url: '/pages/match/match?id=' + id })
  },

  // 装饰赛事字段：状态文案 + 类型标签 + 报名进度百分比
  _decorate(m) {
    const statusText = { draft: '草稿', registering: '报名中', in_progress: '进行中', finished: '已结束' }[m.status] || m.status
    const typeLabel = m.match_type === 'freshman' ? '新生赛' : m.match_type === 'major' ? '大赛' : ''
    const max = m.max_teams || 16
    const progress = Math.min(100, Math.round(((m.registered_count || 0) / max) * 100))
    return Object.assign({}, m, { statusText, typeLabel, progress })
  },

  // 按状态分组（报名中 -> 进行中 -> 草稿 -> 已结束）
  groupMatches(list) {
    const order = ['registering', 'in_progress', 'draft', 'finished']
    const titles = { registering: '报名中', in_progress: '进行中', draft: '草稿', finished: '已结束' }
    const groups = order
      .filter(s => list.some(m => m.status === s))
      .map(s => ({ status: s, title: titles[s], matches: list.filter(m => m.status === s) }))
    const total = list.length
    const registeringCount = list.filter(m => m.status === 'registering').length
    const inProgressCount = list.filter(m => m.status === 'in_progress').length
    return { groups, total, registeringCount, inProgressCount }
  },

  // 装饰 + 分组 + 写入 data
  applyData(data) {
    const list = (data || []).map(m => this._decorate(m))
    const g = this.groupMatches(list)
    this.setData({
      matches: list,
      groups: g.groups,
      total: g.total,
      registeringCount: g.registeringCount,
      inProgressCount: g.inProgressCount
    })
  },

  showRules() {
    wx.navigateTo({ url: '/pages/rules/rules' })
  }
})
