/**
 * 赛事详情页（管理员/审核员）
 * 功能：按阶段分 tab 管理赛事 —— 赛事队伍 / 挑战者组 / 传奇组 / 淘汰赛
 * 入口：管理后台 → 赛事 → 点击赛事卡片
 */
const api = require('../../../utils/api.js')
const { isWaitingForGroup, isRosterLocked } = require('../../../utils/tournament.js')

Page({
  data: {
    matchId: null,
    match: null,
    tab: 'teams',           // teams / challenger / legend / knockout
    // 队伍
    teams: [],
    waitingTeams: [],
    rosterLocked: false,
    groupingBusy: false,
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
    groupCount: 0,
    hasPlayoffStage: false,
    canFinishPlayoff: false,
    challengerFormatHint: '',
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
    bo3Detail: null,
    // 时间窗口面板
    showWindowPanel: false,
    allRounds: [],
    stageWindows: [],
    windowSections: [],
    windowSaving: false,
    windowEditGroup: null,
    windowEditLabel: '',
    windowStartDate: '',
    windowStartTime: '',
    windowEndDate: '',
    windowEndTime: '',
    // 报名时间和比赛排期范围分别管理。
    showRegistrationPanel: false,
    registrationSaving: false,
    registrationStartDate: '',
    registrationStartTime: '',
    registrationEndDate: '',
    registrationEndTime: '',
    registrationSummary: ''
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
    const index = Math.max(0, Math.min(Number(e.currentTarget.dataset.idx) || 0, this.data.challengerSwiper.length - 1))
    this.setData({ challengerStage: index })
  },
  onChallengerSwiper(e) {
    const index = Math.max(0, Math.min(Number(e.detail.current) || 0, this.data.challengerSwiper.length - 1))
    this.setData({ challengerStage: index })
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

  challengerFormat(teams, rounds) {
    const isGroup = name => !!name && ['附加赛', '上区', '下区', '淘汰赛'].indexOf(name) < 0
    // 保存的窗口仅用于回显，不能改变真实分组对应的赛制。
    const groups = [...new Set((teams || []).concat(rounds || []).map(row => row.group_name).filter(isGroup))]
    const groupCount = groups.length
    const hasHistory = (rounds || []).some(round => round.group_name === '附加赛')
    const challengerFormatHint = groupCount === 3
      ? '3 组赛制：3 个小组冠军晋级传奇组，3 个小组第 2 通过附加赛争夺 1 个晋级名额。'
      : groupCount === 4
        ? '4 组赛制：4 个小组冠军直接晋级传奇组，无附加赛。'
        : '请选择赛制：3 组冠军晋级，小组第 2 参加附加赛争夺 1 席；4 组冠军直接晋级，无附加赛。'
    return { groupCount, hasPlayoffStage: groupCount === 3 || (groupCount === 0 && hasHistory), canFinishPlayoff: groupCount === 3, challengerFormatHint }
  },

  async load() {
    try {
      const detail = await api.adminMatchDetail(this.data.matchId)
      const teams = detail.teams || []
      const rounds = detail.rounds || []
      const isChallengerGroup = name => !!name && ['附加赛', '上区', '下区', '淘汰赛'].indexOf(name) < 0
      const challengerRounds = rounds.filter(r => isChallengerGroup(r.group_name) || r.group_name === '附加赛')
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
      const groupRounds = Object.create(null)
      rounds.filter(r => isChallengerGroup(r.group_name)).forEach(r => {
        ;(groupRounds[r.group_name] = groupRounds[r.group_name] || []).push(r)
      })
      const format = this.challengerFormat(teams, rounds)
      const challengerSwiper = [{
        title: '小组赛', type: 'group', groups: groupRounds,
        count: Object.keys(groupRounds).reduce((s, k) => s + groupRounds[k].length, 0)
      }]
      if (format.hasPlayoffStage) challengerSwiper.push({
        title: format.groupCount === 3 ? '附加赛' : '附加赛（历史）', type: 'playoff',
        historyOnly: format.groupCount !== 3, rounds: rounds.filter(r => r.group_name === '附加赛')
      })
      const legendSwiper = [
        { title: '上区', rounds: rounds.filter(r => r.group_name === '上区') },
        { title: '下区', rounds: rounds.filter(r => r.group_name === '下区') }
      ]
      const legendTeams = teams.filter(t => t.stage === 'legend')
      const waitingTeams = teams.filter(isWaitingForGroup)
      const rosterLocked = isRosterLocked(detail.match, teams, rounds)
      const challengerTeams = teams.filter(t => t.stage === 'challenger' && !isWaitingForGroup(t) && t.registration_status !== 'pending' && t.registration_status !== 'rejected')
      const playoffTeams = teams.filter(t => t.stage === 'playoff')
      const eliminatedTeams = teams.filter(t => t.stage === 'eliminated')

      // 淘汰赛名次：决赛冠军/亚军，半决赛负者四强，其余六强
      const koFinal = koSorted.length >= 5 ? koSorted[4] : null
      const koSemi = koSorted.slice(2, 4)
      const koRankOf = (teamId) => {
        if (koFinal && koFinal.status === 'finished' && koFinal.winner_id != null && (koFinal.team1_id == teamId || koFinal.team2_id == teamId)) {
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
        if (isWaitingForGroup(t) || t.registration_status === 'pending' || t.registration_status === 'rejected') return false
        if (t.stage === 'challenger') return true
        if (isChallengerGroup(t.group_name) || t.group_name === '附加赛') return true
        return t.seed > 4   // 从挑战者组晋级上来的（直升的前4 seed 1-4）
      }).map(t => Object.assign({}, t, { stage_status: challengerStatusOf(t) }))
      // 传奇组 tab：所有参加过传奇组比赛的队伍（直升 + 晋级 + 传奇组及以后淘汰）
      const legendTab = teams.filter(t =>
        t.stage === 'legend' || t.stage === 'playoff' ||
        (t.stage === 'eliminated' && ['上区', '下区', '淘汰赛'].indexOf(t.group_name) >= 0)
      )
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
        if (isWaitingForGroup(t)) return 7
        if (t.registration_status === 'pending' || t.registration_status === 'rejected') return 8
        if (t.ko_rank === '冠军') return 0
        if (t.ko_rank === '亚军') return 1
        if (t.ko_rank === '四强') return 2
        if (t.ko_rank === '六强') return 3
        if (t.stage === 'legend') return 4
        if (t.stage === 'challenger') return 5
        return 6
      }
      const rankingTeams = teams.slice()
        .map(t => {
          const ko_rank = koRankOf(t.team_id)
          // 排名状态：淘汰赛名次 > 阶段状态 > 待分组
          let stage_text = '待分组'
          if (t.registration_status === 'pending') stage_text = '报名待审核'
          else if (t.registration_status === 'rejected') stage_text = '报名已驳回'
          else if (isWaitingForGroup(t)) stage_text = '待分组'
          else if (ko_rank) stage_text = ko_rank
          else if (t.stage === 'legend') stage_text = '传奇组'
          else if (t.stage === 'challenger') stage_text = '挑战者'
          else if (t.stage === 'playoff') stage_text = '已晋级'
          else if (t.stage === 'eliminated') stage_text = '已淘汰'
          return Object.assign({}, t, { ko_rank, stage_text })
        })
        .sort((a, b) => {
          const w = stageWeightOf(a) - stageWeightOf(b)
          if (w !== 0) return w
          if (b.wins !== a.wins) return b.wins - a.wins
          if ((b.diff || 0) !== (a.diff || 0)) return (b.diff || 0) - (a.diff || 0)
          return (b.rating || 0) - (a.rating || 0)
        })
      this.setData({
        match: Object.assign({}, detail.match, { statusText: this.statusText(detail.match.status) }),
        registrationSummary: this.describeRegistration(detail.match),
        teams,
        waitingTeams,
        rosterLocked,
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
        groupCount: format.groupCount,
        hasPlayoffStage: format.hasPlayoffStage,
        canFinishPlayoff: format.canFinishPlayoff,
        challengerFormatHint: format.challengerFormatHint,
        challengerStage: Math.min(this.data.challengerStage, challengerSwiper.length - 1),
        legendSwiper,
        allRounds: rounds
      })
    } catch (err) {
      this.showErr(err, '加载失败')
    }
  },

  // 写入成功后使共享角标过期；纯内存通知失败不能把已完成的审批误报为失败。
  invalidateAdminTodos() {
    try {
      require('../../../utils/admin-todos.js').invalidate()
    } catch (_) {
      // 页面返回时仍会按摘要缓存周期重试，不影响服务器已保存的结果。
    }
  },

  // 通过某队伍的报名记录（审核链修复：pending → approved，并自动创建赛事进度参与编排）
  async approveTeamReg(e) {
    const teamId = e.currentTarget.dataset.id
    const name = e.currentTarget.dataset.name || '该队伍'
    if (this.data.rosterLocked || isRosterLocked(this.data.match, this.data.teams, this.data.allRounds)) {
      wx.showToast({ title: '已分组，参赛名单已锁定', icon: 'none' })
      return
    }
    const team = this.data.teams.find(t => t.team_id == teamId)
    if (!team || team.registration_status !== 'pending') return
    wx.showModal({
      title: '通过报名',
      content: `确定通过「${name}」的报名审核吗？通过后显示为待分组。`,
      success: async (res) => {
        if (!res.confirm) return
        if (this.data.rosterLocked || isRosterLocked(this.data.match, this.data.teams, this.data.allRounds)) {
          wx.showToast({ title: '已分组，参赛名单已锁定', icon: 'none' })
          return
        }
        try {
          await api.approveTeamRegistration(this.data.matchId, teamId)
          this.invalidateAdminTodos()
          wx.showToast({ title: '已通过', icon: 'success' })
          await this.load()
        } catch (err) {
          this.showErr(err, '操作失败')
        }
      }
    })
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
    if (r && r.group_name === '附加赛' && !this.data.canFinishPlayoff) {
      wx.showToast({ title: '历史附加赛仅可查看', icon: 'none' })
      return
    }
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
    if (this.data.groupingBusy) return
    wx.showModal({
      title: '3 组含附加赛 / 4 组直晋',
      editable: true,
      placeholderText: '输入 3 或 4',
      success: async (res) => {
        if (!res.confirm || !res.content || this.data.groupingBusy) return
        const value = res.content.trim()
        if (value !== '3' && value !== '4') {
          wx.showToast({ title: '当前赛制仅支持 3 组或 4 组', icon: 'none' })
          return
        }
        const n = Number(value)
        // 自动生成 A, B, C... 组名
        const groups = []
        for (let i = 0; i < n; i++) groups.push(String.fromCharCode(65 + i))
        this.setData({ groupingBusy: true })
        try {
          // 种子与分组由同一个事务保存，分组失败时名单仍可继续审核。
          await api.seedAndGroup(this.data.matchId, groups)
          this.invalidateAdminTodos()
          // 即使重新加载失败，已成功分组的页面也立即关闭审批入口。
          this.setData({ rosterLocked: true })
          wx.showModal({
            title: '分组完成',
            content: `已分配种子并分为 ${groups.join('、')} 组\n` + (n === 3 ? '3 个小组冠军晋级，第 2 名参加附加赛。' : '4 个小组冠军直接晋级，无附加赛。'),
            showCancel: false
          })
          await this.load()
        } catch (err) {
          this.showErr(err, '分组失败')
        } finally {
          this.setData({ groupingBusy: false })
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
      const hasPlayoff = typeof d.has_playoff === 'boolean' ? d.has_playoff : (d.group_count || this.data.groupCount) === 3
      wx.showModal({
        title: '小组赛已结束',
        content: hasPlayoff
          ? `每组第1共 ${w} 队已晋级传奇组\n附加赛对阵已生成 ${n} 场，请录入附加赛比分`
          : `${w} 个小组冠军已晋级传奇组，请到「传奇组」进行分区。`,
        showCancel: false
      })
      this.load()
    } catch (err) {
      this.showErr(err, '结束小组赛失败')
    }
  },

  async finishPlayoff() {
    if (!this.data.canFinishPlayoff) {
      wx.showToast({ title: '当前赛制不需要推进附加赛', icon: 'none' })
      return
    }
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
  },

  // ===== 时间窗口管理 =====
  fmtDateTime(iso) {
    const m = String(iso || '').match(/^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})/)
    return m ? (m[2] + '-' + m[3] + ' ' + m[4] + ':' + m[5]) : (iso || '')
  },
  fmtDate(iso) {
    const m = String(iso || '').match(/^(\d{4})-(\d{2})-(\d{2})/)
    return m ? (m[1] + '-' + m[2] + '-' + m[3]) : ''
  },
  fmtTime(iso) {
    const m = String(iso || '').match(/[T ](\d{2}):(\d{2})/)
    return m ? (m[1] + ':' + m[2]) : ''
  },

  describeRegistration(match) {
    const display = value => this.fmtDate(value) + ' ' + this.fmtTime(value)
    return '开始：' + (match.register_start ? display(match.register_start) : '不限制') + ' · 截止：' + (match.register_end ? display(match.register_end) : '不限制')
  },
  openRegistrationPanel() {
    const match = this.data.match
    if (!match || this.data.registrationSaving) return
    this.setData({
      showRegistrationPanel: true,
      registrationStartDate: this.fmtDate(match.register_start),
      registrationStartTime: this.fmtTime(match.register_start),
      registrationEndDate: this.fmtDate(match.register_end),
      registrationEndTime: this.fmtTime(match.register_end)
    })
  },
  closeRegistrationPanel() {
    if (!this.data.registrationSaving) this.setData({ showRegistrationPanel: false })
  },
  onRegistrationInput(e) {
    if (this.data.registrationSaving) return
    const field = e.currentTarget.dataset.field
    if (['registrationStartDate', 'registrationStartTime', 'registrationEndDate', 'registrationEndTime'].indexOf(field) >= 0) {
      this.setData({ [field]: e.detail.value })
    }
  },
  clearRegistrationLimit(e) {
    if (this.data.registrationSaving) return
    const side = e.currentTarget.dataset.side
    if (side === 'start' || side === 'all') this.setData({ registrationStartDate: '', registrationStartTime: '' })
    if (side === 'end' || side === 'all') this.setData({ registrationEndDate: '', registrationEndTime: '' })
  },
  async saveRegistrationWindow() {
    if (this.data.registrationSaving) return
    const d = this.data
    if (!!d.registrationStartDate !== !!d.registrationStartTime || !!d.registrationEndDate !== !!d.registrationEndTime) {
      wx.showToast({ title: '请补全日期和时间，或清除该限制', icon: 'none' })
      return
    }
    // 保留选择的北京时间；不根据设备时区进行 Date/toISOString 转换。
    const start = d.registrationStartDate ? d.registrationStartDate + 'T' + d.registrationStartTime + ':00' : null
    const end = d.registrationEndDate ? d.registrationEndDate + 'T' + d.registrationEndTime + ':00' : null
    if (start && end && start >= end) {
      wx.showToast({ title: '报名开始时间需早于截止时间', icon: 'none' })
      return
    }
    this.setData({ registrationSaving: true })
    try {
      const updated = await api.updateRegistrationWindow(d.matchId, { register_start: start, register_end: end })
      // 此接口只更新报名时间；保留详情接口提供的报名数量等统计字段。
      const match = Object.assign({}, this.data.match, {
        register_start: updated.register_start,
        register_end: updated.register_end
      })
      match.statusText = this.statusText(match.status)
      this.setData({ match, registrationSummary: this.describeRegistration(match), showRegistrationPanel: false })
      wx.showToast({ title: '报名时间已保存', icon: 'success' })
    } catch (err) {
      this.showErr(err, '保存报名时间失败')
    } finally {
      this.setData({ registrationSaving: false })
    }
  },

  // 组数由实际分组决定，同时保留历史对阵及独立保存的排期范围。
  async openWindowPanel() {
    try {
      const savedWindows = await api.getStageWindows(this.data.matchId)
      const allRounds = this.data.allRounds || []
      const teamGroups = (this.data.teams || []).map(t => t.group_name).filter(Boolean)
      const roundGroups = allRounds.map(r => r.group_name).filter(Boolean)
      const savedGroups = (savedWindows || []).map(w => w.group_name).filter(Boolean)
      const currentGroups = new Set([...teamGroups, ...roundGroups])
      const groupNames = [...new Set([...teamGroups, ...roundGroups, ...savedGroups])]
      const format = this.challengerFormat(this.data.teams, allRounds)
      const stageWindows = groupNames.map(g => {
        const window = (savedWindows || []).find(w => w.group_name === g)
        const ws = window ? window.window_start : null
        const we = window ? window.window_end : null
        const historyOnly = g === '附加赛' && format.groupCount === 4
        const stage = historyOnly ? 'history' : g === '淘汰赛' ? 'knockout' : (g === '上区' || g === '下区') ? 'legend' : 'challenger'
        const groupLabel = /^[A-Z]$/.test(g) ? g + ' 组' : g
        const label = historyOnly ? '附加赛（历史，不适用当前 4 组）' : stage === 'knockout' ? '淘汰赛' : (stage === 'legend' ? '传奇组 · ' : '挑战者组 · ') + groupLabel
        return {
          group_name: g,
          stage,
          label,
          history_only: historyOnly,
          saved_only: !currentGroups.has(g),
          source_hint: g === '附加赛' && format.groupCount === 4
            ? '历史附加赛设置，不参与当前 4 组赛制'
            : !currentGroups.has(g) ? '已保存设置 · 当前无队伍或对阵' : '',
          window_start: ws,
          window_end: we,
          has_window: !!(ws && we),
          window_text: (ws && we) ? (this.fmtDateTime(ws) + ' ~ ' + this.fmtDateTime(we)) : '未限制比赛排期范围'
        }
      })
      const windowSections = [
        { key: 'challenger', title: '挑战者组', hint: format.challengerFormatHint + (format.groupCount === 3 ? '附加赛生成后可设置其排期范围。' : '') },
        { key: 'legend', title: '传奇组', hint: '上区、下区分别设置比赛排期范围。' },
        { key: 'knockout', title: '淘汰赛', hint: '此范围适用于淘汰赛对阵。' },
        { key: 'history', title: '历史设置', hint: '以下设置不参与当前赛制，仅保留查看。' }
      ].map(section => Object.assign({}, section, { windows: stageWindows.filter(w => w.stage === section.key) }))
        .filter(section => section.windows.length > 0)
      this.setData({ showWindowPanel: true, stageWindows, windowSections, windowEditGroup: null })
    } catch (err) {
      this.showErr(err, '加载时间窗口失败')
    }
  },
  closeWindowPanel() {
    if (this.data.windowSaving) return
    this.setData({ showWindowPanel: false, windowEditGroup: null })
  },

  // 打开某阶段的窗口编辑
  openWindowEdit(e) {
    const g = e.currentTarget.dataset.group
    const w = this.data.stageWindows.find(x => x.group_name === g)
    if (!w || w.history_only || this.data.windowSaving) return
    this.setData({
      windowEditGroup: g,
      windowEditLabel: w.label,
      windowStartDate: w && w.window_start ? this.fmtDate(w.window_start) : '',
      windowStartTime: w && w.window_start ? this.fmtTime(w.window_start) : '',
      windowEndDate: w && w.window_end ? this.fmtDate(w.window_end) : '',
      windowEndTime: w && w.window_end ? this.fmtTime(w.window_end) : ''
    })
  },
  cancelWindowEdit() {
    if (this.data.windowSaving) return
    this.setData({ windowEditGroup: null })
  },
  onStartDateChange(e) { this.setData({ windowStartDate: e.detail.value }) },
  onStartTimeChange(e) { this.setData({ windowStartTime: e.detail.value }) },
  onEndDateChange(e) { this.setData({ windowEndDate: e.detail.value }) },
  onEndTimeChange(e) { this.setData({ windowEndTime: e.detail.value }) },

  // 保存窗口
  async saveWindow() {
    if (this.data.windowSaving) return
    const { windowStartDate, windowStartTime, windowEndDate, windowEndTime, windowEditGroup } = this.data
    if (!windowStartDate || !windowStartTime || !windowEndDate || !windowEndTime) {
      wx.showToast({ title: '请填写完整的起止时间', icon: 'none' })
      return
    }
    const start = windowStartDate + 'T' + windowStartTime + ':00'
    const end = windowEndDate + 'T' + windowEndTime + ':00'
    if (start >= end) {
      wx.showToast({ title: '开始时间需早于结束时间', icon: 'none' })
      return
    }
    if (!windowEditGroup) return
    this.setData({ windowSaving: true })
    try {
      await api.setStageWindow(this.data.matchId, windowEditGroup, { window_start: start, window_end: end })
      wx.showToast({ title: '窗口已保存', icon: 'success' })
      this.setData({ showWindowPanel: false, windowEditGroup: null })
      this.load()
    } catch (err) {
      this.showErr(err, '保存失败')
    } finally {
      this.setData({ windowSaving: false })
    }
  }
})
