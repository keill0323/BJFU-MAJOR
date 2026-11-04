const api = require('../../utils/api.js')

function wechatSubscriptions(scene) {
  try { const flow = require('../../utils/wechat-subscriptions.js'); return scene ? flow.prompt(scene) : flow.prefetch() }
  catch (_) { return Promise.resolve({ status: 'unavailable' }) }
}

const CARD_TYPES = ['matches', 'teams', 'rounds', 'players', 'player_stats', 'knowledge', 'registration_preview', 'registration_result', 'verification_status']
const ITEM_ACTIONS = ['open_match', 'open_team', 'open_player', 'contact_player', 'contact_team', 'select_match', 'select_team', 'select_player', 'select_stats_player', 'open_rules', 'open_verify']
const VERIFY_SCOPES = ['student', 'rank']
const SCOPE_FIELDS = { match: 'match_id', team: 'team_id', player: 'player_id' }
const positiveId = value => Number.isSafeInteger(Number(value)) && Number(value) > 0 ? Number(value) : null
const plain = (value, limit = 5000) => typeof value === 'string' ? value.slice(0, limit) : ''
const actionId = value => typeof value === 'string' && /^[A-Za-z0-9_-]{1,128}$/.test(value) ? value : ''
const statsKey = value => typeof value === 'string' && /^[a-f0-9]{32}$/.test(value) ? value : ''
// 仅保留本次小程序运行内、同一登录令牌的会话；不把聊天或令牌写入本地存储。
let sessionSnapshot = null
let sessionOwner = null
const clone = value => JSON.parse(JSON.stringify(value))

function verificationScope(item) {
  const action = item && item.action
  return action && action.kind === 'open_verify' && VERIFY_SCOPES.includes(action.scope) ? action.scope : ''
}

function verificationLabel(item, scope, state) {
  if (state && typeof state === 'object') {
    const rank = scope === 'rank'
    if (state.application_status === 'pending' || state.status === 'pending_review') return rank ? '查看段位审核进度' : '查看在校认证进度'
    if (state.status === 'verified') return rank ? '更新段位材料' : '查看在校认证状态'
    if (['rejected', 'update_rejected'].includes(state.status)) return rank ? '重新提交段位材料' : '重新提交在校认证材料'
    if (state.status === 'not_submitted') return rank ? '提交段位材料' : '提交在校认证材料'
    return rank ? '查看段位认证状态' : '查看在校认证状态'
  }
  const labels = scope === 'rank'
    ? ['提交段位材料', '重新提交段位材料', '更新段位材料', '查看段位审核进度', '查看段位认证状态']
    : ['提交在校认证材料', '重新提交在校认证材料', '查看在校认证进度', '查看在校认证状态']
  return labels.includes(item.title) ? item.title : '查看／提交材料'
}

function refreshVerificationCard(card, fresh) {
  return Object.assign({}, card, { summary: fresh.summary,
    items: (card.items || []).map(item => {
      const scope = verificationScope(item)
      const replacement = scope && fresh.items.find(candidate => verificationScope(candidate) === scope)
      return replacement ? Object.assign({}, replacement, { key: item.key }) : Object.assign({}, item, {
        subtitle: '待核查', badge: '待核查', detail: '当前状态尚未确认。',
        actionLabel: scope ? '查看认证状态' : item.actionLabel
      })
    }) })
}

