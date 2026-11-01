const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm')
const { parseWxml, createRenderer } = require('../../tools/preview-miniprogram.js')
const root = path.resolve(__dirname, '../../miniprogram')
const INVITE = 'invite-template-0123456789', APPLY = 'apply-template-0123456789'
const templates = [{ kind: 'team_invitation', label: '入队邀请', template_id: INVITE }, { kind: 'team_application', label: '入队申请', template_id: APPLY }]
const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b }); return { promise, resolve, reject } }
const event = dataset => ({ currentTarget: { dataset } })

function harness(overrides = {}) {
  const calls = { native: [], saved: [], toasts: [], routes: [], businesses: [], modal: null, configReads: 0 }
  const storage = { token: 'isolated-account' }
  const api = Object.assign({
    BASE: '', getWechatConfig: async () => { calls.configReads++; return { enabled: true, templates } },
    getWechatSubscriptionState: async () => ({ enabled: true, templates: templates.map(row => Object.assign({}, row, { enabled: true, has_accepted: true, actual_remaining: 999, needs_resubscribe: false, last_delivery_status: null })) }),
    saveWechatSubscriptions: async (choices, attempt) => { calls.saved.push({ choices, attempt }) },
    getMe: async () => ({ is_verified: true, rank: 'A' }), getRecruitmentPlayers: async () => ({ items: [] }), getRecruitmentPosts: async () => ({ items: [] }), getMyTeam: async () => null, getMyRecruitment: async () => null,
    registerUser: async id => { calls.businesses.push(['personal', id]) }, registerTeam: async (match, team) => { calls.businesses.push(['team', match, team]) },
    saveRecruitment: async (team, content) => { calls.businesses.push(['publish', team, content]) }, invitePlayer: async (team, user) => { calls.businesses.push(['invite', team, user]) },
    applyJoin: async (team, content) => { calls.businesses.push(['apply', team, content]) },
    getAssistantStatus: async () => ({ enabled: true, available: true }), createAssistantConversation: async () => ({ conversation_id: 'test-conversation' }),
    sendAssistantMessage: async () => ({ reply: '请确认报名', cards: [{ type: 'registration_preview', title: '报名预览', mode: 'personal', action: { kind: 'confirm_registration', action_id: 'registration-test' }, items: [] }] }),
    confirmAssistantAction: async id => { calls.businesses.push(['assistant-confirm', id]); return { message: '已提交，待审核', cards: [] } }
  }, overrides.api)
  const wx = { getStorageSync: key => storage[key], getWindowInfo: () => ({ windowHeight: 700 }),
    showToast: options => calls.toasts.push(options.title), showModal: options => { calls.modal = options },
    navigateTo: options => calls.routes.push(options.url), reLaunch: options => calls.routes.push(options.url),
    requestSubscribeMessage: options => { calls.native.push(options); if (overrides.native) overrides.native(options) }
  }
  if (overrides.unsupported) delete wx.requestSubscribeMessage
  const module = { exports: {} }
  vm.runInNewContext(fs.readFileSync(path.join(root, 'utils/wechat-subscriptions.js'), 'utf8'), { module, require: () => api, wx, console })
  const utility = module.exports
  function load(relative) {
    let definition
    const file = path.join(root, relative)
    vm.runInNewContext(fs.readFileSync(file, 'utf8'), {
      Page: value => { definition = value }, Component: value => { definition = value }, wx, console,
      require: name => {
        if (name.endsWith('/api.js')) return api
        if (name.endsWith('/wechat-subscriptions.js')) return utility
        if (name.endsWith('/chat-client.js')) return { contact: async (id, source) => calls.businesses.push(['contact', id, source]) }
        return require(path.resolve(path.dirname(file), name))
      }
    }, { filename: file })
    const page = Object.assign({}, definition, definition.methods)
    page.data = JSON.parse(JSON.stringify(definition.data))
    page.setData = data => Object.assign(page.data, data)
    page.selectComponent = () => null
    return page
  }
  return { utility, load, api, wx, calls, storage }
}

