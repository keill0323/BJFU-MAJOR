/**
 * 首页 - 赛事列表
 * 功能：展示所有赛事、搜索赛事、点击进入详情
 */
const api = require('../../utils/api.js')

Page({
  data: {
    matches: [],   // 赛事列表数据
    keyword: ''    // 搜索框内容
  },

  // 页面加载时拉取赛事列表
  async onLoad() {
    await this.loadMatches()
  },

  // 下拉刷新：重新拉取
  async onPullDownRefresh() {
    await this.loadMatches()
    wx.stopPullDownRefresh()  // 关闭下拉刷新动画
  },

  // 调用后端获取全部赛事
  async loadMatches() {
    try {
      const data = await api.getMatches()
      this.setData({ matches: data || [] })
    } catch (err) {
      wx.showToast({ title: '加载失败', icon: 'error' })
    }
  },

  // 输入框内容变化时同步到 data
  onSearchInput(e) {
    this.setData({ keyword: e.detail.value })
  },

  // 点搜索：有关键字调搜索接口，没关键字显示全部
  async onSearch() {
    const kw = this.data.keyword.trim()
    if (!kw) {
      await this.loadMatches()
      return
    }
    try {
      const data = await api.searchMatches(kw)
      this.setData({ matches: data || [] })
    } catch (err) {
      wx.showToast({ title: '搜索失败', icon: 'error' })
    }
  },

  // 点赛事卡片：带 id 跳转到详情页
  goMatch(e) {
    const id = e.currentTarget.dataset.id
    wx.navigateTo({ url: '/pages/match/match?id=' + id })
  }
})