function decorateCards(cards, completed = {}, uncertain = {}, currentVerification = false, verificationStates = {}) {
  return (Array.isArray(cards) ? cards : []).filter(card => card && CARD_TYPES.includes(card.type)).slice(0, 12).map((card, cardIndex) => {
    const id = card.type === 'registration_preview' && card.action && card.action.kind === 'confirm_registration' ? actionId(card.action.action_id) : ''
    const expired = !uncertain[id] && !completed[id] && !!card.expires_at && (!Number.isFinite(Date.parse(card.expires_at)) || Date.parse(card.expires_at) <= Date.now())
    return {
      key: 'card-' + cardIndex, type: card.type, title: plain(card.title, 160),
      summary: (card.type === 'verification_status' && !currentVerification ? '这是查询时的状态，返回页面后会重新核查。\n' : '') + plain(card.summary), expires_at: plain(card.expires_at, 64),
      mode: card.mode || (card.data && card.data.mode) || '',
      confirmActionId: id, confirmed: !!completed[id], confirmBlocked: expired,
      confirmationHint: expired ? '预览已过期，请重新询问报名条件' : uncertain[id] ? '上次报名结果尚未确认，再次点击将核查同一操作' : '',
      confirmLabel: expired ? '预览已过期' : completed[id] ? '已提交确认' : uncertain[id] ? '核查报名结果' : '确认提交报名',
      items: (Array.isArray(card.items) ? card.items : []).filter(item => item && typeof item === 'object').slice(0, 30).map((item, itemIndex) => {
        const itemId = positiveId(item.id), candidate = item.action || {}, kind = ITEM_ACTIONS.includes(candidate.kind) ? candidate.kind : ''
        const target = positiveId(candidate.id), playerKey = statsKey(candidate.player_key), matchId = positiveId(candidate.match_id)
        const scope = VERIFY_SCOPES.includes(candidate.scope) ? candidate.scope : ''
        const validAction = kind === 'open_rules' || (kind === 'open_verify' ? !!scope : kind === 'select_stats_player' ? !!playerKey && !!matchId : !!kind && !!target)
        const label = kind === 'open_verify' && scope ? verificationLabel(item, scope, verificationStates[scope]) : { open_match: '查看赛事', open_team: '查看队伍', open_player: '选择选手',
          contact_player: '联系选手', contact_team: '联系队长', select_match: '选择赛事', select_team: '选择队伍', select_player: '选择选手', select_stats_player: '选择统计选手', open_rules: '查看参赛须知', open_verify: '查看／提交材料' }[kind] || ''
        return { key: 'item-' + itemIndex, id: itemId, title: plain(item.title, 160), subtitle: plain(item.subtitle, 500), detail: plain(item.detail), badge: plain(item.badge, 160),
          action: validAction ? { kind, id: target, player_key: playerKey, match_id: matchId, scope } : null, actionLabel: validAction ? label : '',
          selectKind: itemId ? ({ matches: 'match', teams: 'team', players: 'player' }[card.type] || '') : '',
          selectField: ({ matches: 'match_id', teams: 'team_id', players: 'player_id' }[card.type] || '') }
      })
    }
  })
}

