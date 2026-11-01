/**
 * 赛事详情页（用户端）
 * 功能：完整赛事资料展示（队伍状态/排名/小组赛/传奇组/淘汰赛BO3）+ 报名
 * 进入方式：首页点赛事卡片，带 ?id=xx 参数
 */
const api = require('../../utils/api.js')
const tournament = require('../../utils/tournament.js')

function wechatSubscriptions(scene) {
  try { const flow = require('../../utils/wechat-subscriptions.js'); return scene ? flow.prompt(scene) : flow.prefetch() }
  catch (_) { return Promise.resolve({ status: 'unavailable' }) }
}

// 只有后续赛段使用保留名称；挑战者小组采用赛事实际设置的名称与数量。
function isChallengerGroup(groupName) {
  return typeof groupName === 'string' && groupName.trim() !== '' &&
    ['附加赛', '上区', '下区', '淘汰赛'].indexOf(groupName) < 0
}

const sameTeam = (a, b) => a != null && b != null && String(a) === String(b)
const compareGroups = (a, b) => String(a).localeCompare(String(b), 'zh-CN', { numeric: true })
const compareRounds = (a, b) => (Number(a.round_number) || 0) - (Number(b.round_number) || 0) || (Number(a.id) || 0) - (Number(b.id) || 0)
// 对阵队徽暂用队名缩写，不把占位字母当作队伍上传的真实队标。
const teamDisplayName = (name, id) => id == null ? '轮空' : (typeof name === 'string' && name.trim()) || '队伍 ' + id
const teamInitial = (name, id) => {
  if (id == null) return '—'
  const label = teamDisplayName(name, id)
  const ascii = label.match(/^[a-zA-Z0-9]{1,2}/)
  return ascii ? ascii[0].toUpperCase() : Array.from(label)[0]
}
const groupLabel = name => {
  const label = typeof name === 'string' ? name.trim() : ''
  return label === '上区' || label === '下区' ? '传奇组 · ' + label
    : label === '附加赛' || label === '淘汰赛' ? label
      : label ? '挑战者组 · ' + label + (/组$/.test(label) ? '' : '组') : '待分组'
}

// 后端无时区的赛程采用北京时间；带时区的 ISO 时间先统一到北京时间。
// 只接受管理员已确认的真实日历时间，不用创建时间或旧的队长提议补日程。
function officialSchedule(round) {
  if (round.schedule_status !== 'confirmed' || typeof round.scheduled_time !== 'string') return null
  const m = round.scheduled_time.trim().match(/^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.\d{1,6})?)?(Z|[+-]\d{2}:\d{2})?$/i)
  if (!m) return null
  const parts = m.slice(1, 7).map(value => Number(value || 0))
  const [year, month, day, hour, minute, second] = parts
  const wall = Date.UTC(year, month - 1, day, hour, minute, second)
  const calendar = new Date(wall)
  if (calendar.getUTCFullYear() !== year || calendar.getUTCMonth() !== month - 1 || calendar.getUTCDate() !== day ||
      calendar.getUTCHours() !== hour || calendar.getUTCMinutes() !== minute || calendar.getUTCSeconds() !== second) return null
  let offset = 8 * 60
  if (m[7]) {
    if (m[7].toUpperCase() === 'Z') offset = 0
    else {
      const hours = Number(m[7].slice(1, 3)), minutes = Number(m[7].slice(4, 6))
      if (hours > 23 || minutes > 59) return null
      offset = (hours * 60 + minutes) * (m[7][0] === '-' ? -1 : 1)
    }
  }
  const timestamp = wall - offset * 60000
  const beijing = new Date(timestamp + 8 * 60 * 60000)
  const pad = value => String(value).padStart(2, '0')
  return { date: beijing.getUTCFullYear() + '-' + pad(beijing.getUTCMonth() + 1) + '-' + pad(beijing.getUTCDate()),
    clock: pad(beijing.getUTCHours()) + ':' + pad(beijing.getUTCMinutes()), timestamp }
}

