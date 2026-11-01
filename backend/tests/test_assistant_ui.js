// Exercise the real mini-program pages using isolated in-memory API responses.
const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const { parseWxml, createRenderer } = require('../../tools/preview-miniprogram.js')
const root = path.resolve(__dirname, '../..')
const event = dataset => ({ currentTarget: { dataset } })
const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b }); return { promise, resolve, reject } }
const ready = { enabled: true, available: true }
const preview = (id = 'action_123', extras = {}) => Object.assign({ type: 'registration_preview', title: '报名预览', summary: '提交后待审核', mode: 'personal',
  expires_at: '2099-01-01T00:00:00Z', action: { kind: 'confirm_registration', action_id: id }, items: [] }, extras)

function load(relative = 'pages/assistant/assistant.js', overrides = {}, token = 'isolated-token') {
  const calls = { navigations: [], contacts: [], toasts: [], posts: [], confirmations: [] }
  const storage = { token }
  const api = Object.assign({
    getAssistantStatus: async () => ready,
    createAssistantConversation: async () => ({ conversation_id: 'conversation-1', messages: [] }),
    getAssistantConversation: async () => ({ conversation_id: 'conversation-1', messages: [] }),
    sendAssistantMessage: async (id, body) => { calls.posts.push({ id, body }); return { conversation_id: id, reply: '查询完成', cards: [] } },
    confirmAssistantAction: async id => { calls.confirmations.push(id); return { status: 'ok', message: '报名已提交，待审核', data: { registration_status: 'pending' }, cards: [{ type: 'registration_result', title: '待审核', items: [] }] } },
    getMatches: async () => [{ id: 8, name: '起源杯' }], getTeams: async () => [{ id: 12, name: '银河战舰' }],
    getRecruitmentPlayers: async () => ({ items: [{ id: 114, nickname: '测试选手' }] })
  }, overrides)
  let definition
  const file = path.join(root, 'miniprogram', relative)
  const wx = {
    getStorageSync: key => storage[key], getWindowInfo: () => ({ windowHeight: 700 }),
    navigateTo: options => calls.navigations.push(options.url), reLaunch: options => calls.navigations.push(options.url),
    showToast: options => calls.toasts.push(options), stopPullDownRefresh() {}
  }
  vm.runInNewContext(fs.readFileSync(file, 'utf8'), {
    Page: value => { definition = value }, console, wx,
    require: name => {
      if (name.endsWith('/api.js')) return api
      if (name.endsWith('/chat-client.js')) return { contact: async (...args) => { calls.contacts.push(args) } }
      return require(path.resolve(path.dirname(file), name))
    }
  }, { filename: file })
  function newPage() {
    const page = Object.assign({}, definition)
    page.data = JSON.parse(JSON.stringify(definition.data))
    page.setData = value => Object.assign(page.data, value)
    page.selectComponent = () => null
    return page
  }
  return { page: newPage(), newPage, api, wx, storage, calls }
}

async function open(overrides = {}, token) {
  const result = load(undefined, overrides, token)
  result.page.onLoad()
  await result.page.onShow()
  return result
}

async function replyWith(cards, overrides = {}) {
  const result = await open(Object.assign({ sendAssistantMessage: async () => ({ reply: '请查看结果', cards }) }, overrides))
  result.page.setData({ draft: '看看报名条件' })
  await result.page.send()
  result.cardEvent = event({ message: result.page.data.messages[1].id, card: 0 })
  return result
}

test('home assistant stays first and reachable while checking, disabled, offline, or awaiting a server update', async () => {
  const { page, api, calls } = load('pages/index/index.js', { getAssistantStatus: async () => ({ enabled: false }) })
  const tree = parseWxml(fs.readFileSync(path.join(root, 'miniprogram/pages/index/index.wxml'), 'utf8'))
  tree.scope = page.data
  const assertEntry = label => {
    const html = createRenderer({})(tree)
    assert.equal((html.match(/class="assistant-entry"/g) || []).length, 1)
    assert.match(html, /进入助手/)
    assert.ok(html.indexOf('class="assistant-entry"') < html.indexOf('class="player-card"'))
    assert.ok(html.indexOf('class="assistant-entry"') < html.indexOf('section-heading'))
    assert.ok(html.includes(label))
    page.goAssistant()
    assert.equal(calls.navigations.at(-1), '/pages/assistant/assistant')
  }
  assertEntry('查询状态中')
  await page.loadAssistantStatus()
  assertEntry('暂未开放')
  api.getAssistantStatus = async () => { throw { statusCode: 404, detail: 'Not Found' } }
  await page.loadAssistantStatus()
  assertEntry('待服务更新')
  api.getAssistantStatus = async () => { throw { detail: '网络异常' } }
  await page.loadAssistantStatus()
  assertEntry('暂时无法连接')
})