test('configuration and account-specific use state are prefetched without requesting permission', async () => {
  const { utility, calls } = harness()
  await utility.prefetch()
  assert.equal(calls.native.length, 0)
  const state = utility.snapshot()
  assert.equal(state.ready, true); assert.equal(state.configured, true)
  assert.equal(state.templates[0].enabled, true)
  assert.equal(state.templates[0].actual_remaining, 'unknown')
  assert.equal(state.templates[0].has_accepted, true)
})

test('user-gesture scene selects only the existing invitation or application template', async () => {
  const { utility, calls } = harness()
  await utility.prefetch()
  const personal = utility.prompt('personal_registration')
  assert.deepEqual(Array.from(calls.native[0].tmplIds), [INVITE])
  await calls.native[0].success({ [INVITE]: 'accept' }); await personal
  await utility.prefetch()
  const captain = utility.prompt('captain_recruitment')
  assert.deepEqual(Array.from(calls.native[1].tmplIds), [APPLY])
  await calls.native[1].success({ [APPLY]: 'accept' }); await captain
  assert.equal((await utility.prompt('schedule_result')).status, 'unsupported_scene')
  assert.equal(calls.native.length, 2)
})

test('past acceptance never permanently skips future user gestures and each attempt has a new identity', async () => {
  const { utility, calls } = harness()
  await utility.prefetch()
  for (let attempt = 0; attempt < 2; attempt++) {
    const work = utility.prompt('personal_registration')
    await calls.native[attempt].success({ [INVITE]: 'accept' }); await work; await utility.prefetch()
  }
  assert.equal(calls.native.length, 2); assert.equal(calls.saved.length, 2)
  assert.notEqual(calls.saved[0].attempt, calls.saved[1].attempt)
  assert.match(calls.saved[0].attempt, /^[A-Za-z0-9_-]{32,64}$/)
})

test('duplicate native callbacks only persist one authorization attempt even while saving is in flight', async () => {
  const saving = deferred(); let writes = 0
  const { utility, calls } = harness({ api: { saveWechatSubscriptions: async () => { writes++; await saving.promise } } })
  await utility.prefetch()
  const work = utility.prompt('personal_registration')
  const first = calls.native[0].success({ [INVITE]: 'accept' })
  await calls.native[0].success({ [INVITE]: 'accept' })
  assert.equal(writes, 1)
  calls.native[0].fail({ errMsg: 'duplicate callback' })
  saving.resolve(); await first; await work
  assert.equal(writes, 1)
})

test('reject, ban, empty response and cancelled dialogs all resolve without representing available credit', async () => {
  for (const choice of ['reject', 'ban', 'empty', 'cancel']) {
    const { utility, calls } = harness(); await utility.prefetch()
    const work = utility.prompt('personal_registration')
    if (choice === 'cancel') calls.native[0].fail({ errMsg: 'cancel' })
    else await calls.native[0].success(choice === 'empty' ? {} : { [INVITE]: choice })
    const outcome = await work
    assert.notEqual(outcome.status, 'accepted')
    assert.equal(calls.saved.length, ['reject', 'ban'].includes(choice) ? 1 : 0)
    assert.ok(calls.toasts.some(text => /站内消息/.test(text)))
  }
})

test('unsupported WeChat, configuration failure and save failure do not throw or block the business caller', async () => {
  const unsupported = harness({ unsupported: true }); await unsupported.utility.prefetch()
  assert.equal((await unsupported.utility.prompt('personal_registration')).status, 'unsupported')
  const unavailable = harness({ api: { getWechatConfig: async () => { throw Error('offline') } } }); await unavailable.utility.prefetch()
  assert.equal((await unavailable.utility.prompt('personal_registration')).status, 'not_ready')
  const failed = harness({ api: { saveWechatSubscriptions: async () => { throw Error('offline') } } }); await failed.utility.prefetch()
  const work = failed.utility.prompt('personal_registration'); await failed.calls.native[0].success({ [INVITE]: 'accept' })
  assert.equal((await work).status, 'save_failed')
  assert.ok(failed.calls.toasts.some(text => /可能错过微信提醒/.test(text)))
})

