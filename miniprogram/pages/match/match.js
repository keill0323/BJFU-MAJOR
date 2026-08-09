/**
 * 赛事详情页（用户端）
 * 功能：完整赛事资料展示（队伍状态/排名/小组赛/传奇组/淘汰赛BO3）+ 报名
 * 进入方式：首页点赛事卡片，带 ?id=xx 参数
 */
const api = require('../../utils/api.js')

Page({
  data: {
    match: null,
    tab: 'teams',            // teams / challenger / legend / knockout
    // 队伍
    teams: [],
    challengerTeams: [],
    legendTeams: [],
    playoffTeams: [],
    teamsSwiper: [],         // 队伍状态分阶段（挑战者/传奇/淘汰赛）
    teamsStage: 0,
    rankingTeams: [],        // 队伍排名
    // 对阵（按阶段）
    challengerSwiper: [],    // 挑战者组：小组赛分组 + 附加赛
    legendSwiper: [],        // 传奇组：上区/下区
    knockoutGroups: [],      // 淘汰赛 BO3：1/4决赛/半决赛/决赛
    challengerStage: 0,
    legendStage: 0,
    // 我的报名
    myReg: null,
    registered: false,
    myTeam: null,
    isCaptain: false,
    // BO3 小局详情弹层
    showBo3: false,
    bo3Detail: null
  },

  // 页面加载：拉完整赛事资料 + 报名状态
  async onLoad(options) {
    const id = options.id
    if (!id) return
    try {
      const detail = await api.getMatchDetail(id)
      this.setData({
        match: Object.assign({}, detail.match, { statusText: this.statusText(detail.match.status) })
      })
      this.buildData(detail)
      await this.loadMyStatus()
    } catch (err) {
      wx.showToast({ title: '加载失败', icon: 'error' })
    }
  },

  statusText(status) {
    const map = { draft: '草稿', registering: '报名中', in_progress: '进行中', finished: '已结束' }
    return map[status] || status
  },

  // 组装展示数据（复用管理端逻辑，仅只读展示）
  buildData(detail) {
    const teams = detail.teams || []
    const rounds = detail.rounds || []
    const knockoutRounds = rounds.filter(r => r.group_name === '淘汰赛')
    const koSorted = knockoutRounds.slice().sort((a, b) => a.round_number - b.round_number)
    const fmtBo3 = r => (r.bo3_scores ? r.bo3_scores.map(g => g.t1 + '-' + g.t2).join(' ') : '')

    // 淘汰赛对阵按轮次分组
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

    // 小组赛对阵按组分区
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

    // 淘汰赛名次
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
      const played = koSorted.some(r => r.team1_id == teamId || r.team2_id == teamId)
      return played ? '六强' : ''
    }

    // 阶段状态判定
    const challengerStatusOf = (t) => {
      if (t.stage === 'legend' || t.stage === 'playoff') return '已晋级'
      if (t.stage === 'challenger') return '进行中'
      if (t.group_name === '上区' || t.group_name === '下区' || t.group_name === '淘汰赛') return '已晋级'
      return '已淘汰'
    }
    const legendStatusOf = (t) => {
      if (t.stage === 'playoff') return '已晋级'
      if (t.stage === 'legend') return '进行中'
      if (t.group_name === '淘汰赛') return '已晋级'
      return '已淘汰'
    }

    const legendTeams = teams.filter(t => t.stage === 'legend')
    const challengerTeams = teams.filter(t => t.stage === 'challenger')
    const playoffTeams = teams.filter(t => t.stage === 'playoff')

    const challengerTab = teams.filter(t => {
      if (t.stage === 'challenger') return true
      if (['A', 'B', 'C', 'D', 'E', 'F', '附加赛'].indexOf(t.group_name) >= 0) return true
      return t.seed > 4
    }).map(t => Object.assign({}, t, { stage_status: challengerStatusOf(t) }))
    const legendTab = teams.filter(t => t.stage !== 'challenger')
      .map(t => Object.assign({}, t, { stage_status: legendStatusOf(t) }))
    const playoffTab = teams.filter(t => t.stage === 'playoff' ||
      (t.stage === 'eliminated' && t.group_name === '淘汰赛'))
      .map(t => Object.assign({}, t, { ko_rank: koRankOf(t.team_id) }))

    const teamsSwiper = [
      { title: '挑战者组', type: 'challenger', teams: challengerTab },
      { title: '传奇组', type: 'legend', teams: legendTab },
      { title: '淘汰赛', type: 'playoff', teams: playoffTab }
    ]

    // 队伍排名：名次 → 胜场 → 净胜分 → rating
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
      teams,
      challengerTeams,
      legendTeams,
      playoffTeams,
      teamsSwiper,
      rankingTeams,
      challengerSwiper,
      legendSwiper,
      knockoutGroups: koGroups
    })
  },

  switchTab(e) { this.setData({ tab: e.currentTarget.dataset.tab }) },
  switchTeamsStage(e) { this.setData({ teamsStage: Number(e.currentTarget.dataset.idx) }) },
  onTeamsSwiper(e) { this.setData({ teamsStage: e.detail.current }) },
  switchChallengerStage(e) { this.setData({ challengerStage: Number(e.currentTarget.dataset.idx) }) },
  onChallengerSwiper(e) { this.setData({ challengerStage: e.detail.current }) },
  switchLegendStage(e) { this.setData({ legendStage: Number(e.currentTarget.dataset.idx) }) },
  onLegendSwiper(e) { this.setData({ legendStage: e.detail.current }) },

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
  // 空处理器：阻止面板内点击冒泡到外层遮罩
  noop() {},

  // ===== 报名 =====
  // 拉我的报名状态 + 我的队伍
  async loadMyStatus() {
    if (!this.data.match) return
    try {
      const regData = await api.getMyRegistration(this.data.match.id)
      this.setData({
        registered: regData.registered,
        myReg: regData.registration
      })
      // 查我的队伍（判断报名类型用）
      const team = await api.getMyTeam()
      let isCaptain = false
      if (team) {
        const userId = Number(this.getUserIdFromToken())
        isCaptain = team.captain_id === userId
      }
      this.setData({ myTeam: team || null, isCaptain: isCaptain })
    } catch (err) {
      // 没登录等情况，忽略
    }
  },

  // 从 JWT 里解出 user_id（payload 是 base64）
  getUserIdFromToken() {
    try {
      const token = wx.getStorageSync('token')
      const payload = token.split('.')[1]
      const data = JSON.parse(decodeURIComponent(escape(atob(payload))))
      return data.sub
    } catch (err) {
      return null
    }
  },

  // 报名
  async handleRegister() {
    if (!this.data.match) return
    const match = this.data.match

    // 只有报名中的赛事能报
    if (match.status !== 'registering') {
      wx.showToast({ title: '当前不可报名', icon: 'none' })
      return
    }

    // 判断报名方式
    let title = '确认报名'
    let content = '确定报名该赛事吗?'

    if (this.data.myTeam && !this.data.isCaptain) {
      // 队员不能发起队伍报名
      wx.showToast({ title: '你是队员，需队长发起队伍报名', icon: 'none' })
      return
    }
    if (this.data.myTeam) {
      title = '队伍报名'
      content = `将以队伍「${this.data.myTeam.name}」报名，确定?`
    } else {
      title = '个人报名'
      content = '你还没有队伍，将以个人身份报名（可被队长邀请入队）'
    }

    wx.showModal({
      title: title,
      content: content,
      success: async (res) => {
        if (!res.confirm) return
        try {
          if (this.data.myTeam) {
            await api.registerTeam(match.id, this.data.myTeam.id)   // 队伍报名
          } else {
            await api.registerUser(match.id)                        // 个人报名
          }
          wx.showToast({ title: '报名成功', icon: 'success' })
          await this.loadMyStatus()   // 刷新报名状态
        } catch (err) {
          wx.showToast({ title: err.detail || '报名失败', icon: 'error' })
        }
      }
    })
  }
})