test('home assistant badge reports ready and unavailable states without letting stale responses overwrite them', async () => {
  const first = deferred(), second = deferred()
  let number = 0
  const { page, api } = load('pages/index/index.js', { getAssistantStatus: () => (++number === 1 ? first : second).promise })
  const a = page.loadAssistantStatus(), b = page.loadAssistantStatus()
  second.resolve({ enabled: false }); await b
  first.resolve(ready); await a
  assert.equal(page.data.assistantEnabled, false)
  assert.equal(page.data.assistantStatusLabel, '暂未开放')
  api.getAssistantStatus = async () => ready
  await page.loadAssistantStatus()
  assert.equal(page.data.assistantState, 'ready')
  assert.equal(page.data.assistantStatusLabel, '可以使用')
  api.getAssistantStatus = async () => ({ enabled: true, available: false })
  await page.loadAssistantStatus()
  assert.equal(page.data.assistantState, 'unavailable')
  assert.equal(page.data.assistantStatusLabel, '服务暂不可用')
})

test('legacy assistant service explains the required server update without starting a conversation', async () => {
  let creates = 0
  const { page } = await open({ getAssistantStatus: async () => { throw { statusCode: 404, detail: 'Not Found' } },
    createAssistantConversation: async () => { creates++ } })
  assert.equal(page.data.enabled, false)
  assert.equal(page.data.available, false)
  assert.equal(page.data.statusError, '赛事助手服务尚未更新，更新后即可使用。')
  page.data.draft = '查询比分'; await page.send()
  assert.equal(creates, 0)
})

test('direct disabled assistant entry has a return-to-events route and never starts a model conversation', async () => {
  let creates = 0
  const { page, calls } = await open({ getAssistantStatus: async () => ({ enabled: false, available: false }), createAssistantConversation: async () => { creates++ } })
  assert.equal(page.data.reason, '赛事助手暂未开放')
  page.data.draft = '查询比分'; await page.send()
  assert.equal(creates, 0)
  page.goEvents(); assert.deepEqual(calls.navigations, ['/pages/index/index'])
})

test('guest assistant shows login without creating an authenticated conversation', async () => {
  let creates = 0
  const { page, calls } = await open({ createAssistantConversation: async () => { creates++ } }, '')
  assert.equal(page.data.logged, false)
  page.data.draft = '我的报名'; await page.send()
  assert.equal(creates, 0)
  assert.deepEqual(calls.navigations, ['/pages/login/login'])
})

test('multi-turn requests reuse a memory-only conversation and send only selected context IDs', async () => {
  let creates = 0
  const { page, calls } = await open({ createAssistantConversation: async () => { creates++; return { conversation_id: 'conversation-1' } } })
  page.setContext('match', 8, '起源杯'); page.setContext('team', 12, '银河战舰'); page.setContext('unknown', 999, '无效')
  page.data.draft = '下一场对阵'; await page.send()
  page.data.draft = '比分呢'; await page.send()
  assert.equal(creates, 1)
  assert.equal(calls.posts.length, 2)
  assert.equal(calls.posts[0].id, calls.posts[1].id)
  assert.equal(JSON.stringify(calls.posts[0].body.context), '{"match_id":8,"team_id":12}')
  assert.match(calls.posts[0].body.client_message_id, /^[A-Za-z0-9_-]{32,64}$/)
  assert.notEqual(calls.posts[0].body.client_message_id, calls.posts[1].body.client_message_id)
  assert.equal(page.data.messages.length, 4)
  assert.equal(calls.contacts.length, 0)
})

