const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm')
const { parseWxml } = require('../../tools/preview-miniprogram.js')
const root = path.resolve(__dirname, '../../miniprogram')
const plain = value => JSON.parse(JSON.stringify(value))
const click = (id, action) => ({ currentTarget: { dataset: { id, action } } })
const deferred = () => { let resolve; const promise = new Promise(yes => { resolve = yes }); return { promise, resolve } }
const report = (id, overrides = {}) => ({ id, status: 'pending', reason: '骚扰消息', sender_id: 9,
  created_at: '2026-10-01T12:00:00', message: { id: '100', state: 'active', kind: 'text', content: '举报正文' }, ...overrides })

function load(relative, overrides = {}) {
  const state = { token: 'chat-admin-session', calls: [], contacts: [], toasts: [], modals: [], navigations: [], confirm: true }
  let definition
  const api = { BASE: 'https://example.test', getMe: async () => ({ id: 1, role: 'admin' }), ...overrides.api }
  const chat = {
    call: async (route, method, body) => {
      state.calls.push({ route, method, body: body && plain(body) })
      if (overrides.call) return overrides.call(route, method, body)
      return route.startsWith('/admin/reports') && !method ? { items: [report(1)], pending: 1, has_more: false, next_cursor: null } : { message: '处理完成' }
    },
    contact: async (...args) => { state.contacts.push(args); if (overrides.contact) return overrides.contact(...args) }
  }
  vm.runInNewContext(fs.readFileSync(path.join(root, relative + '.js'), 'utf8'), {
    Page: value => { definition = value }, Component: value => { definition = value }, console,
    require(name) {
      if (name.endsWith('/api.js')) return api
      if (name.endsWith('/chat-client.js')) return chat
      if (name.endsWith('/rank.js')) return require(path.join(root, 'utils/rank.js'))
      if (name.endsWith('/tournament.js')) return require(path.join(root, 'utils/tournament.js'))
      throw new Error('Unexpected dependency: ' + name)
    },
    wx: { getStorageSync: () => state.token,
      showModal: options => { state.modals.push(options); options.success({ confirm: state.confirm }) },
      showToast: options => state.toasts.push(options), navigateTo: options => state.navigations.push(options.url), pageScrollTo() {} }
  })
  const page = { ...definition, ...definition.methods, data: plain(definition.data) }
  page.setData = values => {
    for (const [key, value] of Object.entries(values)) {
      if (key.includes('.')) { const [field, id] = key.split('.'); page.data[field][id] = value }
      else page.data[key] = value
    }
  }
  return { page, state, definition }
}

test('chat moderation admits only admins and never requests reports for other roles', async () => {
  for (const role of ['user', 'reviewer']) {
    const { page, state } = load('pages/admin/chat/chat', { api: { getMe: async () => ({ role }) } })
    await page.onShow()
    assert.equal(page.data.allowed, false)
    assert.match(page.data.error, /仅管理员/)
    assert.equal(state.calls.length, 0)
    await page.resolve(click(1, 'hide'))
    await page.sanction()
    assert.equal(state.calls.length, 0)
  }
})

test('moderation permission and report responses cannot escape their page or account', async () => {
  const permission = deferred(), list = deferred()
  const first = load('pages/admin/chat/chat', { api: { getMe: () => permission.promise } })
  const pendingPermission = first.page.onShow()
  first.state.token = 'another-account'
  permission.resolve({ role: 'admin' })
  await pendingPermission
  assert.equal(first.state.calls.length, 0)
  assert.equal(first.page.data.allowed, false)

  const second = load('pages/admin/chat/chat', { call: () => list.promise })
  const pendingList = second.page.onShow()
  await Promise.resolve(); await Promise.resolve()
  second.page.onHide()
  list.resolve({ items: [report(2)], pending: 1, has_more: false })
  await pendingList
  assert.equal(second.page.data.reports.length, 0)
  assert.equal(second.page.data.allowed, false)
})

test('moderation shows deleted evidence honestly and merges report cursor pages without duplicates', async () => {
  const { page, state } = load('pages/admin/chat/chat', { call: async route => route.includes('before_id=3')
    ? { items: [report(3), report(2, { message: null })], pending: 2, has_more: false }
    : { items: [report(4, { message: { state: 'purged' } }), report(3)], pending: 2, has_more: true, next_cursor: 3 } })
  await page.onShow()
  assert.match(page.data.reports[0].contentText, /删除/)
  await page.loadMore()
  assert.deepEqual(plain(page.data.reports.map(row => row.id)), [4, 3, 2])
  assert.match(page.data.reports[2].contentText, /无法查看正文/)
  assert.equal(state.calls[1].route, '/admin/reports?before_id=3')
})

