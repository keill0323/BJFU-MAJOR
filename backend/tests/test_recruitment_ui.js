const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const root = path.resolve(__dirname, '../..')
const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r }); return { promise, resolve } }
function load(file, overrides = {}, token = 'token') {
  let definition
  const state = { token, toasts: [], routes: [], modal: null, subscribe: null }
  const api = { BASE: '', getRecruitmentPlayers: async () => ({ items: [], has_more: false }), getRecruitmentPosts: async () => ({ items: [{ team_id: 2, team_name: '白桦' }], has_more: false }),
    getMyTeam: async () => null, getMyRecruitment: async () => null,
    getWechatConfig: async () => ({ enabled: true, templates: [{ template_id: 'test-template', label: '入队邀请' }] }),
    saveWechatSubscriptions: async () => {}, getPersonalNoticeSummary: async () => ({ total: 3, invitation_count: 1, application_count: 2, schedule_count: 0 }), ...overrides }
  const wx = { getStorageSync: () => state.token, showToast: value => state.toasts.push(value),
    showModal: options => { state.modal = options }, requestSubscribeMessage: options => { state.subscribe = options },
    navigateTo: options => state.routes.push(options.url), stopPullDownRefresh() {} }
  vm.runInNewContext(fs.readFileSync(path.join(root, 'miniprogram', file), 'utf8'), {
    Page: d => { definition = d }, Component: d => { definition = d }, require: name => name.endsWith('/rank.js') ? require(path.join(root, 'miniprogram/utils/rank.js')) : api, wx, console
  })
  const page = { ...definition, ...definition.methods }
  page.data = JSON.parse(JSON.stringify(page.data))
  page.setData = (data, callback) => { Object.assign(page.data, data); if (callback) callback() }
  return { page, state, api }
}

test('recruitment guests browse without personal reads and can navigate to login', async () => {
  let personalReads = 0
  const { page, state } = load('pages/recruitment/recruitment.js', {
    getMyTeam: () => { personalReads++; throw Error('private') }, getMyRecruitment: () => { personalReads++; throw Error('private') }
  }, '')
  await page.load()
  assert.equal(page.data.posts.length, 1)
  assert.equal(personalReads, 0)
  page.apply({ currentTarget: { dataset: { id: 2 } } })
  assert.match(state.routes[0], /login/)
})

test('all players tab displays freshmen, seniors, unknown identities and already teamed players', async () => {
  const { page } = load('pages/recruitment/recruitment.js', { getRecruitmentPlayers: async () => ({ items: [
    { id: 1, is_verified: true, identity: 'new_student', rank: 'A+', has_team: false },
    { id: 2, is_verified: true, identity: 'senior', rank: 'B', has_team: true },
    { id: 3, is_verified: false, identity: null }, { id: 4, is_verified: true, identity: null }
  ], has_more: false }) })
  await page.load()
  assert.equal(page.data.tab, 'players')
  assert.equal(page.data.players.length, 4)
  assert.equal(page.data.players[0].identityText, '新生')
  assert.equal(page.data.players[1].identityText, '老生')
  assert.match(page.data.players[2].identityText, /未认证/)
  assert.match(page.data.players[3].identityText, /待确认/)
})

test('directory invitations require a captain and an unteamed target, and preserve the existing invitation API', async () => {
  const sent = []
  const { page, state } = load('pages/recruitment/recruitment.js', { invitePlayer: async (team, player) => sent.push([team, player]) })
  page.setData({ token: 'token', players: [{ id: 1, displayName: '新队友', has_team: false }, { id: 2, has_team: true }] })
  page.invite({ currentTarget: { dataset: { id: 1 } } })
  assert.equal(state.modal, null)
  page.setData({ mine: { team_id: 10, team_name: '测试队' } })
  page.invite({ currentTarget: { dataset: { id: 2 } } })
  assert.equal(state.modal, null)
  page.invite({ currentTarget: { dataset: { id: 1 } } })
  await state.modal.success({ confirm: true })
  assert.deepEqual(sent, [[10, 1]])
})

test('all application entry points collect an optional introduction and submit it only after confirmation', async () => {
  for (const name of ['recruitment', 'teams', 'team']) {
    const submissions = []
    const { page, state } = load('pages/' + name + '/' + name + '.js', { applyJoin: async (id, message) => submissions.push({ id, message }) })
    page.setData({ token: 'token', posts: [{ team_id: 2, team_name: '白桦' }] })
    const click = { currentTarget: { dataset: { id: 2, name: '白桦' } } }
    if (name === 'recruitment') page.apply(click)
    else await page.applyJoin(click)
    assert.equal(state.modal.editable, true)
    assert.match(state.modal.placeholderText, /自我介绍/)
    await state.modal.success({ confirm: false, content: '不提交' })
    assert.equal(submissions.length, 0)
    await state.modal.success({ confirm: true, content: '  我主打辅助，晚上在线  ' })
    assert.deepEqual(submissions, [{ id: 2, message: '我主打辅助，晚上在线' }])
  }
})