test('rapid repeated send only dispatches one turn', async () => {
  const work = deferred(), started = deferred(); let posts = 0
  const { page } = await open({ sendAssistantMessage: () => { posts++; started.resolve(); return work.promise } })
  page.data.draft = '查对阵'; const first = page.send(); await started.promise
  page.data.draft = '另一个问题'; await page.send()
  assert.equal(posts, 1)
  work.resolve({ reply: '查询完成', cards: [] }); await first
})

test('network-loss recovery repeats the identical intent without duplicating the user message', async () => {
  let attempts = 0; const bodies = []
  const { page } = await open({ sendAssistantMessage: async (_, body) => { bodies.push(body); if (++attempts === 1) throw { detail: '网络错误' }; return { reply: '已取得结果', cards: [] } } })
  page.setContext('match', 8, '起源杯'); page.data.draft = '查比分'; await page.send()
  assert.equal(page.data.retryAvailable, true)
  assert.equal(attempts, 1)
  page.setContext('match', 9, '另一个赛事')
  await page.retryMessage()
  assert.equal(bodies[0], bodies[1])
  assert.equal(bodies[1].context.match_id, 8)
  assert.equal(page.data.messages.length, 2)
  assert.equal(page.data.retryAvailable, false)
})

test('processing response awaits an explicit result-retrieval click and does not resend automatically', async () => {
  let attempts = 0
  const { page } = await open({ sendAssistantMessage: async () => { attempts++; throw { code: 'assistant_turn_processing', statusCode: 409, detail: '处理中' } } })
  page.data.draft = '查对阵'; await page.send()
  assert.equal(attempts, 1)
  assert.match(page.data.requestError, /正在处理/)
  page.data.draft = '新问题'; await page.send()
  assert.equal(attempts, 1)
  await page.retryMessage(); assert.equal(attempts, 2)
})

test('account change discards old conversation, selected context and late responses', async () => {
  const work = deferred(), started = deferred()
  const { page, storage, api } = await open({ sendAssistantMessage: () => { started.resolve(); return work.promise } })
  page.setContext('player', 114, '旧选手'); page.data.draft = '旧账号的问题'; const posting = page.send()
  await started.promise
  storage.token = 'another-account'; await page.onShow()
  work.resolve({ reply: '旧账号私有结果', cards: [preview()] }); await posting
  assert.equal(page.data.messages.length, 0)
  assert.equal(JSON.stringify(page.data.context), '{}')
  assert.equal(page._conversationId, null)
  let newConversation = false
  api.createAssistantConversation = async () => { newConversation = true; return { conversation_id: 'new-session' } }
  api.sendAssistantMessage = async () => ({ reply: '新结果', cards: [] })
  page.data.draft = '新问题'; await page.send()
  assert.equal(newConversation, true)
})

test('hide/resume preserves an unresolved intent without a background retry or history overwrite', async () => {
  const work = deferred(), started = deferred(); let posts = 0, restores = 0
  const { page, api } = await open({ sendAssistantMessage: () => { posts++; started.resolve(); return work.promise }, getAssistantConversation: async () => { restores++; return { messages: [] } } })
  page.data.draft = '下一场'; const posting = page.send(); await started.promise
  const intent = page._pendingMessage
  page.onHide(); work.resolve({ reply: '迟到结果', cards: [] }); await posting
  await page.onShow()
  assert.equal(posts, 1); assert.equal(restores, 0)
  assert.equal(page._pendingMessage, intent)
  api.sendAssistantMessage = async (_, body) => { assert.equal(body, intent); return { reply: '同一意图的结果', cards: [] } }
  await page.retryMessage(); assert.equal(page.data.messages.length, 2)
})

test('same-account completed conversation restores only its server history on return', async () => {
  const { page, api } = await open()
  page.data.draft = '问题'; await page.send(); page.onHide()
  api.getAssistantConversation = async () => ({ messages: [{ role: 'system', content: '不应展示' }, { role: 'assistant', content: '赛事已更新', cards: [] }] })
  await page.onShow()
  assert.equal(page.data.messages.length, 1)
  assert.equal(page.data.messages[0].content, '赛事已更新')
})

