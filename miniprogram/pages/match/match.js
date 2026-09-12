/**
 * 赛事详情页（用户端）
 * 功能：完整赛事资料展示（队伍状态/排名/小组赛/传奇组/淘汰赛BO3）+ 报名
 * 进入方式：首页点赛事卡片，带 ?id=xx 参数
 */
const api = require('../../utils/api.js')
const tournament = require('../../utils/tournament.js')

// 只有后续赛段使用保留名称；挑战者小组采用赛事实际设置的名称与数量。
function isChallengerGroup(groupName) {
  return typeof groupName === 'string' && groupName.trim() !== '' &&
    ['附加赛', '上区', '下区', '淘汰赛'].indexOf(groupName) < 0
}

Page({
  data: {
    match: null,
    loadFailed: false,
    roundCount: 0,
    completedRoundCount: 0,
    registerEndText: '',
    tab: 'teams',            // teams / challenger / legend / knockout
    // 队伍
    teams: [],
    waitingTeams: [],
    challengerTeams: [],
    legendTeams: [],
    playoffTeams: [],
    showHistory: false,
    historyTeam: null,
    historyRounds: [],
    historySections: [],
    allRounds: [],
    rankingTeams: [],        // 队伍排名
    // 对阵（按阶段）
    challengerSwiper: [],    // 挑战者组：小组赛分组，三组赛制另有附加赛
    groupCount: 0,
    hasPlayoffStage: false,
    legendSwiper: [],        // 传奇组：上区/下区
    knockoutGroups: [],      // 淘汰赛 BO3：1/4决赛/半决赛/决赛
    challengerStage: 0,
    legendStage: 0,
    // 我的报名
    myReg: null,
    registered: false,
    myTeam: null,
    isCaptain: false,
    canRegisterTeam: false,
    rosterLocked: false,
    // BO3 小局详情弹层
    showBo3: false,
    bo3Detail: null,
    // 时间协商
    myTeamId: null,
    showSchedule: false,
    scheduleRound: null,
    scheduleDate: '',
    scheduleTime: ''
  },

  // 页面加载：拉完整赛事资料 + 报名状态
  async onLoad(options) {
    const id = options.id
    if (!id) return
    this.matchId = id
    this._notificationRoundId = Number(options.roundId) || null
    const requestId = this._detailRequestId = (this._detailRequestId || 0) + 1
    try {
      const detail = await api.getMatchDetail(id)
      if (requestId !== this._detailRequestId) return
      this.detail = detail
      this.setData({
        match: Object.assign({}, detail.match, { statusText: this.statusText(detail.match.status) })
      })
      this.buildData(detail, null, false)
      // 已登录才拉我的报名状态；未登录允许先浏览详情，不强制登录
      if (wx.getStorageSync('token')) {
        await this.loadMyStatus()
      }
      if (this._notificationRoundId) this.openNotificationRound()
      else this.checkRulePrompt()
    } catch (err) {
      if (requestId !== this._detailRequestId) return
      this.setData({ loadFailed: true })
      wx.showToast({ title: '加载失败', icon: 'error' })
    }
  },

  // 下拉刷新（重新拉详情，不重复弹规则）
  async onPullDownRefresh() {
    const matchId = (this.data.match && this.data.match.id) || this.matchId
    if (!matchId) {
      wx.stopPullDownRefresh()
      return
    }
    const requestId = this._detailRequestId = (this._detailRequestId || 0) + 1
    try {
      const detail = await api.getMatchDetail(matchId)
      if (requestId !== this._detailRequestId) return
      this.detail = detail
      this.setData({
        match: Object.assign({}, detail.match, { statusText: this.statusText(detail.match.status) })
      })
      this.buildData(detail, this.data.myTeamId, this.data.isCaptain)
      if (wx.getStorageSync('token')) {
        await this.loadMyStatus()
      }
      if (this._notificationRoundId) this.openNotificationRound()
    } catch (err) {
      if (requestId === this._detailRequestId) wx.showToast({ title: '刷新失败', icon: 'error' })
    } finally {
      wx.stopPullDownRefresh()
    }
  },

  // 打开比赛卡片时弹出规则提示（「不再提示」按每场比赛独立记忆）
  checkRulePrompt() {
    const match = this.data.match
    if (!match || !match.id) return
    const key = `no_rule_prompt_${match.id}`   // 每场比赛一个开关
    if (wx.getStorageSync(key)) return
    const isFreshman = match.match_type === 'freshman'
    const QQ = '3761215994'
    const content = isFreshman
      ? `这是新生赛，要求队伍至少 3 名新生（大一、大二为新生）。\n研一、博一需联系管理员认证为新生。\n管理员QQ：${QQ}`
      : `请遵守比赛规则，文明竞技。报名需完成学籍认证。\n如有问题请联系管理员QQ：${QQ}`
    wx.showModal({
      title: isFreshman ? '新生赛规则' : '赛事规则',
      content,
      confirmText: '知道了',
      cancelText: '不再提示',
      success: (res) => {
        if (res.cancel) {
          wx.setStorageSync(key, true)
        }
      }
    })
  },

  statusText(status) {
    const map = { draft: '草稿', registering: '报名中', in_progress: '进行中', finished: '已结束' }
    return map[status] || status
  },

  // 组装展示数据（复用管理端逻辑，仅只读展示）
  buildData(detail, myTeamId, isCaptain) {
    const teams = detail.teams || []
    const rounds = (detail.rounds || []).map(r => this.decorateRound(r, myTeamId, isCaptain))
    const rosterLocked = tournament.isRosterLocked(detail.match || this.data.match, teams, rounds)
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
    const groupRounds = Object.create(null)
    rounds.filter(r => isChallengerGroup(r.group_name)).forEach(r => {
      ;(groupRounds[r.group_name] = groupRounds[r.group_name] || []).push(r)
    })
    // 晋级后队伍的 group_name 会变更，历史对阵仍保留最初的小组名称。
    const groupNames = Object.create(null)
    teams.concat(rounds).forEach(item => {
      if (isChallengerGroup(item.group_name)) groupNames[item.group_name] = true
    })
    const groupCount = Object.keys(groupNames).length
    const playoffRounds = rounds.filter(r => r.group_name === '附加赛')
    // 已确认四组时不显示附加赛；仅未知原分组数的旧赛事兼容历史附加赛。
    const hasPlayoffStage = groupCount === 3 || (groupCount === 0 && playoffRounds.length > 0)
    const challengerSwiper = [{
      title: '小组赛',
      type: 'group',
      groups: groupRounds,
      count: Object.keys(groupRounds).reduce((s, k) => s + groupRounds[k].length, 0)
    }]
    if (hasPlayoffStage) challengerSwiper.push({ title: '附加赛', type: 'playoff', rounds: playoffRounds })
    const previousStage = (this.data.challengerSwiper[this.data.challengerStage] || {}).type
    const challengerStage = Math.max(0, challengerSwiper.findIndex(item => item.type === previousStage))
    const legendSwiper = [
      { title: '上区', rounds: rounds.filter(r => r.group_name === '上区') },
      { title: '下区', rounds: rounds.filter(r => r.group_name === '下区') }
    ]

    const legendTeams = teams.filter(t => t.stage === 'legend')
    const waitingTeams = teams.filter(tournament.isWaitingForGroup)
    const challengerTeams = teams.filter(t => t.stage === 'challenger' && !tournament.isWaitingForGroup(t))
    const playoffTeams = teams.filter(t => t.stage === 'playoff')
    const rankingTeams = tournament.buildTeamRanking(teams, rounds)

    this.setData({
      loadFailed: false,
      roundCount: rounds.length,
      completedRoundCount: rounds.filter(r => r.status === 'finished').length,
      registerEndText: this.fmtTime(detail.match && detail.match.register_end),
      teams,
      waitingTeams,
      rosterLocked,
      canRegisterTeam: isCaptain && !rosterLocked && (!this.data.registered || !this.data.myReg || this.data.myReg.team_id !== myTeamId),
      challengerTeams,
      legendTeams,
      playoffTeams,

      rankingTeams,
      allRounds: rounds,
      challengerSwiper,
      challengerStage,
      groupCount,
      hasPlayoffStage,
      legendSwiper,
      knockoutGroups: koGroups
    })
  },

  showTeam(e) {
    const tid = e.currentTarget.dataset.id
    if (!tid) return
    const allRounds = this.data.allRounds
    const historyRounds = allRounds.filter(r => r.team1_id == tid || r.team2_id == tid)
    const source = historyRounds[0]
    const historyTeam = this.data.rankingTeams.find(t => t.team_id == tid) || {
      team_id: tid, team_name: source ? (source.team1_id == tid ? source.team1_name : source.team2_name) : '队伍'
    }
    this.setData({ showHistory: true, historyTeam, historyRounds,
      historySections: tournament.buildTeamHistory(tid, allRounds) })
  },
  closeHistory() {
    this.setData({ showHistory: false, historyTeam: null, historyRounds: [], historySections: [] })
  },

  switchTab(e) { this.setData({ tab: e.currentTarget.dataset.tab }) },
  selectChallengerStage(value) {
    const index = Number(value)
    this.setData({ challengerStage: Number.isInteger(index) && index >= 0 && index < this.data.challengerSwiper.length ? index : 0 })
  },
  switchChallengerStage(e) { this.selectChallengerStage(e.currentTarget.dataset.idx) },
  onChallengerSwiper(e) { this.selectChallengerStage(e.detail.current) },
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
    const matchId = this.data.match.id
    const requestId = this._statusRequestId = (this._statusRequestId || 0) + 1
    const detailRequestId = this._detailRequestId
    const isCurrent = () => requestId === this._statusRequestId && detailRequestId === this._detailRequestId &&
      this.data.match && this.data.match.id === matchId
    try {
      const regData = await api.getMyRegistration(matchId)
      if (!isCurrent()) return
      // 查我的队伍（判断报名类型用）
      const team = await api.getMyTeam()
      if (!isCurrent()) return
      let isCaptain = false
      let myTeamId = null
      if (team) {
        // 用 /me 接口拿当前用户 id（比解析 JWT 更可靠）
        const me = await api.getMe()
        if (!isCurrent()) return
        const userId = me ? me.id : null
        isCaptain = team.captain_id === userId
        myTeamId = team.id
      }
      // 个人报名不代表新创建的队伍已经报名，队长仍需提交整队审核。
      const registration = regData.registration
      const rosterLocked = tournament.isRosterLocked(this.data.match, this.data.teams, this.detail && this.detail.rounds)
      const canRegisterTeam = isCaptain && !rosterLocked && (!regData.registered || !registration || registration.team_id !== myTeamId)
      this.setData({ registered: regData.registered, myReg: registration, myTeam: team || null, isCaptain, myTeamId, canRegisterTeam, rosterLocked })
      // 拿到队长身份后重新装饰对阵（标记可操作的对阵）
      if (this.detail) {
        this.buildData(this.detail, myTeamId, isCaptain)
      }
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
  isTeamRegistrationLocked() {
    return !!this.data.myTeam && (this.data.rosterLocked || tournament.isRosterLocked(this.data.match, this.data.teams, this.detail && this.detail.rounds))
  },
  showRosterLocked() {
    wx.showModal({ title: '参赛名单已锁定', content: '赛事已完成编排，不再接受新队伍报名。', showCancel: false })
  },
  async handleRegister() {
    if (!this.data.match) return
    const match = this.data.match

    // 未登录：先引导登录（浏览详情不强制，报名时才需要）
    if (!wx.getStorageSync('token')) {
      wx.showModal({
        title: '请先登录',
        content: '报名需要先登录小程序。',
        confirmText: '去登录',
        success: (r) => { if (r.confirm) wx.navigateTo({ url: '/pages/login/login' }) }
      })
      return
    }

    // 只有报名中的赛事能报
    if (match.status !== 'registering') {
      wx.showToast({ title: '当前不可报名', icon: 'none' })
      return
    }

    if (this.isTeamRegistrationLocked()) {
      this.showRosterLocked()
      return
    }

    // 报名前检查学籍认证（未认证直接拦截，体验优于等后端报错）
    try {
      const me = await api.getMe()
      if (me && !me.is_verified) {
        wx.showModal({
          title: '未完成认证',
          content: '报名需先完成学籍认证（上传学信网/教务系统/校园卡截图）。',
          confirmText: '去认证',
          success: (r) => { if (r.confirm) wx.reLaunch({ url: '/pages/verify/verify' }) }
        })
        return
      }
      // 报名前检查段位认证（防炸鱼）：未认证段位直接拦截
      if (me && !me.rank) {
        wx.showModal({
          title: '未完成段位认证',
          content: '报名需先完成段位认证（上传完美/5E 平台段位截图，AI 识别段位，防止炸鱼）。',
          confirmText: '去认证',
          success: (r) => { if (r.confirm) wx.reLaunch({ url: '/pages/verify/verify' }) }
        })
        return
      }
    } catch (e) { /* 忽略，交给后端二次校验 */ }

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
        // 确认弹窗打开后也可能刷新到已锁定名单，提交前再检查一次。
        if (this.isTeamRegistrationLocked()) {
          this.showRosterLocked()
          return
        }
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
  },

  // ===== 时间协商 =====
  fmtTime(iso) {
    const m = String(iso || '').match(/^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})/)
    return m ? (m[2] + '-' + m[3] + ' ' + m[4] + ':' + m[5]) : (iso || '')
  },
  fmtDate(iso) {
    const m = String(iso || '').match(/^(\d{4})-(\d{2})-(\d{2})/)
    return m ? (m[1] + '-' + m[2] + '-' + m[3]) : ''
  },
  fmtTimeOnly(iso) {
    const m = String(iso || '').match(/[T ](\d{2}):(\d{2})/)
    return m ? (m[1] + ':' + m[2]) : ''
  },

  // 装饰对阵：时间状态文本 + 我的操作权限
  decorateRound(r, myTeamId, isCaptain) {
    let schedule_text = '未约定时间'
    if (r.schedule_status === 'confirmed') schedule_text = '已确定 ' + this.fmtTime(r.scheduled_time)
    else if (r.schedule_status === 'pending') schedule_text = '待确认 ' + this.fmtTime(r.scheduled_time)
    const window_text = (r.window_start && r.window_end)
      ? (this.fmtTime(r.window_start) + ' ~ ' + this.fmtTime(r.window_end))
      : ''
    let can_operate = false
    let my_side = null
    let i_confirmed = false
    if (isCaptain && myTeamId) {
      if (r.team1_id === myTeamId) my_side = 'team1'
      else if (r.team2_id === myTeamId) my_side = 'team2'
      can_operate = !!my_side && r.status === 'pending' && !!r.team1_id && !!r.team2_id
    }
    if (my_side === 'team1') i_confirmed = !!r.team1_confirmed
    else if (my_side === 'team2') i_confirmed = !!r.team2_confirmed
    return Object.assign({}, r, { schedule_text, window_text, can_operate, my_side, i_confirmed })
  },

  // 打开时间协商面板
  openNotificationRound() {
    const roundId = this._notificationRoundId
    this._notificationRoundId = null
    const round = (this.detail && this.detail.rounds || []).find(r => r.id === roundId)
    if (!round) {
      wx.showToast({ title: '该对阵已移除，请查看最新赛程', icon: 'none' })
      return
    }
    const tab = round.group_name === '淘汰赛' ? 'knockout' : ['上区', '下区'].includes(round.group_name) ? 'legend' : 'challenger'
    const updates = { tab }
    if (tab === 'legend') updates.legendStage = Math.max(0, this.data.legendSwiper.findIndex(s => s.rounds.some(r => r.id === roundId)))
    if (tab === 'challenger') updates.challengerStage = Math.max(0, this.data.challengerSwiper.findIndex(s => (s.rounds || []).some(r => r.id === roundId)))
    this.setData(updates)
    const current = this.decorateRound(round, this.data.myTeamId, this.data.isCaptain)
    if (current.can_operate) this.openSchedule({ currentTarget: { dataset: { id: roundId } } })
    else wx.showToast({ title: round.status !== 'pending' ? '该对阵已开始或结束' : '仅当前参赛队长可处理约赛', icon: 'none' })
  },

  openSchedule(e) {
    const rid = e.currentTarget.dataset.id
    const r = (this.detail && this.detail.rounds || []).find(x => x.id == rid)
    if (!r) return
    this.setData({
      showSchedule: true,
      scheduleRound: this.decorateRound(r, this.data.myTeamId, this.data.isCaptain),
      scheduleDate: r.scheduled_time ? this.fmtDate(r.scheduled_time) : '',
      scheduleTime: r.scheduled_time ? this.fmtTimeOnly(r.scheduled_time) : ''
    })
  },
  closeSchedule() { this.setData({ showSchedule: false, scheduleRound: null }) },
  onScheduleDateChange(e) { this.setData({ scheduleDate: e.detail.value }) },
  onScheduleTimeChange(e) { this.setData({ scheduleTime: e.detail.value }) },

  async submitSchedule() {
    const rid = this.data.scheduleRound ? this.data.scheduleRound.id : null
    const { scheduleDate, scheduleTime } = this.data
    if (!rid) return
    if (!scheduleDate || !scheduleTime) {
      wx.showToast({ title: '请选择日期和时间', icon: 'none' })
      return
    }
    const iso = scheduleDate + 'T' + scheduleTime + ':00'
    try {
      await api.scheduleRound(rid, iso)
      wx.showToast({ title: '已提交，等待对方确认', icon: 'success' })
      this.setData({ showSchedule: false, scheduleRound: null })
      this.refreshDetail()
    } catch (err) {
      wx.showToast({ title: err.detail || '提交失败', icon: 'none' })
    }
  },

  async confirmSchedule() {
    const r = this.data.scheduleRound
    if (!r) return
    try {
      await api.confirmRoundSchedule(r.id, r.scheduled_time)
      wx.showToast({ title: '已确认比赛时间', icon: 'success' })
      this.setData({ showSchedule: false, scheduleRound: null })
      this.refreshDetail()
    } catch (err) {
      wx.showToast({ title: err.detail || '确认失败', icon: 'none' })
    }
  },

  async rejectSchedule() {
    const r = this.data.scheduleRound
    if (!r) return
    // 二次确认，防止误触作废已确定的比赛时间
    const res = await new Promise(resolve => wx.showModal({
      title: '作废约定时间',
      content: r.schedule_status === 'confirmed'
        ? '确定要作废已确定的比赛时间吗？作废后需双方重新约定。'
        : '确定要作废当前提议吗？作废后需重新约定。',
      confirmText: '作废',
      confirmColor: '#e64340',
      success: resolve
    }))
    if (!res.confirm) return
    try {
      await api.rejectRoundSchedule(r.id, r.scheduled_time)
      wx.showToast({ title: '已作废，可重新约定', icon: 'none' })
      this.setData({ showSchedule: false, scheduleRound: null })
      this.refreshDetail()
    } catch (err) {
      wx.showToast({ title: err.detail || '操作失败', icon: 'none' })
    }
  },

  // 重新拉详情（保持我的队长权限标记）
  refreshDetail() {
    if (!this.data.match) return
    api.getMatchDetail(this.data.match.id).then(detail => {
      this.detail = detail
      this.setData({ match: Object.assign({}, detail.match, { statusText: this.statusText(detail.match.status) }) })
      this.buildData(detail, this.data.myTeamId, this.data.isCaptain)
    }).catch(() => {})
  }
})