test('introduction is optional and overlong text is rejected without a backend request', async () => {
  const submissions = []
  const { page, state } = load('pages/recruitment/recruitment.js', { applyJoin: async (id, message) => submissions.push(message) })
  page.setData({ token: 'token', posts: [{ team_id: 2, team_name: '白桦' }] })
  page.apply({ currentTarget: { dataset: { id: 2 } } })
  await state.modal.success({ confirm: true, content: '字'.repeat(201) })
  assert.equal(submissions.length, 0)
  await state.modal.success({ confirm: true, content: '' })
  assert.deepEqual(submissions, [''])
})

test('failed personal reads block applications without hiding public posts', async () => {
  const { page, state } = load('pages/recruitment/recruitment.js', { getMyTeam: async () => { throw Error('offline') } })
  await page.load()
  assert.equal(page.data.accountFailed, true)
  assert.equal(page.data.posts.length, 1)
  page.apply({ currentTarget: { dataset: { id: 2 } } })
  assert.equal(state.modal, null)
})

test('recruitment keeps the original draft on failed save and prevents repeated submit', async () => {
  const pending = deferred()
  let writes = 0
  const { page } = load('pages/recruitment/recruitment.js', { saveRecruitment: () => { writes++; return pending.promise } })
  page.setData({ mine: { team_id: 1 }, draft: '招募一位辅助', editing: true })
  const saving = page.save()
  await page.save()
  assert.equal(writes, 1)
  pending.resolve({})
  await saving
  const bad = load('pages/recruitment/recruitment.js', { saveRecruitment: async () => { throw Error('offline') } }).page
  bad.setData({ mine: { team_id: 1 }, draft: '保留内容', editing: true })
  await bad.save()
  assert.equal(bad.data.editing, true)
  assert.equal(bad.data.draft, '保留内容')
})

test('a delayed recruitment load cannot restore another account data', async () => {
  const pending = deferred()
  const { page, state } = load('pages/recruitment/recruitment.js', { getMyRecruitment: () => pending.promise })
  const reading = page.load()
  state.token = ''
  await page.load()
  pending.resolve({ team_id: 9, content: 'private' })
  await reading
  assert.equal(page.data.mine, null)
})

test('WeChat config is prefetched but only a direct tap opens subscription, with duplicate IDs removed', async () => {
  let writes = 0
  const { page, state } = load('components/subscribe-notice/subscribe-notice.js', {
    getWechatConfig: async () => ({ enabled: true, templates: [{ template_id: 'same', label: '邀请' }, { template_id: 'same', label: '申请' }] }),
    saveWechatSubscriptions: async choices => { writes++; assert.equal(choices.same, 'accept') }
  })
  page._alive = true
  await page.loadConfig()
  assert.equal(state.subscribe, null)
  page.subscribe()
  assert.equal(state.subscribe.tmplIds.length, 1)
  await state.subscribe.success({ same: 'accept' })
  assert.equal(writes, 1)
  assert.equal(page.data.busy, false)
})

test('missing templates show unavailable honestly and do not ask WeChat for permission', async () => {
  const { page, state } = load('components/subscribe-notice/subscribe-notice.js', { getWechatConfig: async () => ({ enabled: false, templates: [] }) })
  page._alive = true
  await page.loadConfig()
  page.subscribe()
  assert.equal(page.data.ready, true)
  assert.equal(page.data.configured, false)
  assert.equal(state.subscribe, null)
})

test('subscription callback from an old login cannot record permission on the new account', async () => {
  let writes = 0
  const { page, state } = load('components/subscribe-notice/subscribe-notice.js', { saveWechatSubscriptions: async () => { writes++ } })
  page._alive = true
  await page.loadConfig()
  page.subscribe()
  state.token = 'another'
  await state.subscribe.success({ 'test-template': 'accept' })
  assert.equal(writes, 0)
})

test('home exposes separate real counts, links to correct inbox section and clears on logout', async () => {
  const { page, state } = load('pages/index/index.js')
  await page.loadPersonalNotices()
  assert.equal(page.data.personalNotice.application_count, 2)
  page.goNotice({ currentTarget: { dataset: { section: 'applications' } } })
  assert.equal(state.routes[0], '/pages/messages/messages?section=applications')
  state.token = ''
  await page.loadPersonalNotices()
  assert.equal(page.data.personalNotice, null)
})

test('home ignores stale notification responses and distinguishes failure from no pending messages', async () => {
  const pending = deferred()
  const { page, state } = load('pages/index/index.js', { getPersonalNoticeSummary: () => pending.promise })
  const reading = page.loadPersonalNotices()
  state.token = ''
  await page.loadPersonalNotices()
  pending.resolve({ total: 5 })
  await reading
  assert.equal(page.data.personalNotice, null)
  const failed = load('pages/index/index.js', { getPersonalNoticeSummary: async () => { throw Error('offline') } }).page
  await failed.loadPersonalNotices()
  assert.equal(failed.data.personalNoticeError, true)
  assert.equal(failed.data.personalNotice, null)
})