test('registration is only confirmed from an explicit preview card click and blocks duplicate taps', async () => {
  const confirmation = deferred(); let notices = 0
  const { page, cardEvent, calls } = await replyWith([preview()], { confirmAssistantAction: id => { calls.confirmations.push(id); return confirmation.promise } })
  assert.equal(calls.confirmations.length, 0)
  page.beforePersonalRegistration = async () => { notices++ }
  const first = page.confirmRegistration(cardEvent); await Promise.resolve()
  await page.confirmRegistration(cardEvent)
  assert.equal(calls.confirmations.length, 1)
  confirmation.resolve({ message: '已提交，待审核', cards: [{ type: 'registration_result', title: '待审核', items: [] }] }); await first
  await page.confirmRegistration(cardEvent)
  assert.equal(calls.confirmations.length, 1)
  assert.equal(notices, 1)
  assert.equal(page.data.messages.at(-1).cards[0].title, '待审核')
})

test('ambiguous confirmation response is checked with the same action ID, never a new model turn', async () => {
  let attempts = 0; const ids = []
  const { page, cardEvent } = await replyWith([preview('same-action')], { confirmAssistantAction: async id => { ids.push(id); if (++attempts === 1) throw { detail: '网络错误' }; return { message: '已提交，待审核', cards: [] } } })
  await page.confirmRegistration(cardEvent)
  assert.equal(page.data.messages[1].cards[0].confirmLabel, '核查报名结果')
  // The already-confirmed intent can still be checked after the preview TTL;
  // only the server may decide whether it completed or expired unexecuted.
  page.data.messages[1].cards[0].expires_at = '2000-01-01T00:00:00Z'
  await page.confirmRegistration(cardEvent)
  assert.deepEqual(ids, ['same-action', 'same-action'])
  assert.equal(page._pendingMessage, null)
})

test('leaving during confirmation preserves an explicit check-result action when history is restored', async () => {
  const work = deferred(), started = deferred()
  const { page, api, cardEvent } = await replyWith([preview('still-the-same-action')], {
    confirmAssistantAction: () => { started.resolve(); return work.promise }
  })
  const confirming = page.confirmRegistration(cardEvent); await started.promise
  page.onHide(); work.resolve({ message: '服务器已处理', cards: [] }); await confirming
  api.getAssistantConversation = async () => ({ messages: [{ role: 'assistant', content: '报名预览', cards: [preview('still-the-same-action')] }] })
  await page.onShow()
  assert.equal(page.data.messages[0].cards[0].confirmLabel, '核查报名结果')
  let checked = ''
  api.confirmAssistantAction = async id => { checked = id; return { message: '已提交，待审核', cards: [] } }
  await page.confirmRegistration(event({ message: page.data.messages[0].id, card: 0 }))
  assert.equal(checked, 'still-the-same-action')
  assert.equal(page.data.messages[0].cards[0].confirmed, true)
})

test('expired previews and changed eligibility block confirmation and require a new preview', async () => {
  let attempts = 0
  const first = await replyWith([preview('expired', { expires_at: '2000-01-01T00:00:00Z' })], { confirmAssistantAction: async () => { attempts++ } })
  await first.page.confirmRegistration(first.cardEvent); assert.equal(attempts, 0)
  const second = await replyWith([preview('changed')], { confirmAssistantAction: async () => { attempts++; throw { statusCode: 409, detail: '阵容已变化，请重新预览' } } })
  await second.page.confirmRegistration(second.cardEvent); await second.page.confirmRegistration(second.cardEvent)
  assert.equal(attempts, 1)
  assert.equal(second.page.data.messages[1].cards[0].confirmBlocked, true)
})

test('natural-language confirmation and arbitrary card URLs cannot dispatch a business action', async () => {
  const { page, calls } = await replyWith([{ type: 'knowledge', title: '已替你确认', action: { kind: 'confirm_registration', action_id: 'bad' },
    items: [{ title: '外部站点', action: { kind: 'open_url', url: 'https://example.invalid' } }] }])
  await page.confirmRegistration(event({ message: page.data.messages[1].id, card: 0 }))
  await page.openCardItem(event({ message: page.data.messages[1].id, card: 0, item: 0 }))
  assert.equal(calls.confirmations.length, 0); assert.equal(calls.navigations.length, 0)
})