Page({
  data: {
    loading: true, enabled: false, available: false, logged: false, statusError: '', reason: '',
    verificationLoading: false, verificationError: '', verificationCard: null,
    messages: [], draft: '', sending: false, confirming: '', requestError: '', retryAvailable: false,
    context: {}, contextLabels: {}, selector: '', selectorTitle: '', selectorKeyword: '', selectorLoading: false, selectorError: '', options: [],
    scrollTarget: '', endAnchor: 'assistant-end-0', windowHeight: 700, keyboardHeight: 0,
    suggestions: ['帮我报名赛事', '看看我还缺哪些报名条件', '我的下一场对阵是谁？', '帮我完成认证']
  },
  onLoad() {
    this._generation = 0
    this._confirmedActions = {}
    this._uncertainActions = {}
    const info = wx.getWindowInfo ? wx.getWindowInfo() : {}
    if (Number(info.windowHeight) > 0) this.setData({ windowHeight: info.windowHeight })
  },
  onShow() { this._visible = true; this.syncAccount(); wechatSubscriptions().catch(() => {}); return this.loadStatus() },
  onHide() { this.stop() },
  onUnload() { this.stop(); this._conversationId = null; this._pendingMessage = null },
  rememberSession() {
    if (sessionOwner !== this || !this._token || this._token !== (wx.getStorageSync('token') || '')) return
    sessionSnapshot = clone({ token: this._token, conversationId: this._conversationId || null,
      pendingMessage: this._pendingMessage || null, confirmedActions: this._confirmedActions, uncertainActions: this._uncertainActions,
      data: { messages: this.data.messages, draft: this.data.draft, context: this.data.context, contextLabels: this.data.contextLabels,
        requestError: this.data.requestError, retryAvailable: this.data.retryAvailable } })
  },
  stop() {
    this._visible = false
    this._generation = (this._generation || 0) + 1
    this._selectorRequest = null
    this._verificationRequest = null
    this._contactAttempt = null
    this.setData({ sending: false, confirming: '', keyboardHeight: 0, selectorLoading: false, verificationLoading: false })
    if (this._pendingMessage) this.setData({ retryAvailable: true, requestError: '上一条问题的结果尚未取得，返回后可获取结果' })
    if (this._confirmPendingId) {
      this._uncertainActions[this._confirmPendingId] = true
      this.data.messages.forEach(message => (message.cards || []).forEach(card => {
        if (card.confirmActionId === this._confirmPendingId) { card.confirmLabel = '核查报名结果'; card.confirmationHint = '上次报名结果尚未确认，再次点击将核查同一操作' }
      }))
      this.setData({ messages: this.data.messages, requestError: '报名结果尚未确认，返回后可核查同一操作' })
    }
    this.rememberSession()
  },
  syncAccount() {
    const token = wx.getStorageSync('token') || ''
    if (token !== this._token) {
      const saved = this._token === undefined && token && sessionSnapshot && sessionSnapshot.token === token ? clone(sessionSnapshot) : null
      sessionSnapshot = null
      sessionOwner = this
      this._token = token
      this._generation = (this._generation || 0) + 1
      this._conversationId = null; this._pendingMessage = null; this._confirmedActions = {}; this._uncertainActions = {}; this._confirmPendingId = null; this._contactAttempt = null
      this.setData({ messages: [], draft: '', context: {}, contextLabels: {}, requestError: '', retryAvailable: false,
        sending: false, confirming: '', selector: '', options: [], logged: !!token,
        verificationCard: null, verificationError: '', verificationLoading: false })
      if (saved) {
        this._conversationId = saved.conversationId
        this._pendingMessage = saved.pendingMessage
        this._confirmedActions = saved.confirmedActions
        this._uncertainActions = saved.uncertainActions
        this.setData(saved.data)
      }
    }
    return token
  },
  current(g, token) { return this._visible && g === this._generation && token === this._token && token === (wx.getStorageSync('token') || '') },
  hasVerificationCards() { return this.data.messages.some(message => (message.cards || []).some(card => card.type === 'verification_status')) },
  replaceVerificationCards(fresh) {
    let latest = null
    const messages = this.data.messages.map(message => Object.assign({}, message, {
      cards: (message.cards || []).map(card => {
        if (card.type !== 'verification_status') return card
        latest = refreshVerificationCard(card, fresh)
        return latest
      })
    }))
    this.setData({ messages, verificationCard: latest })
  },
  async loadVerification() {
    const token = this.syncAccount(), g = this._generation
    if (!token || !this._visible || !this.hasVerificationCards()) return
    const request = this._verificationRequest = {}
    this.setData({ verificationCard: null, verificationError: '', verificationLoading: !!token })
    if (!token || !this._visible) return
    try {
      const result = await api.getAssistantVerificationStatus()
      if (!this.current(g, token) || this._verificationRequest !== request) return
      const cards = decorateCards((result.cards || []).filter(card => card && card.type === 'verification_status'), {}, {}, true, result.data || {})
      if (!cards.length) throw { detail: '未能确认当前认证状态，请进入认证中心核查' }
      this.replaceVerificationCards(cards[0])
    } catch (error) {
      if (this.current(g, token) && this._verificationRequest === request) {
        this.setData({ verificationError: '当前认证状态尚未确认，请进入认证页核查' })
        this.setData({ messages: this.data.messages.map(message => Object.assign({}, message, { cards: (message.cards || []).map(card => card.type !== 'verification_status' ? card : Object.assign({}, card, {
          summary: '本次未能刷新认证状态，请从卡片进入认证页核查。',
          items: card.items.map(item => Object.assign({}, item, { subtitle: '待核查', badge: '待核查', detail: '当前状态尚未确认。',
            actionLabel: verificationScope(item) ? '查看认证状态' : item.actionLabel }))
        })) })) })
      }
    } finally {
      if (this.current(g, token) && this._verificationRequest === request) this.setData({ verificationLoading: false })
    }
  },
  openVerification(e) {
    const scope = typeof e === 'string' ? e : e && e.currentTarget && e.currentTarget.dataset.scope
    if (!VERIFY_SCOPES.includes(scope) || !this._visible || this.data.sending || this.data.confirming) return
    if (!this.syncAccount()) { this.goLogin(); return }
    // 固定本地页面；用户在原认证页主动选图、确认用途及微信隐私授权。
    wx.navigateTo({ url: '/pages/verify/verify?focus=' + scope + '&source=assistant' })
  },
  async loadStatus() {
    const token = this.syncAccount(), g = this._generation
    const request = this._statusRequest = {}
    this.setData({ loading: true, statusError: '' })
    try {
      const status = await api.getAssistantStatus()
      if (!this.current(g, token) || this._statusRequest !== request) return
      this.setData({ enabled: status.enabled === true, available: status.enabled === true && status.available === true,
        reason: plain(status.reason, 500) || (status.enabled ? '服务暂时不可用，请稍后再试' : '赛事助手暂未开放') })
      if (status.enabled && status.available && token && this._conversationId && !this._pendingMessage) await this.restore(g, token)
    } catch (error) {
      if (this.current(g, token) && this._statusRequest === request) this.setData({ enabled: false, available: false,
        statusError: Number(error && error.statusCode) === 404 ? '赛事助手服务尚未更新，更新后即可使用。' : error.detail || '无法获取助手状态，请重试' })
    } finally {
      if (this.current(g, token) && this._statusRequest === request) {
        if (token && this.hasVerificationCards()) await this.loadVerification()
        if (this.current(g, token) && this._statusRequest === request) this.setData({ loading: false })
      }
    }
  },
  async restore(g = this._generation, token = this._token) {
    if (!this._conversationId) return
    const id = this._conversationId
    try {
      const result = await api.getAssistantConversation(id)
      if (!this.current(g, token) || id !== this._conversationId) return
      this.setData({ messages: (result.messages || []).filter(m => ['user', 'assistant'].includes(m.role)).map((m, index) => ({
        id: 'history-' + index, role: m.role, content: plain(m.content), cards: decorateCards(m.cards, this._confirmedActions, this._uncertainActions)
      })) })
      this.scrollToEnd()
    } catch (error) {
      if (!this.current(g, token) || id !== this._conversationId) return
      if ([404, 410].includes(error.statusCode)) { this._conversationId = null; this._pendingMessage = null; this.setData({ messages: [], retryAvailable: false }) }
      this.setData({ requestError: error.detail || '会话状态未能恢复，请重试' })
    }
  },
  onDraft(e) { this.setData({ draft: plain(e.detail.value, 1000) }) },
  onKeyboard(e) { this.setData({ keyboardHeight: Math.max(0, Number(e.detail.height) || 0) }); this.scrollToEnd() },
  scrollToEnd() {
    this._anchor = (this._anchor || 0) + 1
    this.setData({ scrollTarget: 'assistant-end-' + this._anchor, endAnchor: 'assistant-end-' + this._anchor })
  },
  askSuggestion(e) {
    const index = Number(e.currentTarget.dataset.index), value = this.data.suggestions[index]
    if (!value || this.data.sending) return
    this.setData({ draft: value }); return this.send()
  },
  canUse() {
    this.syncAccount()
    if (!this._token) { this.goLogin(); return false }
    return this._visible && this.data.available && !this.data.loading
  },
  async send() {
    return this.sendMessage(this.data.draft.trim())
  },
  async sendMessage(text, preserveDraft = false) {
    if (!this.canUse() || this.data.sending || this.data.confirming) return
    if (this._pendingMessage) { wx.showToast({ title: '请先获取上一条结果，或开启新对话', icon: 'none' }); return }
    if (!text) return
    const intent = { client_message_id: 'am_' + Date.now().toString(36) + '_' + Math.random().toString(36).slice(2).padEnd(13, '0') + Math.random().toString(36).slice(2).padEnd(13, '0'),
      message: text, context: Object.assign({}, this.data.context) }
    this._pendingMessage = intent
    this.setData({ draft: preserveDraft ? this.data.draft : '', messages: this.data.messages.concat({ id: intent.client_message_id, role: 'user', content: text, cards: [] }) })
    return this.sendIntent(intent)
  },
  retryMessage() {
    if (!this.canUse() || this.data.sending || this.data.confirming || !this._pendingMessage || !this.data.retryAvailable) return
    return this.sendIntent(this._pendingMessage)
  },
  async sendIntent(intent) {
    const token = this._token, g = this._generation
    this.setData({ sending: true, requestError: '', retryAvailable: false })
    try {
      if (!this._conversationId) {
        const conversation = await api.createAssistantConversation()
        if (!this.current(g, token)) return
        if (typeof conversation.conversation_id !== 'string' || !conversation.conversation_id) throw { detail: '会话创建失败，请重试' }
        this._conversationId = conversation.conversation_id
      }
      const result = await api.sendAssistantMessage(this._conversationId, intent)
      if (!this.current(g, token)) return
      this._pendingMessage = null
      this.setData({ messages: this.data.messages.concat({ id: intent.client_message_id + '-reply', role: 'assistant', content: plain(result.reply), cards: decorateCards(result.cards, this._confirmedActions, this._uncertainActions) }) })
      if ((result.cards || []).some(card => card && card.type === 'verification_status')) await this.loadVerification()
      this.scrollToEnd()
    } catch (error) {
      if (!this.current(g, token)) return
      const retry = ![400, 401, 403, 404, 410, 422].includes(error.statusCode)
      if (!retry) this._pendingMessage = null
      this.setData({ requestError: error.code === 'assistant_turn_processing' ? '这条问题正在处理，可稍后获取结果' : error.detail || '未能取得回答，可点击获取结果', retryAvailable: retry })
    } finally {
      if (this.current(g, token)) { this.setData({ sending: false }); this.scrollToEnd() }
    }
  },
  async openSelector(e) {
    if (!this.canUse() || this.data.sending || this.data.confirming) return
    const kind = e.currentTarget.dataset.kind
    if (!SCOPE_FIELDS[kind]) return
    this.setData({ selector: kind, selectorTitle: { match: '选择赛事', team: '选择队伍', player: '选择选手' }[kind], selectorKeyword: '', options: [], selectorError: '' })
    return this.loadOptions()
  },
  closeSelector() { this._selectorRequest = null; this.setData({ selector: '', options: [], selectorLoading: false }) },
  onSelectorKeyword(e) { this.setData({ selectorKeyword: plain(e.detail.value, 64) }) },
  async loadOptions() {
    const kind = this.data.selector, keyword = this.data.selectorKeyword.trim(), token = this._token, g = this._generation
    if (!SCOPE_FIELDS[kind]) return
    const request = this._selectorRequest = {}
    this.setData({ selectorLoading: true, selectorError: '' })
    try {
      const result = kind === 'match' ? await api.getMatches() : kind === 'team' ? await api.getTeams() : await api.getRecruitmentPlayers(null, keyword)
      if (!this.current(g, token) || this._selectorRequest !== request) return
      const source = kind === 'player' ? result.items || [] : Array.isArray(result) ? result : []
      const options = source.map(item => ({ id: positiveId(item.id), title: plain(item.name || item.nickname || item.game_id || '选手' + item.id, 160) }))
        .filter(item => item.id && (!keyword || kind === 'player' || item.title.toLowerCase().includes(keyword.toLowerCase()) || String(item.id) === keyword)).slice(0, 50)
      this.setData({ options })
    } catch (error) {
      if (this.current(g, token) && this._selectorRequest === request) this.setData({ selectorError: error.detail || '列表加载失败，请重试' })
    } finally { if (this.current(g, token) && this._selectorRequest === request) this.setData({ selectorLoading: false }) }
  },
  chooseOption(e) {
    const item = this.data.options.find(item => item.id === Number(e.currentTarget.dataset.id))
    if (!item || !this.data.selector) return
    const kind = this.data.selector
    this.closeSelector()
    return this.selectContext(kind, item.id, item.title)
  },
  selectContext(kind, id, title) {
    if (!this.canUse() || this.data.sending || this.data.confirming || !this.setContext(kind, id, title)) return
    if (kind === 'match') {
      if (this._pendingMessage) { wx.showToast({ title: '已选赛事，请先获取上一条结果', icon: 'none' }); return }
      wx.showToast({ title: '已选择，正在查询赛事', icon: 'none' })
      return this.sendMessage('请以所选赛事继续刚才的查询；如果没有未完成的问题，请介绍赛事当前状态及对阵安排。', true)
    }
    wx.showToast({ title: '已选为查询对象', icon: 'success' })
  },
  setContext(kind, id, title) {
    const field = SCOPE_FIELDS[kind], target = positiveId(id)
    if (!field || !target) return
    const context = Object.assign({}, this.data.context), labels = Object.assign({}, this.data.contextLabels)
    if (kind === 'match' && context.match_id !== target) {
      delete context.team_id; delete context.stats_player_key; delete context.player_id
      delete labels.team; delete labels.stats_player; delete labels.player
    }
    context[field] = target; labels[kind] = plain(title, 160)
    this.setData({ context, contextLabels: labels })
    return true
  },
  clearContext(e) {
    const kind = e.currentTarget.dataset.kind, field = kind === 'stats_player' ? 'stats_player_key' : SCOPE_FIELDS[kind]
    if (!field || this.data.sending || this.data.confirming) return
    const context = Object.assign({}, this.data.context), labels = Object.assign({}, this.data.contextLabels)
    delete context[field]; delete labels[kind]
    if (kind === 'match') {
      delete context.team_id; delete context.stats_player_key; delete context.player_id
      delete labels.team; delete labels.stats_player; delete labels.player
    }
    this.setData({ context, contextLabels: labels })
  },
  getCard(e) {
    const dataset = e.currentTarget.dataset
    const message = this.data.messages.find(message => message.id === dataset.message)
    return message && message.cards[Number(dataset.card)]
  },
  selectCardItem(e) {
    if (!this.canUse() || this.data.sending || this.data.confirming) return
    const card = this.getCard(e), item = card && card.items[Number(e.currentTarget.dataset.item)]
    if (item && item.selectKind) return this.selectContext(item.selectKind, item.id, item.title)
  },
  navigateToDetail(url) {
    const token = this._token, g = this._generation
    this.rememberSession()
    wx.navigateTo({ url, fail: () => {
      if (this.current(g, token)) wx.showToast({ title: '页面未能打开，请重试', icon: 'none' })
    } })
  },
  async openCardItem(e) {
    const card = this.getCard(e), item = card && card.items[Number(e.currentTarget.dataset.item)], action = item && item.action
    if (!action) return
    if (action.kind === 'open_verify') { this.openVerification(action.scope); return }
    if (!this.canUse()) return
    const kind = action.kind, id = positiveId(action.id)
    if (kind === 'open_rules') { wx.navigateTo({ url: '/pages/rules/rules' }); return }
    if (kind === 'select_stats_player') {
      const key = statsKey(action.player_key), matchId = positiveId(action.match_id)
      if (!key || !matchId) return
      if (this.data.context.match_id !== matchId) this.setContext('match', matchId, '赛事 ' + matchId)
      this.setData({ context: Object.assign({}, this.data.context, { stats_player_key: key }), contextLabels: Object.assign({}, this.data.contextLabels, { stats_player: item.title }) })
      return
    }
    if (!id) return
    if (kind === 'open_match') this.navigateToDetail('/pages/match/match?id=' + id + '&source=assistant')
    else if (kind === 'open_team') this.navigateToDetail('/pages/teams/teams?teamId=' + id)
    else if (['open_player', 'select_player', 'select_team', 'select_match'].includes(kind)) return this.selectContext(kind.endsWith('team') ? 'team' : kind.endsWith('match') ? 'match' : 'player', id, item.title)
    else if (['contact_player', 'contact_team'].includes(kind)) {
      if (this._contactAttempt) return
      const attempt = this._contactAttempt = {}, token = this._token, g = this._generation
      try { await require('../../utils/chat-client.js').contact(id, kind === 'contact_team' ? 'team' : 'user', kind === 'contact_team', () => this.current(g, token)) }
      finally { if (this._contactAttempt === attempt) this._contactAttempt = null }
    }
  },
  beforePersonalRegistration() { return wechatSubscriptions('personal_registration') },
  async confirmRegistration(e) {
    if (!this.canUse() || this.data.sending || this.data.confirming) return
    const card = this.getCard(e), id = card && card.confirmActionId
    if (!id || card.confirmed || card.confirmBlocked || this._confirmedActions[id]) return
    if (!this._uncertainActions[id] && card.expires_at && Date.parse(card.expires_at) <= Date.now()) { card.confirmBlocked = true; card.confirmLabel = '预览已过期'; this.setData({ messages: this.data.messages }); return }
    const token = this._token, g = this._generation
    this.setData({ confirming: id, requestError: '' })
    try {
      if (card.mode === 'personal') { try { await this.beforePersonalRegistration() } catch (_) {} }
      if (!this.current(g, token)) return
      this._confirmPendingId = id
      const result = await api.confirmAssistantAction(id)
      if (!this.current(g, token)) return
      this._confirmedActions[id] = true
      delete this._uncertainActions[id]
      card.confirmed = true; card.confirmLabel = '已提交确认'; card.confirmationHint = '以报名结果卡片的状态为准'
      this.setData({ messages: this.data.messages.concat({ id: 'confirmed-' + id, role: 'assistant', content: plain(result.message), cards: decorateCards(result.cards, this._confirmedActions, this._uncertainActions) }) })
      this.scrollToEnd()
    } catch (error) {
      if (!this.current(g, token)) return
      card.confirmBlocked = [400, 401, 403, 404, 409, 410, 422].includes(error.statusCode)
      if (card.confirmBlocked) delete this._uncertainActions[id]
      else this._uncertainActions[id] = true
      card.confirmLabel = card.confirmBlocked ? '请重新预览' : '核查报名结果'
      card.confirmationHint = card.confirmBlocked ? error.detail || '报名条件已变化，请重新询问' : '结果尚未确认，再次点击将核查同一操作'
      this.setData({ messages: this.data.messages, requestError: error.detail || '报名结果尚未确认，请点击核查报名结果' })
    } finally { if (this.current(g, token)) { this._confirmPendingId = null; this.setData({ confirming: '' }) } }
  },
  newConversation() {
    if (this.data.sending || this.data.confirming) return
    this._conversationId = null; this._pendingMessage = null; this._confirmPendingId = null
    this._confirmedActions = {}; this._uncertainActions = {}
    this.setData({ messages: [], requestError: '', retryAvailable: false, draft: '' })
    this.rememberSession()
  },
  goLogin() { wx.navigateTo({ url: '/pages/login/login' }) },
  goEvents() { wx.reLaunch({ url: '/pages/index/index' }) }
})
