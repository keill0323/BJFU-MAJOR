/**
 * 赛事详情页（管理员/审核员）
 * 功能：按阶段分 tab 管理赛事 —— 赛事队伍 / 挑战者组 / 传奇组 / 淘汰赛
 * 入口：管理后台 → 赛事 → 点击赛事卡片
 */
const api = require('../../../utils/api.js')

Page({
  data: {
    matchId: null,
    match: null,
    tab: 'teams',           // teams / challenger / legend / knockout
    // 队伍
    teams: [],
    legendTeams: [],        // 传奇组（含直升+晋级）
    challengerTeams: [],    // 挑战者组（小组赛阶段）
    playoffTeams: [],       // 淘汰赛（6强）
    eliminatedTeams: [],    // 已淘汰队伍
    teamsSwiper: [],        // 队伍状态分 tab（挑战者组/传奇组/淘汰赛，含该阶段淘汰队伍）
    teamsStage: 0,          // 队伍状态 swiper 当前 tab
    rankingTeams: [],       // 队伍排名列表
    // 对阵（按阶段）
    challengerRounds: [],
    legendRounds: [],
    knockoutRounds: [],
    knockoutGroups: [],     // 淘汰赛对阵按轮次分组（1/4决赛/半决赛/决赛）
    knockoutHint: '',       // 淘汰赛流程提示
    // 可滑动区块（swiper）
    challengerSwiper: [],
    legendSwiper: [],
    challengerStage: 0,
    legendStage: 0,
    // 队伍历史弹层
    showHistory: false,
    historyTeam: null,
    historyRounds: [],
    // 比分输入面板
    showScoreInput: false,
    scoreRoundId: null,
    scoreInput: '',
    isBo3: false,           // 当前录入的是否淘汰赛 BO3
    bo3Games: ['', '', ''],  // BO3 三局小分
    // BO3 小局详情弹层
    showBo3: false,
    bo3Detail: null
  },

  onLoad(options) {
    this.setData({ matchId: options.matchId })
    this.load()
  },

  switchTab(e) {
    this.setData({ tab: e.currentTarget.dataset.tab })
  },

  // 可滑动区块切换（挑战者组/传奇组）
  switchChallengerStage(e) {
    this.setData({ challengerStage: Number(e.currentTarget.dataset.idx) })
  },
  onChallengerSwiper(e) {
    this.setData({ challengerStage: e.detail.current })
  },
  switchLegendStage(e) {
    this.setData({ legendStage: Number(e.currentTarget.dataset.idx) })
  },
  onLegendSwiper(e) {
    this.setData({ legendStage: e.detail.current })
  },
  // 赛事队伍分阶段区块切换
  switchTeamsStage(e) {
    this.setData({ teamsStage: Number(e.currentTarget.dataset.idx) })
  },
  onTeamsSwiper(e) {
    this.setData({ teamsStage: e.detail.current })
  },

  async load() {
    try {
      const detail = await api.adminMatchDetail(this.data.matchId)
      const teams = detail.teams || []
      const rounds = detail.rounds || []
      const challengerRounds = rounds.filter(r => ['A', 'B', 'C', '附加赛'].indexOf(r.group_name) >= 0)
      const legendRounds = rounds.filter(r => ['上区', '下区'].indexOf(r.group_name) >= 0)
      const knockoutRounds = rounds.filter(r => r.group_name === '淘汰赛')
      // 淘汰赛对阵按轮次分组：前2场1/4决赛，接着2场半决赛，最后1场决赛
      const koSorted = knockoutRounds.slice().sort((a, b) => a.round_number - b.round_number)
      const fmtBo3 = r => (r.bo3_scores ? r.bo3_scores.map(g => g.t1 + '-' + g.t2).join(' ') : '')
      const koGroups = []
      if (koSorted.length > 0) {
        koGroups.push({
          title: '1/4决赛',
          rounds: koSorted.slice(0, 2).map((r, i) => Object.assign({}, r, {
            ko_label: i === 0 ? '上区·第2 vs 第3' : '下区·第2 vs 第3',
            bo3_text: fmtBo3(r)
          }))
        })
      }
      if (koSorted.length > 2) {
        koGroups.push({
          title: '半决赛',
          rounds: koSorted.slice(2, 4).map((r, i) => Object.assign({}, r, {
            ko_label: i === 0 ? '上区1 vs 下区胜者' : '下区1 vs 上区胜者',
            bo3_text: fmtBo3(r)
          }))
        })
      }
      if (koSorted.length > 4) {
        koGroups.push({
          title: '决赛',
          rounds: koSorted.slice(4, 5).map(r => Object.assign({}, r, {
            ko_label: '冠军战',
            bo3_text: fmtBo3(r)
          }))
        })
      }
      // 淘汰赛流程提示
      const koFinished = koSorted.filter(r => r.status === 'finished').length
      let knockoutHint = ''
      if (koSorted.length === 0) {
        knockoutHint = '① 点击「生成淘汰赛」：自动生成 1/4 决赛，每区第1轮空直进半决赛'
      } else if (koSorted.length === 2) {
        knockoutHint = koFinished < 2
          ? `② 请录入 1/4 决赛比分（BO3 三局两胜），已录 ${koFinished}/2`
          : '③ 1/4 决赛打完，点击「推进淘汰赛」生成半决赛'
      } else if (koSorted.length === 4) {
        knockoutHint = koFinished < 4
          ? `④ 请录入半决赛比分（BO3），已录 ${koFinished}/4`
          : '⑤ 半决赛打完，点击「推进淘汰赛」生成决赛'
      } else {
        knockoutHint = koFinished < 5
          ? '⑥ 请录入决赛比分（BO3）'
          : '决赛打完，冠军已产生！'
      }
      // 小组赛对阵按组分区（A组/B组/C组...）
      const groupRounds = {}
      rounds.filter(r => ['A', 'B', 'C', 'D', 'E', 'F'].indexOf(r.group_name) >= 0).forEach(r => {
        ;(groupRounds[r.group_name] = groupRounds[r.group_name] || []).push(r)
      })
      const challengerSwiper = [
        {
          title: '小组赛',
          groups: groupRounds,
          count: Object.keys(groupRounds).reduce((s, k) => s + groupRounds[k].length, 0)
        },
        { title: '附加赛', rounds: rounds.filter(r => r.group_name === '附加赛') }
      ]
      const legendSwiper = [
        { title: '上区', rounds: rounds.filter(r => r.group_name === '上区') },
        { title: '下区', rounds: rounds.filter(r => r.group_name === '下区') }
      ]
      const legendTeams = teams.filter(t => t.stage === 'legend')
      const challengerTeams = teams.filter(t => t.stage === 'challenger')
      const playoffTeams = teams.filter(t => t.stage === 'playoff')
      const eliminatedTeams = teams.filter(t => t.stage === 'eliminated')

      // 淘汰赛名次：决赛冠军/亚军，半决赛负者四强，其余六强
      const koFinal = koSorted.length >= 5 ? koSorted[4] : null
      const koSemi = koSorted.slice(2, 4)
      const koRankOf = (teamId) => {
        if (koFinal && (koFinal.team1_id == teamId || koFinal.team2_id == teamId)) {
          return koFinal.winner_id == teamId ? '冠军' : '亚军'
        }
        for (const r of koSemi) {
          if ((r.team1_id == teamId || r.team2_id == teamId) && r.status === 'finished' && r.winner_id != teamId) {
            return '四强'
          }
        }
        // 只有真正参加过淘汰赛的队伍才标六强
        const played = koSorted.some(r => r.team1_id == teamId || r.team2_id == teamId)
        return played ? '六强' : ''
      }

      // 阶段状态判定（基于当前赛制：前4直升 seed 1-4，其余从挑战者组打起）
      const challengerStatusOf = (t) => {
        if (t.stage === 'legend' || t.stage === 'playoff') return '已晋级'
        if (t.stage === 'challenger') return '进行中'
        // eliminated：曾在传奇/淘汰赛阶段（说明已晋级出挑战者组）-> 已晋级；否则挑战者阶段被淘汰
        if (t.group_name === '上区' || t.group_name === '下区' || t.group_name === '淘汰赛') return '已晋级'
        return '已淘汰'
      }
      const legendStatusOf = (t) => {
        if (t.stage === 'playoff') return '已晋级'
        if (t.stage === 'legend') return '进行中'
        if (t.group_name === '淘汰赛') return '已晋级'   // 晋级淘汰赛后淘汰
        return '已淘汰'                                  // 传奇组第4
      }

      // 挑战者组 tab：所有参加过挑战者组比赛的队伍
      const challengerTab = teams.filter(t => {
        if (t.stage === 'challenger') return true
        if (['A', 'B', 'C', 'D', 'E', 'F', '附加赛'].indexOf(t.group_name) >= 0) return true
        return t.seed > 4   // 从挑战者组晋级上来的（直升的前4 seed 1-4）
      }).map(t => Object.assign({}, t, { stage_status: challengerStatusOf(t) }))
      // 传奇组 tab：所有参加过传奇组比赛的队伍（直升 + 晋级 + 传奇组及以后淘汰）
      const legendTab = teams.filter(t => t.stage !== 'challenger')
        .map(t => Object.assign({}, t, { stage_status: legendStatusOf(t) }))
      // 淘汰赛 tab：所有参加过淘汰赛的队伍（6强 + 淘汰赛被淘汰的）
      const playoffTab = teams.filter(t => t.stage === 'playoff' ||
        (t.stage === 'eliminated' && t.group_name === '淘汰赛'))
        .map(t => Object.assign({}, t, { ko_rank: koRankOf(t.team_id) }))

      // 队伍状态分 tab：挑战者组 / 传奇组 / 淘汰赛
      const teamsSwiper = [
        { title: '挑战者组', type: 'challenger', teams: challengerTab },
        { title: '传奇组', type: 'legend', teams: legendTab },
        { title: '🎯 淘汰赛', type: 'playoff', teams: playoffTab }
      ]

      // 队伍排名：按比赛结果 —— 名次(冠军>亚军>四强>六强>传奇>挑战者>已淘汰) → 胜场 → 净胜分 → rating
      const stageWeightOf = (t) => {
        if (t.ko_rank === '冠军') return 0
        if (t.ko_rank === '亚军') return 1
        if (t.ko_rank === '四强') return 2
        if (t.ko_rank === '六强') return 3
        if (t.stage === 'legend') return 4
        if (t.stage === 'challenger') return 5
        return 6
      }
      const rankingTeams = teams.slice()
        .map(t => Object.assign({}, t, { ko_rank: koRankOf(t.team_id) }))
        .sort((a, b) => {
          const w = stageWeightOf(a) - stageWeightOf(b)
          if (w !== 0) return w
          if (b.wins !== a.wins) return b.wins - a.wins
          if ((b.diff || 0) !== (a.diff || 0)) return (b.diff || 0) - (a.diff || 0)
          return (b.rating || 0) - (a.rating || 0)
        })
      this.setData({
        match: Object.assign({}, detail.match, { statusText: this.statusText(detail.match.status) }),
        teams,
        legendTeams,
        challengerTeams,
        playoffTeams,
        eliminatedTeams,
        teamsSwiper,
        rankingTeams,
        challengerRounds,
        legendRounds,
        knockoutRounds,
        knockoutGroups: koGroups,
        knockoutHint,
        challengerSwiper,
        legendSwiper
      })
    } catch (err) {
      this.showErr(err, '加载失败')
    }
  },

  // BO3 小局详情（淘汰赛点对阵行查看）
  showBo3(e) {
    const rid = e.currentTarget.dataset.id
    let round = null
    this.data.knockoutGroups.forEach(g => {
      g.rounds.forEach(r => { if (r.id == rid) round = r })
    })
    if (!round) return
    this.setData({
      bo3Detail: {
        team1_name: round.team1_name || '轮空',
        team2_name: round.team2_name || '轮空',
        score1: round.team1_score,
        score2: round.team2_score,
        ko_label: round.ko_label,
        games: round.bo3_scores || []
      },
      showBo3: true
    })
  },
  closeBo3() { this.setData({ showBo3: false, bo3Detail: null }) },

  // ===== 录比分（页面内输入面板）=====
  openScore(e) {
    const rid = e.currentTarget.dataset.id
    // 判断该对阵是否淘汰赛（BO3）
    const r = []
      .concat(this.data.challengerRounds)
      .concat(this.data.legendRounds)
      .concat(this.data.knockoutRounds)
      .find(x => x.id == rid)
    const isBo3 = !!(r && r.group_name === '淘汰赛')
    this.setData({
      scoreRoundId: rid,
      isBo3,
      scoreInput: '',
      bo3Games: ['', '', ''],
      showScoreInput: true
    })
  },
  onScoreInput(e) {
    this.setData({ scoreInput: e.detail.value })
  },
  onBo3Input(e) {
    const idx = Number(e.currentTarget.dataset.index)
    const games = this.data.bo3Games.slice()
    games[idx] = e.detail.value
    this.setData({ bo3Games: games })
  },
  closeScoreInput() {
    this.setData({ showScoreInput: false, scoreRoundId: null, scoreInput: '', bo3Games: ['', '', ''] })
  },
  // 空处理器：阻止面板内点击冒泡到外层遮罩
  noop() {},
  async submitScore() {
    const rid = this.data.scoreRoundId
    if (!rid) return
    try {
      if (this.data.isBo3) {
        // ===== BO3（淘汰赛）：每局小分，总比分自动判定 =====
        const games = []
        for (const s of this.data.bo3Games) {
          const t = (s || '').trim()
          if (!t) continue
          const parts = t.split(/[-:：]/)
          const t1 = parseInt(parts[0], 10)
          const t2 = parseInt(parts[1], 10)
          if (isNaN(t1) || isNaN(t2)) {
            wx.showToast({ title: '每局格式如 13-9', icon: 'none' })
            return
          }
          games.push({ t1, t2 })
        }
        if (games.length < 2) {
          wx.showToast({ title: '至少填写2局小分', icon: 'none' })
          return
        }
        let s1 = 0, s2 = 0
        games.forEach(g => { if (g.t1 > g.t2) s1++; else if (g.t2 > g.t1) s2++ })
        if (s1 === s2 || Math.max(s1, s2) !== 2) {
          wx.showToast({ title: 'BO3 需一方赢2局（2-0 或 2-1）', icon: 'none' })
          return
        }
        await api.updateRoundResult(rid, { team1_score: s1, team2_score: s2, bo3_scores: games })
      } else {
        // ===== 单局（小组赛/附加赛/传奇组循环赛）=====
        const parts = (this.data.scoreInput || '').trim().split(/[-:：]/)
        const s1 = parseInt(parts[0], 10)
        const s2 = parseInt(parts[1], 10)
        if (isNaN(s1) || isNaN(s2)) {
          wx.showToast({ title: '比分格式错误，如 13-9', icon: 'none' })
          return
        }
        await api.updateRoundResult(rid, { team1_score: s1, team2_score: s2 })
      }
      this.setData({ showScoreInput: false, scoreRoundId: null, scoreInput: '', bo3Games: ['', '', ''] })
      wx.showToast({ title: '比分已更新', icon: 'success' })
      this.load()
    } catch (err) {
      this.showErr(err, '更新失败')
    }
  },

  // ===== 队伍历史对阵 =====
  showTeam(e) {
    const tid = e.currentTarget.dataset.id
    if (!tid) return
    const allRounds = []
      .concat(this.data.challengerRounds)
      .concat(this.data.legendRounds)
      .concat(this.data.knockoutRounds)
    const historyRounds = allRounds.filter(r => r.team1_id == tid || r.team2_id == tid)
    const historyTeam = this.data.teams.find(t => t.team_id == tid)
    this.setData({ showHistory: true, historyTeam, historyRounds })
  },
  closeHistory() {
    this.setData({ showHistory: false, historyTeam: null, historyRounds: [] })
  },

  // ===== 数据导出（CSV） =====
  // 导出赛事全部对阵
  exportAll() {
    wx.showLoading({ title: '生成中...' })
    api.exportMatchCsv(this.data.matchId)
      .then(() => {
        wx.hideLoading()
        wx.showToast({ title: '已导出', icon: 'success' })
      })
      .catch(err => {
        wx.hideLoading()
        this.showErr(err, '导出失败')
      })
  },

  // 导出某队伍在该赛事的历史对阵
  exportTeamCsv(e) {
    const tid = e.currentTarget.dataset.id
    if (!tid) return
    wx.showLoading({ title: '生成中...' })
    api.exportMatchCsv(this.data.matchId, tid)
      .then(() => {
        wx.hideLoading()
        wx.showToast({ title: '已导出', icon: 'success' })
      })
      .catch(err => {
        wx.hideLoading()
        this.showErr(err, '导出失败')
      })
  },

  // ===== 编排操作 =====
  // 分组（输入组数，自动生成 A/B/C...；自动先分配种子：前4直升传奇组）
  promptGroup() {
    wx.showModal({
      title: '分组',
      editable: true,
      placeholderText: '输入组数，如: 3',
      success: async (res) => {
        if (!res.confirm || !res.content) return
        const n = parseInt(res.content.trim(), 10)
        if (isNaN(n) || n < 2 || n > 6) {
          wx.showToast({ title: '请输入 2-6 之间的组数', icon: 'none' })
          return
        }
        // 自动生成 A, B, C... 组名
        const groups = []
        for (let i = 0; i < n; i++) groups.push(String.fromCharCode(65 + i))
        try {
          // 先分配种子（前4直升传奇组），再蛇形分组
          await api.autoSeeds(this.data.matchId)
          await api.autoGroup(this.data.matchId, groups)
          wx.showModal({
            title: '分组完成',
            content: `已分配种子并分为 ${groups.join('、')} 组`,
            showCancel: false
          })
          this.load()
        } catch (err) {
          this.showErr(err, '分组失败')
        }
      }
    })
  },

  // ③ 生成对阵：一键为所有小组生成单循环对阵（已生成的组自动跳过）
  async generateAllMatches() {
    try {
      const groups = [...new Set(this.data.challengerTeams.map(t => t.group_name).filter(Boolean))]
      if (groups.length < 2) {
        wx.showToast({ title: '请先分组再生成对阵', icon: 'none' })
        return
      }
      let made = 0
      for (const g of groups) {
        // 该组已有对阵则跳过，避免重复生成
        const existing = this.data.challengerRounds.filter(r => r.group_name === g)
        if (existing.length > 0) continue
        await api.autoMatches(this.data.matchId, g, true)
        made += 1
      }
      wx.showToast({ title: made > 0 ? `已为 ${made} 个组生成对阵` : '各组对阵已存在', icon: 'success' })
      this.load()
    } catch (err) {
      this.showErr(err, '生成对阵失败')
    }
  },

  async finishGroup() {
    try {
      const res = await api.finishGroupStage(this.data.matchId)
      const d = res.data || {}
      const w = (d.group_winners || []).length
      const n = d.added_rounds || 0
      wx.showModal({
        title: '小组赛已结束',
        content: `每组第1共 ${w} 队已晋级传奇组\n附加赛对阵已生成 ${n} 场，请录入附加赛比分`,
        showCancel: false
      })
      this.load()
    } catch (err) {
      this.showErr(err, '结束小组赛失败')
    }
  },

  async finishPlayoff() {
    try {
      await api.finishPlayoffStage(this.data.matchId)
      wx.showModal({
        title: '附加赛已结束',
        content: '附加赛第1已晋级传奇组，请到「传奇组」tab 进行分区',
        showCancel: false
      })
      this.load()
    } catch (err) {
      this.showErr(err, '结束附加赛失败')
    }
  },

  async divideLegend() {
    try {
      const res = await api.divideLegend(this.data.matchId)
      const d = res.data || {}
      const n = d.added_rounds || 0
      wx.showModal({
        title: '传奇组已分区',
        content: `8队已分上下区，循环赛对阵已生成 ${n} 场，请录入比分`,
        showCancel: false
      })
      this.load()
    } catch (err) {
      this.showErr(err, '传奇组分区失败')
    }
  },

  async finishLegend() {
    try {
      const res = await api.finishLegendStage(this.data.matchId)
      const d = res.data || {}
      const q = (d.qualified || []).length
      wx.showModal({
        title: '传奇组结束',
        content: `上下区每区前3共 ${q} 队晋级淘汰赛，请到「淘汰赛」tab 生成对阵`,
        showCancel: false
      })
      this.load()
    } catch (err) {
      this.showErr(err, '结束传奇组失败')
    }
  },

  async genKnockout() {
    try {
      const res = await api.generateKnockout(this.data.matchId)
      const d = res.data || {}
      const n = d.added_rounds || 0
      wx.showModal({
        title: '淘汰赛已生成',
        content: `6强1/4决赛已生成 ${n} 场（每区第1轮空），请录入比分`,
        showCancel: false
      })
      this.load()
    } catch (err) {
      this.showErr(err, '生成淘汰赛失败')
    }
  },

  async advanceKnockout() {
    try {
      await api.advanceKnockout(this.data.matchId)
      wx.showModal({
        title: '淘汰赛已推进',
        content: '已生成下一轮对阵，请录入比分',
        showCancel: false
      })
      this.load()
    } catch (err) {
      this.showErr(err, '推进淘汰赛失败')
    }
  },

  // ===== 工具 =====
  showErr(err, fallback) {
    const msg = (err && err.detail) || fallback || '操作失败'
    wx.showModal({ title: '提示', content: msg, showCancel: false })
  },

  statusText(s) {
    const map = { draft: '草稿', registering: '报名中', in_progress: '进行中', finished: '已结束' }
    return map[s] || s
  },

  stageTag(s, seed) {
    if (s === 'legend') return seed <= 4 ? '直升队伍' : '晋级传奇组'
    if (s === 'playoff') return '已晋级淘汰赛'
    if (s === 'eliminated') return '已淘汰'
    return '挑战者'
  }
})