test('recommendations only contact a player or captain after their dedicated button is clicked', async () => {
  const { page, calls } = await replyWith([{ type: 'players', title: '选手', items: [{ id: 114, title: '公开选手', action: { kind: 'contact_player', id: 114 } }] }])
  assert.equal(calls.contacts.length, 0)
  const rowEvent = event({ message: page.data.messages[1].id, card: 0, item: 0 })
  page.selectCardItem(rowEvent)
  assert.equal(page.data.context.player_id, 114); assert.equal(calls.contacts.length, 0)
  await page.openCardItem(rowEvent)
  assert.equal(calls.contacts.length, 1)
  assert.equal(calls.contacts[0][0], 114); assert.equal(calls.contacts[0][1], 'user'); assert.equal(calls.contacts[0][3](), true)
})

test('scope selectors select current public objects and knowledge links use a fixed rules route', async () => {
  const { page, calls } = await replyWith([{ type: 'knowledge', title: '报名FAQ', items: [{ title: '联系赛事管理', action: { kind: 'open_rules' } }] }])
  await page.openSelector(event({ kind: 'match' })); await page.chooseOption(event({ id: 8 }))
  await page.openSelector(event({ kind: 'team' })); page.chooseOption(event({ id: 12 }))
  await page.openSelector(event({ kind: 'player' })); page.chooseOption(event({ id: 114 }))
  assert.equal(JSON.stringify(page.data.context), '{"match_id":8,"team_id":12,"player_id":114}')
  await page.openCardItem(event({ message: page.data.messages[1].id, card: 0, item: 0 }))
  assert.deepEqual(calls.navigations, ['/pages/rules/rules'])
})

test('imported statistical players use an opaque identity and selecting another match clears it', async () => {
  const key = 'a'.repeat(32)
  const { page, calls } = await replyWith([{ type: 'player_stats', title: '同名选手', items: [{ id: key, title: '同名 · 队伍甲',
    action: { kind: 'select_stats_player', player_key: key, match_id: 8 } }] }])
  await page.openCardItem(event({ message: page.data.messages[1].id, card: 0, item: 0 }))
  assert.equal(page.data.context.stats_player_key, key)
  assert.equal(page.data.context.match_id, 8)
  assert.equal(page.data.context.player_id, undefined)
  assert.equal(calls.contacts.length, 0)
  page.setContext('player', 114, '本地用户')
  page.setContext('match', 9, '另一赛事')
  assert.equal(page.data.context.stats_player_key, undefined)
  assert.equal(page.data.context.player_id, undefined)
  page.clearContext(event({ kind: 'match' }))
  assert.equal(JSON.stringify(page.data.context), '{}')
  page.data.draft = '查询'; await page.send()
  assert.equal(page._pendingMessage, null)
})

test('changing or clearing an event also clears its team, while selecting the same event preserves it', async () => {
  const { page, calls } = await open()
  page.setContext('match', 8, '原赛事'); page.setContext('team', 12, '原赛事队伍')
  page.setContext('match', 8, '同一赛事')
  assert.equal(page.data.context.team_id, 12)
  assert.equal(page.data.contextLabels.team, '原赛事队伍')
  page.setContext('match', 9, '另一赛事')
  assert.equal(page.data.context.team_id, undefined)
  assert.equal(page.data.contextLabels.team, undefined)
  page.setContext('team', 22, '另一赛事队伍')
  page.clearContext(event({ kind: 'match' }))
  assert.equal(JSON.stringify(page.data.context), '{}')
  assert.equal(JSON.stringify(page.data.contextLabels), '{}')
  page.data.draft = '查看对阵'; await page.send()
  assert.equal(JSON.stringify(calls.posts[0].body.context), '{}')
})

test('selecting an event with an empty composer immediately continues the query using its ID', async () => {
  const { page, api, calls } = await replyWith([{ type: 'matches', title: '请选择赛事', items: [
    { id: 8, title: '起源杯', action: { kind: 'open_match', id: 8 } }
  ] }])
  const row = event({ message: page.data.messages[1].id, card: 0, item: 0 })
  api.sendAssistantMessage = async (id, body) => { calls.posts.push({ id, body }); return { reply: '赛事详情', cards: [] } }
  page.data.draft = ''
  await page.selectCardItem(row)
  assert.equal(calls.posts.length, 1)
  const followUp = calls.posts[0].body
  assert.equal(followUp.context.match_id, 8)
  assert.match(followUp.message, /以所选赛事继续刚才的查询/)
  assert.equal(page.data.draft, '')
  assert.equal(page.data.messages.length, 4)
  assert.equal(calls.confirmations.length, 0)
  assert.match(calls.toasts.at(-1).title, /正在查询/)
  const tree = parseWxml(fs.readFileSync(path.join(root, 'miniprogram/pages/assistant/assistant.wxml'), 'utf8'))
  tree.scope = page.data
  assert.match(createRenderer({})(tree), /已选 · 再次查询/)
})