test('report resolution requires a reason and confirmation and posts the chosen operation exactly once', async () => {
  const { page, state } = load('pages/admin/chat/chat')
  await page.onShow()
  await page.resolve(click(1, 'hide'))
  assert.match(page.data.actionError, /处理原因/)
  assert.equal(state.calls.filter(call => call.method === 'POST').length, 0)
  page.onReason({ currentTarget: { dataset: { id: 1 } }, detail: { value: ' 核实存在骚扰 ' } })
  state.confirm = false
  await page.resolve(click(1, 'hide'))
  assert.equal(state.calls.filter(call => call.method === 'POST').length, 0)
  state.confirm = true
  await page.resolve(click(1, 'hide'))
  const write = state.calls.find(call => call.method === 'POST')
  assert.deepEqual(write, { route: '/admin/reports/1/resolve', method: 'POST', body: { action: 'hide', reason: '核实存在骚扰' } })
  assert.equal(page.data.processing, null)
})

test('a hidden moderation page never displays a late resolution result or refreshes another account', async () => {
  const response = deferred()
  const { page, state } = load('pages/admin/chat/chat', { call: async (route, method) => method === 'POST' ? response.promise : { items: [report(1)], pending: 1 } })
  await page.onShow()
  page.data.reasons[1] = '核查后隐藏'
  const pending = page.resolve(click(1, 'hide'))
  await Promise.resolve(); await Promise.resolve()
  page.onHide()
  state.token = 'new-account'
  response.resolve({ message: '已处理' })
  await pending
  assert.equal(state.toasts.length, 0)
  assert.equal(state.calls.length, 2)
  assert.equal(page.data.reports.length, 0)
})

test('ban and unban use their exact durations and cannot submit invalid users or empty reasons', async () => {
  const { page, state } = load('pages/admin/chat/chat')
  await page.onShow()
  await page.sanction()
  assert.match(page.data.sanctionMessage, /有效/)
  page.data.sanctionUser = '9'; page.data.sanctionReason = ' 持续骚扰 '
  await page.sanction()
  page.onSanctionIndex({ detail: { value: '6' } })
  await page.sanction()
  assert.deepEqual(state.calls.filter(call => call.route === '/admin/sanctions').map(call => call.body), [
    { user_id: 9, hours: 24, reason: '持续骚扰' }, { user_id: 9, hours: 0, reason: '持续骚扰' }
  ])
})

test('recruitment and team profile contact the correct user or captain with page-scoped callbacks', async () => {
  const recruit = load('pages/recruitment/recruitment')
  recruit.page._chatVisible = true; recruit.page._chatGeneration = 1
  recruit.page.data.players = [{ id: 4 }]; recruit.page.data.posts = [{ team_id: 9 }]
  await recruit.page.contactPlayer(click(4)); await recruit.page.contactTeam(click(9))
  assert.deepEqual(recruit.state.contacts.map(args => args.slice(0, 3)), [[4, 'user', false], [9, 'team', true]])
  assert.equal(recruit.state.contacts[0][3](), true)
  recruit.page.onHide()
  assert.equal(recruit.state.contacts[0][3](), false)

  const profile = load('components/team-profile/team-profile')
  profile.page._generation = 1; profile.page._detached = false
  profile.page.data.team = { id: 9, captain_id: 5 }; profile.page.data.members = [{ user_id: 4 }]
  await profile.page.contactCaptain(); await profile.page.contactMember(click(4))
  assert.deepEqual(profile.state.contacts.map(args => args.slice(0, 3)), [[9, 'team', true], [4, 'user', false]])
  profile.state.token = 'another-user'
  assert.equal(profile.state.contacts[0][3](), false)
})

test('round contact is only enabled for participating captains, including finished rounds', async () => {
  const { page, state } = load('pages/match/match')
  const round = { id: 8, team1_id: 1, team2_id: 2, status: 'finished' }
  page.detail = { rounds: [round] }; page.data.myTeamId = 1; page.data.isCaptain = true
  page.onShow()
  assert.equal(page.decorateRound(round, 1, true).can_chat, true)
  assert.equal(page.decorateRound(round, 1, true).can_operate, false)
  assert.equal(page.decorateRound(round, 1, false).can_chat, false)
  assert.equal(page.decorateRound(round, 3, true).can_chat, false)
  await page.contactRound(click(8))
  assert.deepEqual(state.contacts[0].slice(0, 3), [8, 'round', true])
  page.onHide()
  assert.equal(state.contacts[0][3](), false)
})

test('home chat is an independent always-present component and admins get a separate moderation entry', () => {
  const index = JSON.parse(fs.readFileSync(path.join(root, 'pages/index/index.json'), 'utf8'))
  assert.equal(index.usingComponents['chat-entry'], '/components/chat-entry/chat-entry')
  const tree = parseWxml(fs.readFileSync(path.join(root, 'pages/index/index.wxml'), 'utf8'))
  let entryPath
  function walk(node, parents = []) {
    if (node.tag === 'chat-entry') entryPath = parents.concat(node)
    for (const child of node.children || []) walk(child, parents.concat(node))
  }
  walk(tree)
  assert.ok(entryPath)
  assert.equal(entryPath.some(node => node.attrs && ['wx:if', 'wx:elif', 'wx:else'].some(key => key in node.attrs)), false)
  const { page, state } = load('pages/admin/admin')
  page.data.user = { role: 'reviewer' }; page.openChatReports()
  assert.equal(state.navigations.length, 0)
  page.data.user = { role: 'admin' }; page.openChatReports()
  assert.deepEqual(state.navigations, ['/pages/admin/chat/chat'])
})