test('missing templates give a nonblocking operation-time hint about station messages', async () => {
  const { utility, calls } = harness({ api: { getWechatConfig: async () => ({ enabled: false, templates: [] }) } })
  await utility.prefetch()
  const outcome = await utility.prompt('personal_registration')
  assert.equal(outcome.status, 'unconfigured'); assert.equal(calls.native.length, 0)
  assert.ok(calls.toasts.some(text => /可能错过通知.*站内消息/.test(text)))
})

test('subscription record saving alone uses a short timeout and preserves the attempt ID', async () => {
  const calls = [], module = { exports: {} }
  const wx = { getStorageSync: () => 'isolated-token', request: options => { calls.push(options); options.success({ statusCode: 200, data: {} }) } }
  vm.runInNewContext(fs.readFileSync(path.join(root, 'utils/api.js'), 'utf8'), { module, wx })
  await module.exports.saveWechatSubscriptions({ [INVITE]: 'accept' }, 'unique-attempt-01234567890123456789')
  await module.exports.registerUser(8)
  assert.equal(calls[0].timeout, 10000); assert.equal(calls[1].timeout, 60000)
  assert.equal(calls[0].data.attempt_id, 'unique-attempt-01234567890123456789')
})

test('an old account callback cannot save permission or overwrite the new account state', async () => {
  const { utility, storage, calls } = harness(); await utility.prefetch()
  const work = utility.prompt('personal_registration')
  storage.token = 'other-account'; await utility.prefetch()
  await calls.native[0].success({ [INVITE]: 'accept' }); assert.equal((await work).status, 'account_changed')
  assert.equal(calls.saved.length, 0)
  assert.equal(utility.snapshot().ready, true)
})

test('template changes cannot inherit the old template use or authorization state', async () => {
  const old = { kind: 'team_invitation', label: '邀请', template_id: 'old-template-0123456789' }
  const { utility } = harness({ api: { getWechatConfig: async () => ({ enabled: true, templates }),
    getWechatSubscriptionState: async () => ({ templates: [Object.assign({}, old, { enabled: true, needs_resubscribe: true, last_delivery_status: 'sent' })] }) } })
  await utility.prefetch(); const state = utility.snapshot()
  assert.equal(state.templates[0].enabled, false)
  assert.equal(state.needsResubscribe, false)
  assert.equal(state.templates[0].last_delivery_status, null)
})

test('a successful-use reminder is shown persistently without claiming all WeChat authorizations are exhausted', async () => {
  const { utility, load } = harness({ api: { getWechatSubscriptionState: async () => ({ templates: [{ template_id: INVITE, enabled: true, needs_resubscribe: true, successful_send_count: 1, last_delivery_status: 'sent' }] }) } })
  const component = load('components/subscribe-notice/subscribe-notice.js'); component._alive = true
  await component.loadConfig()
  assert.equal(utility.snapshot().needsResubscribe, true)
  const tree = parseWxml(fs.readFileSync(path.join(root, 'components/subscribe-notice/subscribe-notice.wxml'), 'utf8')); tree.scope = component.data
  const html = createRenderer({})(tree)
  assert.match(html, /已使用过一次微信提醒/); assert.match(html, /不代表全部授权已用完/)
  assert.match(html, /补充订阅微信提醒/); assert.doesNotMatch(html, /额度已耗尽|剩余0次/)
})