test('automatic event query preserves the existing draft and rejects double taps while sending', async () => {
  const work = deferred(), started = deferred()
  const { page, api, calls } = await replyWith([{ type: 'matches', title: '选择', items: [{ id: 8, title: '起源杯' }] }])
  api.sendAssistantMessage = (_, body) => { calls.posts.push({ body }); started.resolve(); return work.promise }
  page.data.draft = '我还没写完的另一个问题'
  const row = event({ message: page.data.messages[1].id, card: 0, item: 0 })
  const first = page.selectCardItem(row)
  await started.promise
  await page.selectCardItem(row)
  await page.openCardItem(row)
  assert.equal(calls.posts.length, 1)
  assert.equal(calls.posts[0].body.context.match_id, 8)
  assert.equal(page.data.draft, '我还没写完的另一个问题')
  work.resolve({ reply: '这是所选赛事的结果', cards: [preview()] }); await first
  assert.equal(page.data.draft, '我还没写完的另一个问题')
  assert.equal(page.data.messages.at(-1).content, '这是所选赛事的结果')
  assert.equal(calls.confirmations.length, 0)
})

test('failed automatic queries keep the event and draft and retry the same intent explicitly', async () => {
  const { page, api, calls } = await replyWith([{ type: 'matches', title: '选择', items: [{ id: 8, title: '起源杯' }] }])
  api.sendAssistantMessage = async (_, body) => { calls.posts.push({ body }); throw { detail: '网络错误' } }
  page.data.draft = '未发送草稿'
  const row = event({ message: page.data.messages[1].id, card: 0, item: 0 })
  await page.selectCardItem(row)
  assert.equal(page.data.context.match_id, 8)
  assert.equal(page.data.contextLabels.match, '起源杯')
  assert.equal(page.data.draft, '未发送草稿')
  assert.equal(page.data.retryAvailable, true)
  assert.equal(page.data.requestError, '网络错误')
  await page.selectCardItem(row)
  assert.equal(calls.posts.length, 1)
  const intent = page._pendingMessage
  api.sendAssistantMessage = async (_, body) => { assert.equal(body, intent); return { reply: '赛事查询已完成', cards: [] } }
  await page.retryMessage()
  assert.equal(page.data.messages.length, 4)
  assert.equal(page.data.draft, '未发送草稿')
})

test('team and player selection remains a visible context choice without an automatic query', async () => {
  const { page, calls } = await replyWith([{ type: 'teams', title: '队伍', items: [{ id: 12, title: '银河战舰' }] }])
  const row = event({ message: page.data.messages[1].id, card: 0, item: 0 })
  await page.selectCardItem(row)
  assert.equal(page.data.context.team_id, 12)
  assert.equal(calls.posts.length, 0)
  assert.equal(calls.toasts.at(-1).title, '已选为查询对象')
  const tree = parseWxml(fs.readFileSync(path.join(root, 'miniprogram/pages/assistant/assistant.wxml'), 'utf8'))
  tree.scope = page.data
  assert.match(createRenderer({})(tree), /已选为查询对象 ✓/)
})

test('event detail navigation declares the assistant origin and failed navigation preserves the page', async () => {
  const { page, wx, calls } = await replyWith([{ type: 'matches', title: '赛事', items: [
    { id: 8, title: '起源杯', action: { kind: 'open_match', id: 8 } }
  ] }])
  page.data.draft = '返回后继续写'
  const before = JSON.stringify(page.data.messages)
  wx.navigateTo = options => { calls.navigations.push(options.url); options.fail({ errMsg: 'navigateTo:fail' }) }
  await page.openCardItem(event({ message: page.data.messages[1].id, card: 0, item: 0 }))
  assert.equal(calls.navigations.at(-1), '/pages/match/match?id=8&source=assistant')
  assert.match(calls.toasts.at(-1).title, /页面未能打开/)
  assert.equal(JSON.stringify(page.data.messages), before)
  assert.equal(page.data.draft, '返回后继续写')
  assert.equal(page._visible, true)
})