Page({
  data: {
    returnSource: '',
    match: null,
    loadFailed: false,
    roundCount: 0,
    completedRoundCount: 0,
    registerEndText: '',
    tab: 'teams',            // 兼容旧链接与导航：teams / mine / challenger / legend / knockout
    mainTab: 'teams',        // teams / matches / stats / info
    matchupStage: 'all',     // all / challenger / playoff / legend / knockout
    matchupGroup: '',
    matchupDate: '',
    onlyMyTeam: false,
    matchupStages: [],
    matchupStageIndex: 0,
    matchupGroups: [],
    matchupGroupChoices: [{ value: '', label: '全部小组' }],
    matchupGroupIndex: 0,
    matchupDates: [{ value: '', label: '全部日期' }],
    matchupDateIndex: 0,
    matchupSections: [],
    matchupTimelineSections: [],
    matchupCount: 0,
    matchupEmptyText: '暂无对阵',
    showBracket: false,
    canShowBracket: false,
    bracketExpanded: false,
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
    groupOverview: [],
    challengerOverviewGroups: [],
    legendOverviewGroups: [],
    selectedChallengerGroup: '',
    selectedChallengerRoundCount: 0,
    myMatchSummary: null,
    myRounds: [],
    myRoundsPendingCount: 0,
    // 对阵（按阶段）
    challengerSwiper: [],    // 挑战者组：小组赛分组，三组赛制另有附加赛
    groupCount: 0,
    hasPlayoffStage: false,
    legendSwiper: [],        // 传奇组：上区/下区
    knockoutGroups: [],      // 淘汰赛 BO3：1/4决赛/半决赛/决赛
    knockoutBracketColumns: [], // 已确认的六强晋级路径；缺失后续场次仅显示占位
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
    myTeamId: null,
    focusedRoundId: null
  },

  onShow() {
    wechatSubscriptions().catch(() => {})
    this._chatVisible = true
    this._chatGeneration = (this._chatGeneration || 0) + 1
    this._pageHidden = false
    if (!wx.getStorageSync('token')) this.clearMyStatus()
    // onLoad 与首次 onShow 相邻，首屏只发起一次详情请求。
    if (!this._shownOnce) { this._shownOnce = true; return }
    if (this.matchId || this.data.match) return this.refreshDetail()
  },
  onHide() { this.invalidateRequests(); this.stopContact() },
  onUnload() { this._unloaded = true; this.invalidateRequests(); this.stopContact() },
  invalidateRequests() {
    this._pageHidden = true
    this._detailRequestId = (this._detailRequestId || 0) + 1
    this._statusRequestId = (this._statusRequestId || 0) + 1
  },
  stopContact() { this._chatVisible = false; this._chatGeneration = (this._chatGeneration || 0) + 1; this._contactAttempt = null },
  roundActions(round) {
    const actions = []
    if (round.can_chat) actions.push({ key: 'chat', label: '联系对方队长', handler: 'contactRound' })
    if (round.group_name === '淘汰赛') actions.push({ key: 'detail', label: '小局详情', handler: 'showBo3' })
    return actions
  },
  async openRoundActions(e) {
    if (this._roundActionAttempt || this._pageHidden || this._unloaded) return
    const id = Number(e.currentTarget.dataset.id)
    const token = wx.getStorageSync('token') || ''
    const teamId = this.data.myTeamId, captain = this.data.isCaptain, generation = this._chatGeneration
    const currentRound = () => {
      if (this._pageHidden || this._unloaded || generation !== this._chatGeneration ||
          token !== (wx.getStorageSync('token') || '') ||
          (this._identityToken != null && token !== this._identityToken) ||
          String(teamId) !== String(this.data.myTeamId) || captain !== this.data.isCaptain) return null
      const raw = (this.detail && this.detail.rounds || []).find(round => sameTeam(round.id, id))
      return raw && this.decorateRound(raw, token ? this.data.myTeamId : null, token && this.data.isCaptain)
    }
    const round = currentRound()
    if (!round) return
    const actions = this.roundActions(round)
    if (!actions.length) return
    const attempt = this._roundActionAttempt = {}
    try {
      const index = actions.length === 1 ? 0 : await new Promise(resolve => wx.showActionSheet({
        itemList: actions.map(action => action.label),
        success: res => resolve(res.tapIndex),
        fail: () => resolve(-1)
      }))
      const selected = actions[index], latest = currentRound()
      if (!selected || !latest) return
      // 原生菜单停留期间可能刷新赛程或切换身份，不能沿用打开时的权限。
      const action = this.roundActions(latest).find(item => item.key === selected.key)
      if (action) return await this[action.handler]({ currentTarget: { dataset: { id } } })
    } finally {
      if (this._roundActionAttempt === attempt) this._roundActionAttempt = null
    }
  },
  async contactRound(e) {
    const id = Number(e.currentTarget.dataset.id)
    const round = (this.detail && this.detail.rounds || []).find(item => item.id === id)
    if (!round || !this.decorateRound(round, this.data.myTeamId, this.data.isCaptain).can_chat || this._contactAttempt) return
    const generation = this._chatGeneration, token = wx.getStorageSync('token') || ''
    const attempt = this._contactAttempt = {}
    const isCurrent = () => this._chatVisible && generation === this._chatGeneration && token === (wx.getStorageSync('token') || '')
    try { await require('../../utils/chat-client.js').contact(id, 'round', true, isCurrent) }
    finally { if (this._contactAttempt === attempt) this._contactAttempt = null }
  },

  // 页面加载：拉完整赛事资料 + 报名状态
  async onLoad(options = {}) {
    this.setData({ returnSource: options.source === 'assistant' ? 'assistant' : '' })
    const id = options.id
    if (!id) return
    this.matchId = id
    this._initialTab = options.tab || ''
    this._notificationRoundId = Number(options.roundId) || null
    return this.requestDetail({ initial: true })
  },

  // 下拉刷新（重新拉详情，不重复弹规则）
  async onPullDownRefresh() {
    const matchId = (this.data.match && this.data.match.id) || this.matchId
    if (!matchId) {
      wx.stopPullDownRefresh()
      return
    }
    try {
      await this.requestDetail({ toast: true })
    } finally {
      wx.stopPullDownRefresh()
    }
  },

  clearMyStatus() {
    this._statusRequestId = (this._statusRequestId || 0) + 1
    this.setData({ myReg: null, registered: false, myTeam: null, isCaptain: false, myTeamId: null,
      canRegisterTeam: false, myMatchSummary: null, myRounds: [], myRoundsPendingCount: 0,
      onlyMyTeam: false, focusedRoundId: null,
      mainTab: this.data.tab === 'mine' ? 'teams' : this.data.mainTab,
      tab: this.data.tab === 'mine' ? 'teams' : this.data.tab })
    if (this.detail) this.buildData(this.detail, null, false)
  },

  async requestDetail(options = {}) {
    const matchId = this.matchId || (this.data.match && this.data.match.id)
    if (!matchId || this._unloaded || this._pageHidden) return
    const token = wx.getStorageSync('token') || ''
    if (!token || (this._identityToken != null && this._identityToken !== token)) this.clearMyStatus()
    this._identityToken = token
    const requestId = this._detailRequestId = (this._detailRequestId || 0) + 1
    const isCurrent = () => requestId === this._detailRequestId && !this._pageHidden && !this._unloaded && token === (wx.getStorageSync('token') || '')
    try {
      const detail = await api.getMatchDetail(matchId)
      if (!isCurrent()) { if (requestId === this._detailRequestId && token !== (wx.getStorageSync('token') || '')) this.clearMyStatus(); return }
      this.detail = detail
      this.setData({ match: Object.assign({}, detail.match, { statusText: this.statusText(detail.match.status) }) })
      this.buildData(detail, this.data.myTeamId, this.data.isCaptain)
      if (token) await this.loadMyStatus()
      if (!isCurrent()) return
      if (this._notificationRoundId) {
        // 对阵通知优先；不能在下一次刷新时再被尚未消费的 tab 覆盖。
        this._initialTab = ''
        this.openNotificationRound()
      } else {
        // 首次请求可能失败或被后台切换中断，初始目的地在首次成功读取时兑现。
        const initialTab = this._initialTab
        this._initialTab = ''
        if (initialTab && !this._tabSelectedManually) this.switchTab({ currentTarget: { dataset: { tab: initialTab } } })
        if (options.initial) this.checkRulePrompt()
      }
    } catch (err) {
      if (!isCurrent()) return
      if (options.initial) this.setData({ loadFailed: true })
      if (options.initial || options.toast) wx.showToast({ title: options.initial ? '加载失败' : '刷新失败', icon: 'error' })
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
    const rawKnockout = (detail.rounds || []).filter(r => r.group_name === '淘汰赛').slice().sort(compareRounds)
    const challengerRoundMeta = this.buildChallengerRoundMeta(detail.rounds || [])
    const rounds = (detail.rounds || []).map(r => this.decorateRound(Object.assign({}, r, {
      stage_label: r.group_name === '淘汰赛' ? this.knockoutStageLabel(rawKnockout.findIndex(k => k.id === r.id)) : groupLabel(r.group_name)
    }, challengerRoundMeta.get(r)), myTeamId, isCaptain))
    const rosterLocked = tournament.isRosterLocked(detail.match || this.data.match, teams, rounds)
    const knockoutRounds = rounds.filter(r => r.group_name === '淘汰赛')
    const koSorted = knockoutRounds.slice().sort(compareRounds)
    const fmtBo3 = r => (r.bo3_scores ? r.bo3_scores.map(g => g.t1 + '-' + g.t2).join(' ') : '')

    // 淘汰赛对阵按轮次分组
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

    // 小组赛对阵按组分区
    const grouped = Object.create(null)
    rounds.filter(r => isChallengerGroup(r.group_name)).forEach(r => {
      ;(grouped[r.group_name] = grouped[r.group_name] || []).push(r)
    })
    // 晋级后队伍的 group_name 会变更，历史对阵仍保留最初的小组名称。
    const groupNames = Object.create(null)
    teams.concat(rounds).forEach(item => {
      if (isChallengerGroup(item.group_name)) groupNames[item.group_name] = true
    })
    const names = Object.keys(groupNames).sort(compareGroups)
    const groupCount = names.length
    const groupRounds = Object.create(null)
    names.forEach(name => { groupRounds[name] = (grouped[name] || []).slice().sort(compareRounds) })
    const selectedChallengerGroup = names.includes(this.data.selectedChallengerGroup) ? this.data.selectedChallengerGroup : ''
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
    const groupOverview = this.buildGroupOverview(teams, rounds, myTeamId).map(section =>
      section.group_name === '附加赛' && !hasPlayoffStage
        ? Object.assign({}, section, { can_view: false, hint: '历史附加赛记录，本赛事当前不设附加赛' }) : section)
    const challengerOverviewGroups = groupOverview.filter(s => s.tab === 'challenger' && isChallengerGroup(s.group_name))
    const legendOverviewGroups = groupOverview.filter(s => s.tab === 'legend')
    const ownTeam = myTeamId != null && teams.find(t => sameTeam(t.team_id, myTeamId))
    const myRounds = myTeamId == null ? [] : rounds.filter(r => r.is_mine).sort((a, b) => {
      const finishedOrder = Number(a.status === 'finished') - Number(b.status === 'finished')
      if (finishedOrder) return finishedOrder
      if (a.status !== 'finished') {
        const aTime = a.schedule_status === 'confirmed' && a.scheduled_time ? Date.parse(a.scheduled_time) : NaN
        const bTime = b.schedule_status === 'confirmed' && b.scheduled_time ? Date.parse(b.scheduled_time) : NaN
        const aHasTime = Number.isFinite(aTime), bHasTime = Number.isFinite(bTime)
        if (aHasTime !== bHasTime) return aHasTime ? -1 : 1
        if (aHasTime && aTime !== bTime) return aTime - bTime
      }
      return compareRounds(a, b)
    })
    const myRoundsPendingCount = myRounds.filter(r => r.status !== 'finished').length
    const mineSections = groupOverview.filter(s => s.is_mine)
    const currentSection = mineSections.find(s => ownTeam && ownTeam.group_name && s.group_name === ownTeam.group_name) ||
      mineSections.find(s => ownTeam && ownTeam.stage === 'legend' && s.key === 'legend-waiting') || mineSections[mineSections.length - 1]
    const pendingOpponents = Array.from(new Set(myRounds.filter(r => r.status !== 'finished' && r.opponent_id != null).map(r => r.opponent_name)))
    const nextRound = this.nextTeamRound(myRounds, ownTeam)
    const myMatchHint = ownTeam && ownTeam.stage === 'legend' && !['上区', '下区'].includes(ownTeam.group_name)
      ? (myRounds.some(r => isChallengerGroup(r.group_name) || r.group_name === '附加赛') ? '已晋级传奇组' : '已直升传奇组') + '，等待传奇组分区及对阵生成'
      : !myRounds.length ? '队伍已在赛事名单中，等待生成对阵'
        : myRoundsPendingCount ? '比赛时间由管理员统一安排；队长可联系对手' : '本队已生成的对阵均已结束'
    const myMatchSummary = ownTeam || myRounds.length ? {
      team_id: myTeamId,
      team_name: (ownTeam ? ownTeam.team_name : (myRounds[0].team1_is_mine ? myRounds[0].team1_name : myRounds[0].team2_name)) || '我的队伍',
      group_text: ownTeam && ownTeam.stage === 'playoff'
        ? '淘汰赛' + (['上区', '下区'].includes(ownTeam.group_name) ? ' · ' + ownTeam.group_name + '晋级' : '')
        : ownTeam && ownTeam.stage === 'legend'
          ? (['上区', '下区'].includes(ownTeam.group_name) ? groupLabel(ownTeam.group_name) : '传奇组 · 待分区')
          : (currentSection ? currentSection.label : '待分组') + (ownTeam && ownTeam.stage === 'eliminated' ? ' · 已出局' : ''),
      total_count: myRounds.length,
      pending_count: myRoundsPendingCount,
      notice_title: ownTeam && ownTeam.stage === 'eliminated' ? '本队已出局，查看战绩'
        : nextRound ? (nextRound.status === 'in_progress' ? '本队有比赛正在进行'
          : nextRound.schedule_status === 'confirmed' && nextRound.scheduled_time ? '比赛时间已安排，查看对阵' : '本队有待赛对阵，查看赛程')
          : !myRounds.length || (ownTeam && ownTeam.stage === 'legend' && !['上区', '下区'].includes(ownTeam.group_name))
            ? '本队等待后续赛程安排' : '本队当前阶段对阵已结束，查看战绩',
      next_round: nextRound,
      next_label: nextRound ? (nextRound.status === 'in_progress' ? '正在比赛' : '下一场对阵') : '',
      next_empty_text: ownTeam && ownTeam.stage === 'eliminated' ? '本队已出局，可查看各阶段比赛记录'
        : !myRounds.length || (ownTeam && ownTeam.stage === 'legend' && !['上区', '下区'].includes(ownTeam.group_name))
          ? myMatchHint : '本队当前阶段暂无待赛对阵',
      opponent_text: pendingOpponents.length ? pendingOpponents.join('、') : myRounds.length ? '暂无待比赛对手' : '对手待编排',
      hint: myMatchHint
    } : null
    // 默认展示赛事概览；个人对阵由提醒进入，手动页签与赛程通知定位保持优先。
    const navigationToken = wx.getStorageSync('token') || ''
    if (navigationToken !== this._navigationToken) {
      this._navigationToken = navigationToken
      this._tabSelectedManually = false
    }
    const tab = !myMatchSummary && this.data.tab === 'mine' ? 'teams' : this.data.tab

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
      groupOverview,
      challengerOverviewGroups,
      legendOverviewGroups,
      selectedChallengerGroup,
      selectedChallengerRoundCount: selectedChallengerGroup ? groupRounds[selectedChallengerGroup].length : challengerSwiper[0].count,
      myMatchSummary,
      tab,
      onlyMyTeam: !!myMatchSummary && this.data.onlyMyTeam,
      mainTab: tab === 'teams' && this.data.tab === 'mine' ? 'teams' : this.data.mainTab,
      myRounds,
      myRoundsPendingCount,
      allRounds: rounds,
      challengerSwiper,
      challengerStage,
      groupCount,
      hasPlayoffStage,
      legendSwiper,
      knockoutGroups: koGroups,
      knockoutBracketColumns: this.buildKnockoutBracket(koSorted)
    })
    this.refreshMatchupFilters()
  },

  buildKnockoutBracket(rounds) {
    // 服务端以完整赛段推进：2 场首轮、4 场含半决赛、5 场含决赛。
    // 当前 API 没有独立的淘汰赛 slot，残缺/异常记录不能用连线推断晋级位置。
    if (![2, 4, 5].includes(rounds.length)) return []
    const sorted = rounds.slice().sort(compareRounds)
    const ids = new Set(), numbers = new Set()
    for (const r of sorted) {
      const number = Number(r.round_number)
      if (r.id == null || !String(r.id).trim() || ids.has(String(r.id)) || !Number.isInteger(number) || number <= 0 ||
        numbers.has(number) || r.team1_id == null || r.team2_id == null || sameTeam(r.team1_id, r.team2_id)) return []
      ids.add(String(r.id))
      numbers.add(number)
    }
    const quarterIds = new Set(sorted.slice(0, 2).reduce((all, r) => all.concat(String(r.team1_id), String(r.team2_id)), []))
    if (quarterIds.size !== 4) return []
    const hasWinner = r => r.status === 'finished' &&
      (sameTeam(r.winner_id, r.team1_id) || sameTeam(r.winner_id, r.team2_id))
    if (sorted.length >= 4) {
      // 上区第 1 对首轮第二场胜者；下区第 1 对首轮第一场胜者。
      if (!hasWinner(sorted[0]) || !hasWinner(sorted[1]) ||
        !sameTeam(sorted[2].team2_id, sorted[1].winner_id) || !sameTeam(sorted[3].team2_id, sorted[0].winner_id) ||
        sameTeam(sorted[2].team1_id, sorted[3].team1_id) ||
        quarterIds.has(String(sorted[2].team1_id)) || quarterIds.has(String(sorted[3].team1_id))) return []
    }
    if (sorted.length === 5 && (!hasWinner(sorted[2]) || !hasWinner(sorted[3]) ||
      !sameTeam(sorted[4].team1_id, sorted[2].winner_id) || !sameTeam(sorted[4].team2_id, sorted[3].winner_id))) return []

    const node = (key, label, r, firstDependency, secondDependency) => {
      if (!r) return { key, label, is_placeholder: true, round: null,
        team1_name: firstDependency, team2_name: secondDependency, team1_initial: '待', team2_initial: '待', status_text: '尚未生成' }
      // 树按服务端 team1/team2 显示，每局比分也保持该顺序。
      const current = Object.assign({}, r, { ko_label: label,
        bo3_text: (Array.isArray(r.bo3_scores) ? r.bo3_scores : []).map(g => g.t1 + '-' + g.t2).join(' / ') })
      return { key, label, is_placeholder: false, round: current,
        team1_name: r.team1_display_name, team2_name: r.team2_display_name,
        team1_initial: r.team1_initial, team2_initial: r.team2_initial,
        status_text: r.status === 'finished' ? '已结束' : r.status === 'in_progress' ? '进行中' : '待比赛' }
    }
    return [
      { key: 'quarter', title: '1/4决赛', format: 'BO3', nodes: [
        node('quarter-lower', '第二场', sorted[1]),
        node('quarter-upper', '第一场', sorted[0])
      ] },
      { key: 'semi', title: '半决赛', format: 'BO3', nodes: [
        node('semi-upper', '上区第1 vs 第二场胜者', sorted[2], '上区第1', '第二场胜者'),
        node('semi-lower', '下区第1 vs 第一场胜者', sorted[3], '下区第1', '第一场胜者')
      ] },
      { key: 'final', title: '决赛', format: 'BO3', nodes: [
        node('final', '冠军战', sorted[4], '上方半决赛胜者', '下方半决赛胜者')
      ] }
    ]
  },

  buildChallengerRoundMeta(rounds) {
    const groups = new Map()
    rounds.filter(r => isChallengerGroup(r.group_name)).forEach(r => {
      if (!groups.has(r.group_name)) groups.set(r.group_name, [])
      groups.get(r.group_name).push(r)
    })
    const meta = new Map()
    groups.forEach(groupRounds => {
      const sorted = groupRounds.slice().sort(compareRounds)
      const pairs = new Map()
      sorted.forEach((r, index) => {
        meta.set(r, { group_match_index: index + 1, group_match_text: '本组第 ' + (index + 1) + ' 场', group_round_label: '' })
        if (r.team1_id == null || r.team2_id == null || sameTeam(r.team1_id, r.team2_id)) return
        const pairKey = JSON.stringify([String(r.team1_id), String(r.team2_id)].sort())
        if (!pairs.has(pairKey)) pairs.set(pairKey, [])
        pairs.get(pairKey).push(r)
      })
      // round_number 是全赛事场序号；仅真实的两次交手可标注双循环。
      pairs.forEach(pairRounds => {
        if (pairRounds.length !== 2) return
        pairRounds.forEach((r, index) => { meta.get(r).group_round_label = '第' + (index + 1) + '次交手' })
      })
    })
    return meta
  },

  nextTeamRound(myRounds, ownTeam) {
    if (ownTeam && ownTeam.stage === 'eliminated') return null
    const isCurrentStage = r => {
      if (!ownTeam) return true
      if (ownTeam.stage === 'playoff') return r.group_name === '淘汰赛'
      if (ownTeam.stage === 'legend') return ['上区', '下区'].includes(ownTeam.group_name) && r.group_name === ownTeam.group_name
      if (ownTeam.stage === 'challenger') return (isChallengerGroup(ownTeam.group_name) || ownTeam.group_name === '附加赛') && r.group_name === ownTeam.group_name
      return ownTeam.group_name === '附加赛' && r.group_name === '附加赛'
    }
    const pending = myRounds.filter(r => r.opponent_id != null && r.opponent_id !== '' && !sameTeam(r.team1_id, r.team2_id) &&
      (r.status === 'pending' || r.status === 'in_progress') && isCurrentStage(r))
    return pending.find(r => r.status === 'in_progress') || pending[0] || null
  },

  knockoutStageLabel(index) { return index < 2 ? '淘汰赛 · 1/4决赛' : index < 4 ? '淘汰赛 · 半决赛' : '淘汰赛 · 决赛' },

  buildGroupOverview(teams, rounds, myTeamId) {
    const sections = new Map()
    const teamNames = new Map(teams.map(t => [String(t.team_id), t.team_name]))
    const descriptor = (name, team) => {
      if (team && team.stage === 'playoff') return { key: 'knockout', label: '淘汰赛', group_name: '淘汰赛', tab: 'knockout', order: 5 }
      if (team && team.stage === 'legend' && !['上区', '下区'].includes(name)) return { key: 'legend-waiting', label: '传奇组 · 待分区', group_name: '', tab: 'legend', order: 4 }
      if (isChallengerGroup(name)) return { key: 'challenger:' + name, label: groupLabel(name), group_name: name, tab: 'challenger', order: 0 }
      if (name === '附加赛') return { key: 'playoff', label: '附加赛', group_name: name, tab: 'challenger', order: 1 }
      if (name === '上区' || name === '下区') return { key: 'legend:' + name, label: groupLabel(name), group_name: name, tab: 'legend', order: name === '上区' ? 2 : 3 }
      if (name === '淘汰赛') return { key: 'knockout', label: '淘汰赛', group_name: '淘汰赛', tab: 'knockout', order: 5 }
      if (team && team.stage === 'legend') return { key: 'legend-waiting', label: '传奇组 · 待分区', group_name: '', tab: 'legend', order: 4 }
      return { key: 'waiting', label: '待分组', group_name: '', tab: 'teams', order: 6 }
    }
    const getSection = (name, team) => {
      const spec = descriptor(name, team)
      if (!sections.has(spec.key)) sections.set(spec.key, Object.assign({}, spec, { teamMap: new Map(), rounds: [] }))
      return sections.get(spec.key)
    }
    const addTeam = (section, id, name) => {
      if (id == null) return
      const key = String(id)
      if (!section.teamMap.has(key)) section.teamMap.set(key, { team_id: id, team_name: teamNames.get(key) || name || '队伍 ' + id, is_mine: sameTeam(id, myTeamId) })
    }
    teams.forEach(team => addTeam(getSection(team.group_name, team), team.team_id, team.team_name))
    rounds.forEach(round => {
      const section = getSection(round.group_name)
      section.rounds.push(round)
      addTeam(section, round.team1_id, round.team1_name)
      addTeam(section, round.team2_id, round.team2_name)
    })
    return Array.from(sections.values()).sort((a, b) => a.order - b.order || compareGroups(a.group_name, b.group_name)).map(section => {
      const sectionTeams = Array.from(section.teamMap.values())
      const noRounds = section.rounds.length === 0
      return {
        key: section.key, label: section.label,
        short_label: section.tab === 'challenger' && section.group_name !== '附加赛' ? section.group_name + (/组$/.test(section.group_name) ? '' : '组')
          : section.key === 'legend-waiting' ? '待分区' : section.group_name || '待分组',
        group_name: section.group_name, tab: section.tab,
        teams: sectionTeams, team_count: sectionTeams.length, round_count: section.rounds.length,
        finished_count: section.rounds.filter(r => r.status === 'finished').length,
        is_mine: sectionTeams.some(t => t.is_mine),
        can_view: section.tab !== 'teams' && section.key !== 'legend-waiting',
        hint: section.key === 'legend-waiting' ? '等待传奇组分区，对阵尚未生成'
          : section.key === 'waiting' ? '尚未确定分组，不计入挑战者小组'
            : noRounds ? '队伍已分组，等待生成对阵' : '已结束 ' + section.rounds.filter(r => r.status === 'finished').length + ' / ' + section.rounds.length + ' 场'
      }
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

  roundStage(round) {
    return round.group_name === '淘汰赛' ? 'knockout' : round.group_name === '附加赛' ? 'playoff'
      : ['上区', '下区'].includes(round.group_name) ? 'legend' : 'challenger'
  },
  officialRoundDate(round) {
    const time = officialSchedule(round)
    return time ? time.date : ''
  },
  buildMatchupTimelineSections(rounds) {
    const sections = new Map()
    rounds.forEach(round => {
      const time = officialSchedule(round)
      const finished = round.status === 'finished'
      const key = time ? 'day-' + time.date : finished ? 'undated-finished' : 'undated-upcoming'
      if (!sections.has(key)) {
        const date = time && time.date
        const weekday = date ? '周' + '日一二三四五六'[new Date(date + 'T00:00:00Z').getUTCDay()] : ''
        sections.set(key, { key, date: date || '',
          title: date ? date.slice(5, 7) + '月' + date.slice(8) + '日' : finished ? '已结束 · 时间未记录' : '时间待定',
          caption: date ? date.slice(0, 4) + ' · ' + weekday : finished ? '未记录正式开赛时间' : '由管理员统一安排',
          order: date ? 0 : finished ? 2 : 1, rounds: [] })
      }
      sections.get(key).rounds.push(Object.assign({}, round, {
        timeline_clock: time ? time.clock : '—',
        timeline_clock_caption: time ? '北京时间' : finished ? '时间未录' : '待排期',
        timeline_timestamp: time ? time.timestamp : null
      }))
    })
    return Array.from(sections.values()).sort((a, b) => a.order - b.order || a.date.localeCompare(b.date)).map(section => {
      section.rounds.sort((a, b) => {
        if (section.date) return a.timeline_timestamp - b.timeline_timestamp || compareRounds(a, b)
        return (a.status === 'in_progress' ? 0 : 1) - (b.status === 'in_progress' ? 0 : 1) || compareRounds(a, b)
      })
      return section
    })
  },
  toggleMatchupBracket() {
    if (!this.data.canShowBracket) return
    this.setData({ bracketExpanded: !this.data.showBracket, focusedRoundId: null })
    this.refreshMatchupFilters()
  },
  refreshMatchupFilters(callback) {
    const stages = [{ value: 'all', label: '全部阶段' }, { value: 'challenger', label: '挑战者小组赛' }]
    if (this.data.hasPlayoffStage) stages.push({ value: 'playoff', label: '附加赛' })
    stages.push({ value: 'legend', label: '传奇组' }, { value: 'knockout', label: '淘汰赛' })
    const matchupStage = stages.some(s => s.value === this.data.matchupStage) ? this.data.matchupStage : 'all'
    const source = this.data.allRounds.filter(r => this.roundStage(r) !== 'playoff' || this.data.hasPlayoffStage)
    const inStage = source.filter(r => matchupStage === 'all' || this.roundStage(r) === matchupStage)
    const groups = matchupStage === 'challenger' ? this.data.challengerOverviewGroups
      : matchupStage === 'legend' ? this.data.legendOverviewGroups.filter(g => g.group_name) : []
    const matchupGroups = groups.map(g => ({ value: g.group_name, label: g.short_label }))
    const matchupGroup = matchupGroups.some(g => g.value === this.data.matchupGroup) ? this.data.matchupGroup : ''
    const matchupGroupChoices = [{ value: '', label: matchupStage === 'legend' ? '全部分区' : '全部小组' }].concat(matchupGroups)
    const inGroup = inStage.filter(r => !matchupGroup || r.group_name === matchupGroup)
    const onlyMyTeam = !!this.data.myMatchSummary && this.data.onlyMyTeam
    const candidates = inGroup.filter(r => !onlyMyTeam || r.is_mine)
    const dates = Array.from(new Set(candidates.map(r => this.officialRoundDate(r)).filter(Boolean))).sort()
    const matchupDates = [{ value: '', label: '全部日期' }].concat(dates.map(value => ({ value, label: value.slice(5).replace('-', '月') + '日' })))
    if (candidates.some(r => !this.officialRoundDate(r))) matchupDates.push({ value: 'unscheduled', label: '未记录正式时间' })
    const matchupDate = matchupDates.some(d => d.value === this.data.matchupDate) ? this.data.matchupDate : ''
    const filtered = candidates.filter(r => !matchupDate || (matchupDate === 'unscheduled' ? !this.officialRoundDate(r) : this.officialRoundDate(r) === matchupDate))
    // 本队筛选沿用本队视角；公共列表保持服务端双方顺序。
    const display = filtered.map(r => Object.assign({}, r, {
      display_team1_name: onlyMyTeam ? r.my_team_name : r.team1_display_name,
      display_team2_name: onlyMyTeam ? r.opponent_name : r.team2_display_name,
      display_team1_initial: onlyMyTeam ? r.my_team_initial : r.team1_initial,
      display_team2_initial: onlyMyTeam ? r.opponent_initial : r.team2_initial,
      display_score1: onlyMyTeam ? r.my_score : r.team1_score,
      display_score2: onlyMyTeam ? r.opponent_score : r.team2_score,
      display_winner1: r.status === 'finished' && sameTeam(r.winner_id, onlyMyTeam ? this.data.myTeamId : r.team1_id),
      display_winner2: r.status === 'finished' && sameTeam(r.winner_id, onlyMyTeam ? r.opponent_id : r.team2_id)
    }))
    const sections = new Map()
    display.forEach(r => {
      const key = r.group_name === '淘汰赛' ? r.stage_label : r.group_name
      if (!sections.has(key)) sections.set(key, { key, title: r.stage_label, stage: this.roundStage(r), rounds: [] })
      sections.get(key).rounds.push(r)
    })
    const order = { challenger: 0, playoff: 1, legend: 2, knockout: 3 }
    const matchupSections = Array.from(sections.values()).sort((a, b) => order[a.stage] - order[b.stage] ||
      (a.stage === 'knockout' ? compareRounds(a.rounds[0], b.rounds[0]) : compareGroups(a.key, b.key)))
    matchupSections.forEach(s => s.rounds.sort((a, b) => {
      if (onlyMyTeam) return this.data.myRounds.findIndex(r => r.id === a.id) - this.data.myRounds.findIndex(r => r.id === b.id)
      const aDate = this.officialRoundDate(a), bDate = this.officialRoundDate(b)
      return (aDate && bDate ? String(a.scheduled_time).localeCompare(String(b.scheduled_time)) : aDate ? -1 : bDate ? 1 : 0) || compareRounds(a, b)
    }))
    const matchupTimelineSections = this.buildMatchupTimelineSections(display)
    const canShowBracket = matchupStage === 'knockout' && !onlyMyTeam && !matchupDate && this.data.knockoutBracketColumns.length > 0
    const bracketExpanded = canShowBracket && this.data.bracketExpanded
    const matchupEmptyText = onlyMyTeam && !this.data.myRounds.length ? this.data.myMatchSummary.hint
      : matchupStage === 'legend' && !inStage.length ? '传奇组对阵尚未生成，等待分区及赛程编排'
        : matchupGroup && !inGroup.length ? '所选小组对阵尚未生成'
          : candidates.length && !filtered.length ? '所选日期没有对阵' : onlyMyTeam ? '本队在所选阶段暂无对阵' : '暂无对阵，等待管理员编排'
    this.setData({ matchupStages: stages, matchupStage, matchupStageIndex: stages.findIndex(s => s.value === matchupStage),
      matchupGroups, matchupGroup, matchupGroupChoices, matchupGroupIndex: matchupGroupChoices.findIndex(g => g.value === matchupGroup), onlyMyTeam,
      matchupDates, matchupDate, matchupDateIndex: matchupDates.findIndex(d => d.value === matchupDate),
      matchupSections, matchupTimelineSections, matchupCount: filtered.length, matchupEmptyText,
      canShowBracket, bracketExpanded, showBracket: bracketExpanded }, callback)
  },
  changeMatchupStagePicker(e) {
    const option = this.data.matchupStages[Number(e.detail.value)]
    if (option) this.changeMatchupStage({ currentTarget: { dataset: { stage: option.value } } })
  },
  changeMatchupStage(e) {
    const stage = e.currentTarget.dataset.stage
    if (!this.data.matchupStages.some(s => s.value === stage)) return
    this.rememberTabSelection()
    this.setData({ mainTab: 'matches', matchupStage: stage, matchupGroup: '', matchupDate: '', focusedRoundId: null, bracketExpanded: false,
      tab: stage === 'legend' ? 'legend' : stage === 'knockout' ? 'knockout' : stage === 'all' ? 'matches' : 'challenger',
      challengerStage: stage === 'playoff' ? Math.max(0, this.data.challengerSwiper.findIndex(s => s.type === 'playoff')) : 0,
      selectedChallengerGroup: '' })
    this.refreshMatchupFilters()
  },
  changeMatchupGroupPicker(e) {
    const option = this.data.matchupGroupChoices[Number(e.detail.value)]
    if (option) this.changeMatchupGroup({ currentTarget: { dataset: { group: option.value } } })
  },
  changeMatchupGroup(e) {
    const group = e.currentTarget.dataset.group || ''
    this.setData({ matchupGroup: group, selectedChallengerGroup: this.data.matchupStage === 'challenger' ? group : '',
      focusedRoundId: null, matchupDate: '', bracketExpanded: false })
    this.refreshMatchupFilters()
  },
  changeMatchupDate(e) {
    const option = this.data.matchupDates[Number(e.detail.value)]
    if (!option) return
    this.setData({ matchupDate: option.value, focusedRoundId: null, bracketExpanded: false })
    this.refreshMatchupFilters()
  },
  toggleMyTeam() {
    if (!this.data.myMatchSummary) return
    this.setData({ onlyMyTeam: !this.data.onlyMyTeam, matchupDate: '', focusedRoundId: null, bracketExpanded: false })
    this.refreshMatchupFilters()
  },
  openMatchupDetail(e) {
    const round = this.data.allRounds.find(r => sameTeam(r.id, e.currentTarget.dataset.id))
    if (!round) return
    if (round.group_name === '淘汰赛') return this.showBo3(e)
    this.setData({ showBo3: true, bo3Detail: {
      team1_name: round.team1_display_name, team2_name: round.team2_display_name,
      status: round.status, score1: round.team1_score, score2: round.team2_score,
      ko_label: round.stage_label + ' · BO1', format_text: 'BO1', games: []
    } })
  },
  goRules() { wx.navigateTo({ url: '/pages/rules/rules' }) },

  rememberTabSelection() {
    this._navigationToken = wx.getStorageSync('token') || ''
    this._tabSelectedManually = true
  },
  scrollMatchContent() {
    if (typeof wx.pageScrollTo === 'function') wx.pageScrollTo({ selector: '#match-content', duration: 180 })
  },
  switchTab(e) {
    this.rememberTabSelection()
    const { tab, jump } = e.currentTarget.dataset
    const updates = { tab, bracketExpanded: false }
    let focus = null
    if (tab === 'teams' || tab === 'info' || tab === 'stats') updates.mainTab = tab
    else {
      updates.mainTab = 'matches'
      if (tab === 'mine') {
        updates.onlyMyTeam = !!this.data.myMatchSummary
        updates.matchupStage = 'all'
        updates.matchupGroup = ''
        updates.matchupDate = ''
        focus = this.data.myMatchSummary && this.data.myMatchSummary.next_round || this.data.myRounds[0]
        updates.focusedRoundId = focus ? focus.id : null
      } else if (['challenger', 'legend', 'knockout'].includes(tab)) {
        updates.matchupStage = tab === 'challenger' && this.data.challengerStage ? 'playoff' : tab
        updates.matchupGroup = tab === 'challenger' ? this.data.selectedChallengerGroup : tab === 'legend' ? (this.data.legendSwiper[this.data.legendStage] || {}).title || '' : ''
        updates.onlyMyTeam = false
        updates.matchupDate = ''
      }
    }
    this.setData(updates)
    this.refreshMatchupFilters(jump === true || jump === 'true' ? () => {
      if (focus && typeof wx.pageScrollTo === 'function') wx.pageScrollTo({ selector: '#round-' + focus.id, duration: 180 })
      else this.scrollMatchContent()
    } : undefined)
  },
  selectChallengerGroup(e) {
    this.rememberTabSelection()
    const name = e.currentTarget.dataset.group
    const valid = this.data.groupOverview.some(s => s.tab === 'challenger' && s.group_name === name && isChallengerGroup(name))
    const groups = (this.data.challengerSwiper[0] || {}).groups || {}
    this.setData({ selectedChallengerGroup: valid ? name : '', challengerStage: 0,
      selectedChallengerRoundCount: valid ? (groups[name] || []).length : (this.data.challengerSwiper[0] || {}).count || 0 })
    this.setData({ mainTab: 'matches', tab: 'challenger', matchupStage: 'challenger', matchupGroup: valid ? name : '', matchupDate: '', focusedRoundId: null })
    this.refreshMatchupFilters()
  },
  openGroupRounds(e) {
    const { tab, group } = e.currentTarget.dataset
    const section = this.data.groupOverview.find(s => s.tab === tab && s.group_name === group && s.can_view)
    if (!section) return
    this.rememberTabSelection()
    const updates = { tab, mainTab: 'matches', matchupStage: tab === 'challenger' && group === '附加赛' ? 'playoff' : tab,
      matchupGroup: isChallengerGroup(group) || tab === 'legend' ? group : '', onlyMyTeam: false, matchupDate: '', focusedRoundId: null }
    if (tab === 'challenger') {
      updates.challengerStage = section.group_name === '附加赛' ? Math.max(0, this.data.challengerSwiper.findIndex(s => s.type === 'playoff')) : 0
      updates.selectedChallengerGroup = isChallengerGroup(group) ? group : ''
      updates.selectedChallengerRoundCount = isChallengerGroup(group) ? section.round_count : (this.data.challengerSwiper[0] || {}).count || 0
    }
    if (tab === 'legend') updates.legendStage = Math.max(0, this.data.legendSwiper.findIndex(s => s.title === group))
    this.setData(updates)
    this.refreshMatchupFilters(() => this.scrollMatchContent())
  },
  selectChallengerStage(value) {
    const index = Number(value)
    this.setData({ challengerStage: Number.isInteger(index) && index >= 0 && index < this.data.challengerSwiper.length ? index : 0 })
    this.setData({ matchupStage: this.data.challengerStage ? 'playoff' : 'challenger',
      matchupGroup: this.data.challengerStage ? '' : this.data.selectedChallengerGroup, matchupDate: '' })
    this.refreshMatchupFilters()
  },
  switchChallengerStage(e) { this.selectChallengerStage(e.currentTarget.dataset.idx) },
  onChallengerSwiper(e) { this.selectChallengerStage(e.detail.current) },
  switchLegendStage(e) {
    this.setData({ legendStage: Number(e.currentTarget.dataset.idx), matchupGroup: (this.data.legendSwiper[Number(e.currentTarget.dataset.idx)] || {}).title || '' })
    this.refreshMatchupFilters()
  },
  onLegendSwiper(e) { this.switchLegendStage({ currentTarget: { dataset: { idx: e.detail.current } } }) },

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
        status: round.status,
        score1: round.team1_score,
        score2: round.team2_score,
        ko_label: round.ko_label,
        format_text: 'BO3',
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
    const token = wx.getStorageSync('token') || ''
    if (!token) { this.clearMyStatus(); return }
    if (this._identityToken != null && this._identityToken !== token) this.clearMyStatus()
    const matchId = this.data.match.id
    const requestId = this._statusRequestId = (this._statusRequestId || 0) + 1
    const detailRequestId = this._detailRequestId
    const isCurrent = () => requestId === this._statusRequestId && detailRequestId === this._detailRequestId &&
      this.data.match && this.data.match.id === matchId && !this._pageHidden && !this._unloaded && token === (wx.getStorageSync('token') || '')
    const stale = () => {
      if (isCurrent()) return false
      if (requestId === this._statusRequestId && token !== (wx.getStorageSync('token') || '')) this.clearMyStatus()
      return true
    }
    try {
      const regData = await api.getMyRegistration(matchId)
      if (stale()) return
      // 查我的队伍（判断报名类型用）
      const team = await api.getMyTeam()
      if (stale()) return
      let isCaptain = false
      let myTeamId = null
      if (team) {
        // 用 /me 接口拿当前用户 id（比解析 JWT 更可靠）
        const me = await api.getMe()
        if (stale()) return
        const userId = me ? me.id : null
        isCaptain = sameTeam(team.captain_id, userId)
        myTeamId = team.id
      }
      // 个人报名不代表新创建的队伍已经报名，队长仍需提交整队审核。
      const registration = regData.registration
      const rosterLocked = tournament.isRosterLocked(this.data.match, this.data.teams, this.detail && this.detail.rounds)
      const canRegisterTeam = isCaptain && !rosterLocked && (!regData.registered || !registration || registration.team_id !== myTeamId)
      this._identityToken = token
      this.setData({ registered: regData.registered, myReg: registration, myTeam: team || null, isCaptain, myTeamId, canRegisterTeam, rosterLocked })
      // 拿到队长身份后重新装饰对阵（标记可操作的对阵）
      if (this.detail) {
        this.buildData(this.detail, myTeamId, isCaptain)
      }
    } catch (err) {
      if (isCurrent()) this.clearMyStatus()
      else stale()
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
    const match = this.data.match, registrationToken = wx.getStorageSync('token') || ''

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

    if (registrationToken !== (wx.getStorageSync('token') || '') || this._pageHidden || this._unloaded) return
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
        if (!res.confirm || registrationToken !== (wx.getStorageSync('token') || '')) return
        // 确认弹窗打开后也可能刷新到已锁定名单，提交前再检查一次。
        if (this.isTeamRegistrationLocked()) {
          this.showRosterLocked()
          return
        }
        try {
          if (this.data.myTeam) {
            await api.registerTeam(match.id, this.data.myTeam.id)   // 队伍报名
          } else {
            await wechatSubscriptions('personal_registration').catch(() => {})
            if (registrationToken !== (wx.getStorageSync('token') || '') || this._pageHidden || this._unloaded) return
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

  // ===== 赛程时间展示 =====
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
    const time = officialSchedule(r)
    const schedule_text = time ? '比赛时间 ' + time.date.slice(5) + ' ' + time.clock : '待管理员安排'
    const window_text = (r.window_start && r.window_end)
      ? (this.fmtTime(r.window_start) + ' ~ ' + this.fmtTime(r.window_end))
      : ''
    let my_side = null
    const team1_is_mine = sameTeam(r.team1_id, myTeamId)
    const team2_is_mine = sameTeam(r.team2_id, myTeamId)
    if (team1_is_mine) my_side = 'team1'
    else if (team2_is_mine) my_side = 'team2'
    const can_chat = !!isCaptain && !!my_side && r.team1_id != null && r.team2_id != null
    const is_mine = !!my_side
    const team1_display_name = teamDisplayName(r.team1_name, r.team1_id)
    const team2_display_name = teamDisplayName(r.team2_name, r.team2_id)
    const team1_initial = teamInitial(r.team1_name, r.team1_id)
    const team2_initial = teamInitial(r.team2_name, r.team2_id)
    const my_team_name = is_mine ? (team1_is_mine ? team1_display_name : team2_display_name) : ''
    const my_team_initial = is_mine ? (team1_is_mine ? team1_initial : team2_initial) : ''
    const opponent_id = is_mine ? (team1_is_mine ? r.team2_id : r.team1_id) : null
    const opponent_name = is_mine ? (team1_is_mine ? team2_display_name : team1_display_name) : ''
    const opponent_initial = is_mine ? (team1_is_mine ? team2_initial : team1_initial) : ''
    const validResult = r.status === 'finished' && r.team1_id != null && r.team2_id != null &&
      (sameTeam(r.winner_id, r.team1_id) || sameTeam(r.winner_id, r.team2_id))
    const result_text = is_mine && opponent_id == null ? '轮空' : validResult && is_mine ? (sameTeam(r.winner_id, myTeamId) ? '胜' : '负')
      : r.status === 'finished' ? '已结束' : r.status === 'in_progress' ? '进行中' : '待比赛'
    const my_score = is_mine ? (team1_is_mine ? r.team1_score : r.team2_score) : null
    const opponent_score = is_mine ? (team1_is_mine ? r.team2_score : r.team1_score) : null
    const bo3_text = (Array.isArray(r.bo3_scores) ? r.bo3_scores : []).map(g =>
      team2_is_mine ? g.t2 + '-' + g.t1 : g.t1 + '-' + g.t2).join(' / ')
    const stage_label = r.stage_label || (r.group_name === '淘汰赛' ? this.knockoutStageLabel(
      (this.detail && this.detail.rounds || []).filter(item => item.group_name === '淘汰赛').slice().sort(compareRounds).findIndex(item => item.id === r.id)
    ) : groupLabel(r.group_name))
    return Object.assign({}, r, { schedule_text, window_text, can_chat, my_side,
      is_mine, team1_is_mine, team2_is_mine, opponent_id, opponent_name, my_score, opponent_score, result_text, bo3_text,
      team1_display_name, team2_display_name, team1_initial, team2_initial, my_team_name, my_team_initial, opponent_initial,
      group_label: groupLabel(r.group_name), stage_label,
      group_match_index: r.group_match_index || null, group_match_text: r.group_match_text || '', group_round_label: r.group_round_label || '' })
  },

  // 赛程通知直达对应场次，时间操作仅在管理后台提供。
  openNotificationRound() {
    const roundId = this._notificationRoundId
    this._notificationRoundId = null
    const round = (this.detail && this.detail.rounds || []).find(r => r.id === roundId)
    if (!round) {
      wx.showToast({ title: '该对阵已移除，请查看最新赛程', icon: 'none' })
      return
    }
    if (round.group_name === '附加赛' && !this.data.hasPlayoffStage) {
      wx.showToast({ title: '本赛事已不设附加赛，该通知仅作历史记录', icon: 'none' })
      return
    }
    const tab = round.group_name === '淘汰赛' ? 'knockout' : ['上区', '下区'].includes(round.group_name) ? 'legend' : 'challenger'
    this.rememberTabSelection()
    const updates = { tab, mainTab: 'matches', focusedRoundId: roundId, bracketExpanded: false,
      matchupStage: this.roundStage(round), matchupGroup: isChallengerGroup(round.group_name) || tab === 'legend' ? round.group_name : '',
      onlyMyTeam: false, matchupDate: '' }
    if (tab === 'legend') updates.legendStage = Math.max(0, this.data.legendSwiper.findIndex(s => s.rounds.some(r => r.id === roundId)))
    if (tab === 'challenger') {
      updates.challengerStage = round.group_name === '附加赛' ? Math.max(0, this.data.challengerSwiper.findIndex(s => s.type === 'playoff')) : 0
      updates.selectedChallengerGroup = isChallengerGroup(round.group_name) ? round.group_name : ''
      updates.selectedChallengerRoundCount = isChallengerGroup(round.group_name)
        ? (this.data.challengerSwiper[0].groups[round.group_name] || []).length : this.data.challengerSwiper[0].count
    }
    this.setData(updates)
    this.refreshMatchupFilters(() => {
      if (typeof wx.pageScrollTo === 'function') wx.pageScrollTo({ selector: '#round-' + roundId, duration: 180 })
    })
  },

  // 重新拉详情（保持我的队长权限标记）
  refreshDetail() {
    return this.requestDetail()
  }
})
