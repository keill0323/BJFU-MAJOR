/**
 * 对阵记录页（管理员/审核员）
 * 功能：查看赛事全部对阵、录入比分、点击队伍查看该队本赛事历史对阵
 * 入口：管理后台 → 赛事 → 对阵记录
 */
const api = require('../../../utils/api.js')

Page({
  data: {
    matchId: null,
    match: null,
    teams: [],
    legendTeams: [],
    challengerTeams: [],
    rounds: [],
    groups: {},           // 按组聚合的对阵 { A组: [..], 附加赛: [..] }
    showHistory: false,   // 队伍历史弹层
    historyTeam: null,
    historyRounds: [],
    // 比分输入面板（页面内输入，录完停留当前页）
    showScoreInput: false,
    scoreRoundId: null,
    scoreInput: ''
  },

  onLoad(options) {
    this.setData({ matchId: options.matchId })
    this.load()
  },

  async load() {
    try {
      const detail = await api.adminMatchDetail(this.data.matchId)
      const teams = detail.teams || []
      const rounds = detail.rounds || []
      const groups = {}
      rounds.forEach(r => {
        const g = r.group_name || '未分组'
        ;(groups[g] = groups[g] || []).push(r)
      })
      this.setData({
        match: Object.assign({}, detail.match, { statusText: this.statusText(detail.match.status) }),
        teams,
        legendTeams: teams.filter(t => t.stage === 'legend'),
        challengerTeams: teams.filter(t => t.stage !== 'legend'),
        rounds,
        groups
      })
    } catch (err) {
      this.showErr(err, '加载失败')
    }
  },

  // 打开比分输入面板
  openScore(e) {
    this.setData({ scoreRoundId: e.currentTarget.dataset.id, scoreInput: '', showScoreInput: true })
  },
  onScoreInput(e) {
    this.setData({ scoreInput: e.detail.value })
  },
  closeScoreInput() {
    this.setData({ showScoreInput: false, scoreRoundId: null, scoreInput: '' })
  },
  // 提交比分，刷新当前页（不返回）
  async submitScore() {
    const rid = this.data.scoreRoundId
    if (!rid) return
    const parts = (this.data.scoreInput || '').trim().split(/[-:：]/)
    const s1 = parseInt(parts[0], 10)
    const s2 = parseInt(parts[1], 10)
    if (isNaN(s1) || isNaN(s2)) {
      wx.showToast({ title: '比分格式错误，如 13-9', icon: 'none' })
      return
    }
    try {
      await api.updateRoundResult(rid, { team1_score: s1, team2_score: s2 })
      this.setData({ showScoreInput: false, scoreRoundId: null, scoreInput: '' })
      wx.showToast({ title: '比分已更新', icon: 'success' })
      this.load()
    } catch (err) {
      this.showErr(err, '更新失败')
    }
  },

  // 点击队伍：查看该队在本赛事的全部历史对阵
  showTeam(e) {
    const tid = e.currentTarget.dataset.id
    if (!tid) return
    const historyRounds = this.data.rounds.filter(r => r.team1_id == tid || r.team2_id == tid)
    const historyTeam = this.data.teams.find(t => t.team_id == tid)
    this.setData({ showHistory: true, historyTeam, historyRounds })
  },

  closeHistory() {
    this.setData({ showHistory: false, historyTeam: null, historyRounds: [] })
  },

  showErr(err, fallback) {
    const msg = (err && err.detail) || fallback || '操作失败'
    wx.showModal({ title: '提示', content: msg, showCancel: false })
  },

  statusText(s) {
    const map = { draft: '草稿', registering: '报名中', in_progress: '进行中', finished: '已结束' }
    return map[s] || s
  }
})