test('same-account page destruction restores history, selected IDs and the unsent draft in memory', async () => {
  const { page, newPage, api, storage } = await open()
  page.setContext('match', 8, '起源杯')
  page.data.draft = '已发送的问题'; await page.send()
  page.data.draft = '尚未发送的草稿'
  page.onHide(); page.onUnload()
  api.getAssistantConversation = async id => { assert.equal(id, 'conversation-1'); return { messages: [
    { role: 'user', content: '已发送的问题', cards: [] }, { role: 'assistant', content: '查询完成', cards: [] }
  ] } }
  const fresh = newPage(); fresh.onLoad(); await fresh.onShow()
  assert.equal(fresh._conversationId, 'conversation-1')
  assert.equal(fresh.data.messages.length, 2)
  assert.equal(fresh.data.messages[1].content, '查询完成')
  assert.equal(fresh.data.context.match_id, 8)
  assert.equal(fresh.data.contextLabels.match, '起源杯')
  assert.equal(fresh.data.draft, '尚未发送的草稿')
  assert.deepEqual(Object.keys(storage), ['token'])
})

test('a reconstructed page keeps its local history if refresh fails and does not lose the draft', async () => {
  const { page, newPage, api } = await open()
  page.data.draft = '查赛事'; await page.send()
  page.data.draft = '未发送草稿'; page.onUnload()
  api.getAssistantConversation = async () => { throw { statusCode: 503, detail: '暂时无法刷新' } }
  const fresh = newPage(); fresh.onLoad(); await fresh.onShow()
  assert.equal(fresh.data.messages.length, 2)
  assert.equal(fresh.data.draft, '未发送草稿')
  assert.equal(fresh.data.requestError, '暂时无法刷新')
})

test('reconstructed pending turns reject old replies and only retrieve the same original intent on click', async () => {
  const pending = deferred(), started = deferred(); let restores = 0
  const { page, newPage, api } = await open({ sendAssistantMessage: () => { started.resolve(); return pending.promise },
    getAssistantConversation: async () => { restores++; return { messages: [] } } })
  page.setContext('match', 8, '起源杯'); page.data.draft = '查询对阵'
  const posting = page.send(); await started.promise
  const intent = JSON.stringify(page._pendingMessage)
  page.data.draft = '新草稿'; page.onUnload()
  const fresh = newPage(); fresh.onLoad(); await fresh.onShow()
  pending.resolve({ reply: '旧页面的迟到回包', cards: [] }); await posting
  assert.equal(fresh.data.messages.length, 1)
  assert.equal(fresh.data.retryAvailable, true)
  assert.equal(restores, 0)
  api.sendAssistantMessage = async (id, body) => { assert.equal(id, 'conversation-1'); assert.equal(JSON.stringify(body), intent); return { reply: '同一问题的结果', cards: [] } }
  await fresh.retryMessage()
  assert.equal(fresh.data.messages.length, 2)
  assert.equal(fresh.data.draft, '新草稿')
})

test('reconstructed pages keep uncertain registrations as explicit checks of the same action', async () => {
  const pending = deferred(), started = deferred()
  const { page, newPage, api, cardEvent } = await replyWith([preview('survives-reconstruction')], {
    confirmAssistantAction: () => { started.resolve(); return pending.promise }
  })
  page.beforePersonalRegistration = async () => {}
  const confirming = page.confirmRegistration(cardEvent); await started.promise
  page.onUnload()
  api.getAssistantConversation = async () => ({ messages: [{ role: 'assistant', content: '报名预览', cards: [preview('survives-reconstruction')] }] })
  const fresh = newPage(); fresh.onLoad(); await fresh.onShow()
  const card = fresh.data.messages[0].cards[0]
  assert.equal(card.confirmLabel, '核查报名结果')
  pending.resolve({ message: '旧页面迟到的提交结果', cards: [] }); await confirming
  assert.equal(fresh.data.messages.length, 1)
  let checked
  api.confirmAssistantAction = async id => { checked = id; return { message: '已提交，待审核', cards: [] } }
  fresh.beforePersonalRegistration = async () => {}
  await fresh.confirmRegistration(event({ message: fresh.data.messages[0].id, card: 0 }))
  assert.equal(checked, 'survives-reconstruction')
  assert.equal(card.confirmed, true)
})