test('unknown delivery is kept distinct from confirmed consumption or a promise of retry', async () => {
  const { utility, load } = harness({ api: { getWechatSubscriptionState: async () => ({ templates: [{ template_id: INVITE, enabled: true, needs_resubscribe: false, successful_send_count: 0, last_delivery_status: 'unknown' }] }) } })
  const component = load('components/subscribe-notice/subscribe-notice.js'); component._alive = true; await component.loadConfig()
  const tree = parseWxml(fs.readFileSync(path.join(root, 'components/subscribe-notice/subscribe-notice.wxml'), 'utf8')); tree.scope = component.data
  const html = createRenderer({})(tree)
  assert.equal(utility.snapshot().needsResubscribe, false)
  assert.match(html, /送达状态不确定/); assert.doesNotMatch(html, /已使用过一次/)
})

test('normal personal registration still submits after declining its invitation reminder', async () => {
  const { utility, load, calls } = harness({ native: options => options.success({ [INVITE]: 'reject' }) }); await utility.prefetch()
  const page = load('pages/match/match.js'); page.data.match = { id: 8, status: 'registering' }; page.loadMyStatus = async () => {}
  await page.handleRegister(); assert.equal(calls.native.length, 0)
  await calls.modal.success({ confirm: true })
  assert.deepEqual(calls.businesses, [['personal', 8]])
  assert.deepEqual(Array.from(calls.native[0].tmplIds), [INVITE])
})

test('team registration does not claim the captain can authorize invitations for all members', async () => {
  const { utility, load, calls } = harness(); await utility.prefetch()
  const page = load('pages/match/match.js'); page.data.match = { id: 8, status: 'registering' }; page.data.myTeam = { id: 12, name: '队伍' }; page.data.isCaptain = true; page.loadMyStatus = async () => {}
  await page.handleRegister(); await calls.modal.success({ confirm: true })
  assert.deepEqual(calls.businesses, [['team', 8, 12]])
  assert.equal(calls.native.length, 0)
})

test('publishing recruitment and manual invitations remain available after WeChat failures', async () => {
  const { utility, load, calls } = harness({ native: options => options.fail({ errMsg: 'denied' }) }); await utility.prefetch()
  const page = load('pages/recruitment/recruitment.js')
  page.setData({ token: 'isolated-account', mine: { team_id: 12, team_name: '队伍', can_publish: true }, draft: '招募队员', players: [{ id: 114, displayName: '选手', has_team: false }] })
  page.load = async () => {}
  await page.save()
  assert.deepEqual(calls.businesses[0], ['publish', 12, '招募队员'])
  assert.deepEqual(Array.from(calls.native[0].tmplIds), [APPLY])
  page.invite(event({ id: 114 })); await calls.modal.success({ confirm: true })
  assert.deepEqual(calls.businesses[1], ['invite', 12, 114])
  assert.equal(calls.native.length, 2)
})

test('a changed account during the reminder cannot submit a registration under the replacement login', async () => {
  const { utility, load, calls, storage } = harness(); await utility.prefetch()
  const page = load('pages/match/match.js'); page.data.match = { id: 8, status: 'registering' }; page.loadMyStatus = async () => {}
  await page.handleRegister(); const submit = calls.modal.success({ confirm: true })
  storage.token = 'replacement-account'; await calls.native[0].success({ [INVITE]: 'accept' }); await submit
  assert.equal(calls.businesses.length, 0)
})

test('assistant personal-confirm button uses the invitation template and still confirms after cancellation', async () => {
  const { utility, load, calls } = harness({ native: options => options.fail({ errMsg: 'cancel' }) }); await utility.prefetch()
  const page = load('pages/assistant/assistant.js'); page.onLoad(); await page.onShow()
  page.data.draft = '个人报名'; await page.send()
  assert.equal(calls.native.length, 0); assert.equal(calls.businesses.length, 0)
  await page.confirmRegistration(event({ message: page.data.messages[1].id, card: 0 }))
  assert.deepEqual(Array.from(calls.native[0].tmplIds), [INVITE])
  assert.deepEqual(calls.businesses, [['assistant-confirm', 'registration-test']])
})
