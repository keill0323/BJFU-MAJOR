/**
 * 赛事详情页（管理员/审核员）
 * 功能：按阶段分 tab 管理赛事 —— 赛事队伍 / 挑战者组 / 传奇组 / 淘汰赛
 * 入口：管理后台 → 赛事 → 点击赛事卡片
 */
const api = require('../../../utils/api.js')
const { isWaitingForGroup, isRosterLocked, buildTeamRanking, buildTeamHistory } = require('../../../utils/tournament.js')

Page({
  data: {
    matchId: null,
    match: null,
    tab: 'teams',           // teams / challenger / legend / knockout
    roundFilter: 'all',
    roundDate: '',
    roundGroup: '',
    roundGroups: [],
    filteredRounds: [],
    roundFilterOptions: [{ id: 'all', label: '全部' }, { id: 'today', label: '今天' }, { id: 'pending', label: '待录分' }, { id: 'unscheduled', label: '未排期' }],
    roundCounts: { all: 0, today: 0, pending: 0, unscheduled: 0 },
    expandedSections: {},
    // 队伍
    teams: [],
    waitingTeams: [],
    rosterLocked: false,
    groupingBusy: false,
    generatingMatches: false,
    legendTeams: [],        // 传奇组（含直升+晋级）
    challengerTeams: [],    // 挑战者组（小组赛阶段）
    playoffTeams: [],       // 淘汰赛（6强）
    eliminatedTeams: [],    // 已淘汰队伍
    rankingTeams: [],       // 队伍排名列表
    // 对阵（按阶段）
    challengerRounds: [],
    legendRounds: [],
    knockoutRounds: [],
    knockoutGroups: [],     // 淘汰赛对阵按轮次分组（1/4决赛/半决赛/决赛）
    knockoutHint: '',       // 淘汰赛流程提示
    // 阶段分页数据，列表随页面滚动，不使用限高 swiper。
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
    historyTab: 'profile',
    historyTeam: null,
    historyRounds: [],
    historySections: [],
    // 比分输入面板
    showScoreInput: false,
    scoreRoundId: null,
    scoreInput: '',
    scoreSaving: false,
    scoreKeyboardHeight: 0,
    scorePanelHeight: 400,
    scoreFocusId: '',
    scoreTeam1Name: '',
    scoreTeam2Name: '',
    scoreMatchLabel: '',
    // 管理员逐场排期；提交时携带打开面板时的原值，防止覆盖其他管理员修改。
    showRoundSchedule: false,
    roundScheduleId: null,
    roundScheduleExpectedTime: null,
    roundScheduleDate: '',
    roundScheduleTime: '',
    roundScheduleTeams: '',
    roundScheduleLabel: '',
    roundScheduleCurrentText: '',
    roundScheduleSaving: false,
    roundScheduleConflict: false,
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
    this._pageUnloaded = false
    this.setData({ matchId: options.matchId, tab: options.tab === 'teams' ? 'teams' : 'schedule' })
    this.load()
  },

  onUnload() {
    this._pageUnloaded = true
  },

  switchTab(e) {
    const tab = e.currentTarget.dataset.tab
    if (['schedule', 'teams', 'challenger', 'legend', 'knockout'].indexOf(tab) < 0) return
    this.setData({ tab })
  },

  toggleAdminSection(e) {
    const section = e.currentTarget.dataset.section
    if (['challengerRoster', 'challengerFlow', 'legendRoster', 'legendFlow', 'knockoutRoster', 'knockoutFlow'].indexOf(section) < 0) return
    this.setData({ expandedSections: Object.assign({}, this.data.expandedSections, { [section]: !this.data.expandedSections[section] }) })
  },
  beijingToday() {
    return new Date(Date.now() + 8 * 60 * 60 * 1000).toISOString().slice(0, 10)
  },
  filterRoundList() {
    const today = this.beijingToday()
    const playable = round => !!round.team1_id && !!round.team2_id
    const confirmed = round => !!round.scheduled_time && (round.schedule_status === 'confirmed' || (round.team1_confirmed && round.team2_confirmed))
    const pending = round => playable(round) && round.status !== 'finished'
    const isToday = round => confirmed(round) && this.fmtDate(round.scheduled_time) === today
    const unscheduled = round => round.status === 'pending' && playable(round) && !confirmed(round)
    const rounds = this.data.allRounds || []
    const counts = { all: rounds.length, today: rounds.filter(isToday).length, pending: rounds.filter(pending).length, unscheduled: rounds.filter(unscheduled).length }
    const filtered = rounds.filter(round => {
      if (this.data.roundGroup && round.group_name !== this.data.roundGroup) return false
      if (this.data.roundDate && (!confirmed(round) || this.fmtDate(round.scheduled_time) !== this.data.roundDate)) return false
      if (this.data.roundFilter === 'today') return isToday(round)
      if (this.data.roundFilter === 'pending') return pending(round)
      if (this.data.roundFilter === 'unscheduled') return unscheduled(round)
      return true
    }).slice().sort((left, right) => {
      const leftTime = confirmed(left) ? left.scheduled_time : '9999'
      const rightTime = confirmed(right) ? right.scheduled_time : '9999'
      return leftTime.localeCompare(rightTime) || String(left.group_name || '').localeCompare(String(right.group_name || '')) || left.round_number - right.round_number || left.id - right.id
    }).map(round => Object.assign({}, round, {
      admin_readonly: round.group_name === '附加赛' && this.data.groupCount !== 3,
      admin_can_schedule: round.admin_can_schedule && !(round.group_name === '附加赛' && this.data.groupCount !== 3),
      admin_group_label: /^[A-Z]$/.test(round.group_name || '') ? round.group_name + ' 组' : round.group_name || '未分组',
      admin_status_label: round.status === 'finished' ? '已录分' : round.status === 'in_progress' ? '进行中' : '待比赛',
      admin_format_label: round.group_name === '淘汰赛' ? 'BO3' : 'BO1'
    }))
    this.setData({ filteredRounds: filtered, roundCounts: counts })
  },
  setRoundFilter(e) {
    const filter = e.currentTarget.dataset.filter
    if (!this.data.roundFilterOptions.some(item => item.id === filter)) return
    this.setData({ roundFilter: filter, roundDate: '' })
    this.filterRoundList()
  },
  setRoundGroup(e) {
    const group = e.currentTarget.dataset.group || ''
    if (group && !this.data.roundGroups.some(item => item.value === group)) return
    this.setData({ roundGroup: group })
    this.filterRoundList()
  },
  setRoundDate(e) {
    this.setData({ roundDate: e.detail.value, roundFilter: 'all' })
    this.filterRoundList()
  },
  resetRoundFilters() {
    this.setData({ roundFilter: 'all', roundDate: '', roundGroup: '' })
    this.filterRoundList()
  },

  // 阶段切换（挑战者组/传奇组）
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
      if (this._pageUnloaded) return
      const teams = detail.teams || []
      const rounds = (detail.rounds || []).map(round => Object.assign({}, round, {
        admin_schedule_text: this.roundScheduleDisplay(round),
        admin_can_schedule: round.status === 'pending' && !!round.team1_id && !!round.team2_id
      }))
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
            ko_label: i === 0 ? '第一场' : '第二场',
            bo3_text: fmtBo3(r)
          }))
        })
      }
      if (koSorted.length > 2) {
        koGroups.push({
          title: '半决赛',
          rounds: koSorted.slice(2, 4).map((r, i) => Object.assign({}, r, {
            ko_label: i === 0 ? '上区第1 vs 第二场胜者' : '下区第1 vs 第一场胜者',
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

      const rankingTeams = buildTeamRanking(teams, rounds)
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
      const roundGroups = [...new Set(rounds.map(round => round.group_name).filter(Boolean))].sort().map(value => ({ value, label: /^[A-Z]$/.test(value) ? value + ' 组' : value }))
      this.setData({ roundGroups, roundGroup: roundGroups.some(item => item.value === this.data.roundGroup) ? this.data.roundGroup : '' })
      this.filterRoundList()
    } catch (err) {
      if (!this._pageUnloaded) this.showErr(err, '加载失败')
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
    if (this._pageUnloaded || this.data.scoreSaving) return
    const rid = e.currentTarget.dataset.id
    // 判断该对阵是否淘汰赛（BO3）
    const r = []
      .concat(this.data.challengerRounds)
      .concat(this.data.legendRounds)
      .concat(this.data.knockoutRounds)
      .find(x => x.id == rid)
    if (!r) return
    if (r && r.group_name === '附加赛' && !this.data.canFinishPlayoff) {
      wx.showToast({ title: '历史附加赛仅可查看', icon: 'none' })
      return
    }
    const isBo3 = !!(r && r.group_name === '淘汰赛')
    let windowHeight = 700
    try {
      const info = typeof wx.getWindowInfo === 'function' ? wx.getWindowInfo() : null
      if (info && Number(info.windowHeight) > 0) windowHeight = Number(info.windowHeight)
    } catch (_) {
      // 不支持窗口信息的旧基础库继续使用可滚动面板。
    }
    this._scoreWindowHeight = windowHeight
    this.setData({
      scoreRoundId: rid,
      isBo3,
      scoreInput: r.status === 'finished' && !isBo3 ? r.team1_score + '-' + r.team2_score : '',
      bo3Games: [0, 1, 2].map(index => r.status === 'finished' && r.bo3_scores && r.bo3_scores[index] ? r.bo3_scores[index].t1 + '-' + r.bo3_scores[index].t2 : ''),
      scoreSaving: false,
      scoreKeyboardHeight: 0,
      scorePanelHeight: Math.min(Math.floor(windowHeight * 0.82), isBo3 ? 520 : 400),
      scoreFocusId: '',
      scoreTeam1Name: r.team1_name || '队伍一',
      scoreTeam2Name: r.team2_name || '队伍二',
      scoreMatchLabel: `${r.group_name || ''}${/^[A-Z]$/.test(r.group_name || '') ? ' 组' : ''}${r.round_number ? ' · 第 ' + r.round_number + ' 场' : ''}`,
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
  onScoreFocus(e) {
    this.setData({ scoreFocusId: e.currentTarget.dataset.focusId || '' })
  },
  onScoreKeyboardHeightChange(e) {
    if (this._pageUnloaded || !this.data.showScoreInput) return
    const windowHeight = this._scoreWindowHeight || 700
    const height = Math.max(0, Math.min(Number(e.detail.height) || 0, windowHeight))
    const focusId = this.data.scoreFocusId
    // 输入框不再推起整个页面；面板避让键盘，表单独立滚动，按钮固定在面板底部。
    this.setData({
      scoreKeyboardHeight: height,
      scorePanelHeight: Math.max(0, Math.min(Math.floor(windowHeight * 0.82), this.data.isBo3 ? 520 : 400, windowHeight - height - 12)),
      scoreFocusId: ''
    }, () => {
      if (!this._pageUnloaded && this.data.showScoreInput) this.setData({ scoreFocusId: focusId })
    })
  },
  closeScoreInput() {
    if (this.data.scoreSaving) return
    if (typeof wx.hideKeyboard === 'function') wx.hideKeyboard()
    this.setData({ showScoreInput: false, scoreRoundId: null, scoreInput: '', bo3Games: ['', '', ''], scoreKeyboardHeight: 0, scoreFocusId: '' })
  },
  // 空处理器：阻止面板内点击冒泡到外层遮罩
  noop() {},
  async submitScore() {
    const rid = this.data.scoreRoundId
    if (this._pageUnloaded || !rid || this.data.scoreSaving) return
    this.setData({ scoreSaving: true })
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
      if (this._pageUnloaded) return
      if (typeof wx.hideKeyboard === 'function') wx.hideKeyboard()
      this.setData({ showScoreInput: false, scoreRoundId: null, scoreInput: '', bo3Games: ['', '', ''], scoreKeyboardHeight: 0, scoreFocusId: '' })
      wx.showToast({ title: '比分已更新', icon: 'success' })
      this.load()
    } catch (err) {
      if (!this._pageUnloaded) this.showErr(err, '更新失败')
    } finally {
      if (!this._pageUnloaded) this.setData({ scoreSaving: false })
    }
  },

  // ===== 管理员逐场排期 =====
  roundScheduleDisplay(round) {
    if (!round.scheduled_time) return '待管理员安排'
    const time = this.fmtDateTime(round.scheduled_time) + '（北京时间）'
    const confirmed = round.schedule_status === 'confirmed' || (round.team1_confirmed && round.team2_confirmed)
    return confirmed ? time : '历史提议 ' + time + '，待管理员确认安排'
  },
  openRoundSchedule(e) {
    if (this._pageUnloaded || this.data.roundScheduleSaving) return
    const round = this.data.allRounds.find(item => item.id == e.currentTarget.dataset.id)
    if (!round || round.status !== 'pending' || !round.team1_id || !round.team2_id) return
    this.setData({
      showRoundSchedule: true,
      roundScheduleId: round.id,
      roundScheduleExpectedTime: round.scheduled_time || null,
      roundScheduleDate: this.fmtDate(round.scheduled_time),
      roundScheduleTime: this.fmtTime(round.scheduled_time),
      roundScheduleTeams: (round.team1_name || '队伍一') + ' vs ' + (round.team2_name || '队伍二'),
      roundScheduleLabel: (round.group_name || '') + (/^[A-Z]$/.test(round.group_name || '') ? ' 组' : '') + (round.round_number ? ' · 第 ' + round.round_number + ' 场' : ''),
      roundScheduleCurrentText: round.scheduled_time ? this.roundScheduleDisplay(round) : '尚未安排',
      roundScheduleConflict: false
    })
  },
  closeRoundSchedule() {
    if (this._pageUnloaded || this.data.roundScheduleSaving) return
    this.setData({ showRoundSchedule: false, roundScheduleId: null, roundScheduleConflict: false })
  },
  onRoundScheduleInput(e) {
    if (this._pageUnloaded || this.data.roundScheduleSaving) return
    const field = e.currentTarget.dataset.field
    if (field === 'roundScheduleDate' || field === 'roundScheduleTime') {
      this.setData({ [field]: e.detail.value })
    }
  },
  async refreshRoundSchedule() {
    if (this._pageUnloaded || this.data.roundScheduleSaving) return
    const roundId = this.data.roundScheduleId
    this.setData({ roundScheduleSaving: true })
    try {
      // 独立读取；加载失败时不得把旧的 expected_time 误标为已刷新。
      const detail = await api.adminMatchDetail(this.data.matchId)
      if (this._pageUnloaded || !this.data.showRoundSchedule) return
      const round = (detail.rounds || []).find(item => item.id == roundId)
      if (!round || round.status !== 'pending' || !round.team1_id || !round.team2_id) {
        this.setData({ showRoundSchedule: false, roundScheduleId: null })
        wx.showToast({ title: '该场已不可排期，请查看最新对阵', icon: 'none' })
        await this.load()
        return
      }
      // 保留草稿，显示服务器当前值，由管理员再次点击保存才写入。
      this.setData({
        roundScheduleExpectedTime: round.scheduled_time || null,
        roundScheduleCurrentText: round.scheduled_time ? this.roundScheduleDisplay(round) : '尚未安排',
        roundScheduleConflict: false
      })
      wx.showToast({ title: '当前排期已刷新，请核对后保存', icon: 'none' })
    } catch (err) {
      if (!this._pageUnloaded) this.showErr(err, '刷新排期失败，请重试')
    } finally {
      if (!this._pageUnloaded) this.setData({ roundScheduleSaving: false })
    }
  },
  saveRoundSchedule() {
    if (this._pageUnloaded || this.data.roundScheduleSaving || this.data.roundScheduleConflict) return
    const date = this.data.roundScheduleDate
    const time = this.data.roundScheduleTime
    if (!date || !time) {
      wx.showToast({ title: '请选择完整的比赛日期和时间', icon: 'none' })
      return
    }
    // 按北京时间提交，不经过手机本地时区转换。
    return this.writeRoundSchedule(date + 'T' + time + ':00')
  },
  clearRoundSchedule() {
    return this.writeRoundSchedule(null)
  },
  async writeRoundSchedule(scheduledTime) {
    if (this._pageUnloaded || this.data.roundScheduleSaving || this.data.roundScheduleConflict || !this.data.roundScheduleId) return
    const roundId = this.data.roundScheduleId
    const expectedTime = this.data.roundScheduleExpectedTime
    this.setData({ roundScheduleSaving: true })
    try {
      await api.adminScheduleRound(roundId, scheduledTime, expectedTime)
      if (this._pageUnloaded) return
      this.setData({ showRoundSchedule: false, roundScheduleId: null, roundScheduleConflict: false })
      wx.showToast({ title: scheduledTime ? '比赛时间已安排' : '比赛时间已清除', icon: 'success' })
      await this.load()
    } catch (err) {
      if (this._pageUnloaded) return
      if (err && Number(err.statusCode) === 409) {
        this.setData({ roundScheduleConflict: true })
        wx.showModal({ title: '排期已变更', content: '这场比赛的时间已被修改。你的输入已保留，请先点击「刷新当前排期」，核对后再保存。', showCancel: false })
      } else {
        this.showErr(err, '保存比赛时间失败，请重试')
      }
    } finally {
      if (!this._pageUnloaded) this.setData({ roundScheduleSaving: false })
    }
  },

  // ===== 队伍历史对阵 =====
  showTeam(e) {
    const tid = e.currentTarget.dataset.id
    if (!tid) return
    const allRounds = this.data.allRounds
    const historyRounds = allRounds.filter(r => r.team1_id == tid || r.team2_id == tid)
    const source = historyRounds[0]
    const historyTeam = this.data.rankingTeams.find(t => t.team_id == tid) || {
      team_id: tid, team_name: source ? (source.team1_id == tid ? source.team1_name : source.team2_name) : '队伍'
    }
    this.setData({ showHistory: true, historyTab: 'profile', historyTeam, historyRounds,
      historySections: buildTeamHistory(tid, allRounds) })
  },
  switchHistoryTab(e) {
    const tab = e.currentTarget.dataset.tab
    if (tab === 'profile' || tab === 'history') this.setData({ historyTab: tab })
  },
  closeHistory() {
    this.setData({ showHistory: false, historyTeam: null, historyRounds: [], historySections: [] })
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

  // ③ 生成对阵：一键为所有小组生成双循环 BO1（已生成的组自动跳过）
  async generateAllMatches() {
    if (this.data.generatingMatches) return
    const groups = [...new Set(this.data.challengerTeams.map(t => t.group_name).filter(Boolean))]
    if (groups.length < 2) {
      wx.showToast({ title: '请先分组再生成对阵', icon: 'none' })
      return
    }
    this.setData({ generatingMatches: true })
    try {
      let made = 0
      for (const g of groups) {
        // 该组已有对阵则跳过，避免重复生成
        const existing = this.data.challengerRounds.filter(r => r.group_name === g)
        if (existing.length > 0) continue
        await api.autoMatches(this.data.matchId, g, false)
        made += 1
      }
      wx.showToast({ title: made > 0 ? `已为 ${made} 组生成双循环 BO1` : '各组对阵已存在', icon: 'success' })
    } catch (err) {
      this.showErr(err, '生成对阵失败')
    } finally {
      // 一部分组成功后，后续组可能失败；也要同步已有对阵，避免再次提交成功的组。
      try {
        await this.load()
      } finally {
        this.setData({ generatingMatches: false })
      }
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