test('reconstruction never restores another login and an intervening login invalidates the old snapshot', async () => {
  const { page, newPage, storage } = await open()
  page.setContext('match', 8, '旧赛事'); page.data.draft = '旧账号问题'; await page.send()
  page.data.draft = '旧账号草稿'; page.onUnload()
  storage.token = 'another-account'
  const second = newPage(); second.onLoad(); await second.onShow()
  assert.equal(second.data.messages.length, 0)
  assert.equal(second.data.draft, '')
  assert.equal(JSON.stringify(second.data.context), '{}')
  second.onUnload(); storage.token = 'isolated-token'
  const third = newPage(); third.onLoad(); await third.onShow()
  assert.equal(third.data.messages.length, 0)
  assert.equal(third._conversationId, null)
})

test('new conversation clears the saved history and ignores an old failing restore', async () => {
  const pending = deferred()
  const { page, api, newPage } = await open()
  page.data.draft = '旧问题'; await page.send()
  api.getAssistantConversation = () => pending.promise
  const restoring = page.restore()
  page.newConversation()
  pending.reject({ statusCode: 404, detail: '旧会话已失效' }); await restoring
  assert.equal(page.data.requestError, '')
  page.onUnload()
  const fresh = newPage(); fresh.onLoad(); await fresh.onShow()
  assert.equal(fresh.data.messages.length, 0)
  assert.equal(fresh._conversationId, null)
  assert.equal(fresh.data.draft, '')
})

test('assistant layout bounds its scroll area, keeps the composer reachable and renders pending-result cards as text', async () => {
  const { page } = await replyWith([preview()])
  const source = fs.readFileSync(path.join(root, 'miniprogram/pages/assistant/assistant.wxml'), 'utf8')
  const tree = parseWxml(source); tree.scope = page.data
  const html = createRenderer({})(tree)
  assert.match(html, /报名预览/); assert.match(html, /确认提交报名/)
  assert.match(source, /class="assistant-history" scroll-y/)
  assert.match(source, /bindkeyboardheightchange="onKeyboard"/)
  assert.doesNotMatch(source, /rich-text|catchtouchmove/)
  const css = fs.readFileSync(path.join(root, 'miniprogram/pages/assistant/assistant.wxss'), 'utf8')
  assert.match(css, /\.assistant-history[^}]*min-height:0/)
  assert.match(css, /\.assistant-composer[^}]*flex-shrink:0/)
  page.onKeyboard({ detail: { height: 280 } }); assert.equal(page.data.keyboardHeight, 280)
  const app = JSON.parse(fs.readFileSync(path.join(root, 'miniprogram/app.json'), 'utf8'))
  assert.equal(app.pages.filter(page => page === 'pages/assistant/assistant').length, 1)
})

test('assistant API stays separate from chat, encodes opaque IDs and preserves structured processing errors', async () => {
  const calls = [], module = { exports: {} }
  const wx = { getStorageSync: () => 'test-only-token', request: options => { calls.push(options); options.success({ statusCode: 200, data: {} }) } }
  const file = path.join(root, 'miniprogram/utils/api.js')
  vm.runInNewContext(fs.readFileSync(file, 'utf8'), { module, wx }, { filename: file })
  const api = module.exports
  await api.getAssistantStatus(); await api.createAssistantConversation(); await api.getAssistantConversation('a/b')
  await api.sendAssistantMessage('a/b', { client_message_id: 'one-intent', message: '查比分', context: { match_id: 8 } })
  await api.confirmAssistantAction('x/y'); await api.getMatches()
  assert.equal(calls[3].url.endsWith('/api/assistant/conversations/a%2Fb/messages'), true)
  assert.equal(calls[3].timeout, 75000); assert.equal(calls[5].timeout, 60000)
  assert.equal(calls.some(call => call.url.includes('/api/chat/')), false)
  assert.equal(calls[4].url.endsWith('/api/assistant/actions/x%2Fy/confirm'), true)
  wx.request = options => options.success({ statusCode: 409, data: { detail: { code: 'assistant_turn_processing', message: '正在处理，请稍后重试' } } })
  await assert.rejects(api.sendAssistantMessage('one', {}), error => error.code === 'assistant_turn_processing' && error.detail === '正在处理，请稍后重试')
})
