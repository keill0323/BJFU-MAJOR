const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm')
const { parseWxml, createRenderer } = require('../../tools/preview-miniprogram.js')
const root = path.resolve(__dirname, '../../miniprogram')
const plain = value => JSON.parse(JSON.stringify(value))
const click = dataset => ({ currentTarget: { dataset } })
const deferred = () => { let resolve; return { promise: new Promise(done => { resolve = done }), resolve: value => resolve(value) } }

function setup(name, overrides = {}) {
  let definition
  const state = { token: 'community-token', routes: [], modals: [], sends: [], reads: [], scrolls: [], toasts: [] }
  const api = {
    BASE: 'https://example.invalid', getMyTeam: async () => null,
    getTeams: async () => [{ id: 2, name: '白桦', status: 'approved', members: [] }],
    getMyRecruitment: async () => null, getRecruitmentPosts: async () => ({ items: [], has_more: false }),
    getRecruitmentPlayers: async () => ({ items: [], has_more: false }),
    getMyInvitations: async () => [], getMyRankApplications: async () => [], getMyTeamApplications: async () => [],
    getAdminNotices: async () => ({ items: [], unread_count: 0, has_more: false }),
    getScheduleNotifications: async () => ({ items: [], unread_count: 0, has_more: false }),
    applyJoin: async (id, message) => { state.sends.push([id, message]) },
    readAdminNotice: async id => state.reads.push(id), readScheduleNotification: async id => state.reads.push(id),
    ...overrides
  }
  vm.runInNewContext(fs.readFileSync(path.join(root, 'pages', name, name + '.js'), 'utf8'), {
    Page: value => { definition = value }, console,
    require: value => value.endsWith('/rank.js') ? require(path.join(root, 'utils/rank.js')) : value.endsWith('/image-upload.js') ? { showImageError() {} } : api,
    wx: { getStorageSync: () => state.token, showModal: options => state.modals.push(options),
      showToast: options => state.toasts.push(options), navigateTo: options => state.routes.push(options.url),
      reLaunch: options => state.routes.push(options.url), pageScrollTo: options => state.scrolls.push(options.selector) }
  })
  const page = definition
  page.data = plain(page.data)
  page.setData = (update, callback) => { Object.assign(page.data, update); if (callback) callback() }
  const render = () => { const tree = parseWxml(fs.readFileSync(path.join(root, 'pages', name, name + '.wxml'), 'utf8')); tree.scope = page.data; return createRenderer({})(tree) }
  return { page, state, api, render }
}

test('team shortcuts route to the requested recruitment task and application summary has a real anchor', () => {
  const { page, state, render } = setup('team')
  for (const tab of ['players', 'posts', 'publish']) page.goRecruitment(click({ tab }))
  assert.deepEqual(state.routes, ['players', 'posts', 'publish'].map(tab => '/pages/recruitment/recruitment?tab=' + tab))
  page.setData({ loading: false, myTeam: { id: 8, status_class: 'approved' }, isCaptain: true, applications: [{ id: 1 }] })
  assert.match(render(), /1 条入队申请待处理/)
  page.showApplications()
  assert.deepEqual(state.scrolls, ['#team-applications'])
  assert.match(fs.readFileSync(path.join(root, 'pages/team/team.wxml'), 'utf8'), /id="team-applications"/)
})

test('publish deep link only opens an eligible captain draft and never publishes automatically', async () => {
  for (const canPublish of [true, false]) {
    const { page } = setup('recruitment', { getMyRecruitment: async () => ({ team_id: 2, team_name: '白桦', can_publish: canPublish, content: '缺步枪手' }) })
    page.onLoad({ tab: 'publish' })
    await page.load()
    assert.equal(page.data.tab, 'posts')
    assert.equal(page.data.editing, canPublish)
    if (canPublish) assert.equal(page.data.draft, '缺步枪手')
  }
  const { page } = setup('recruitment')
  page.onLoad({ tab: 'players' })
  assert.equal(page.data.tab, 'players')
})

test('recruitment descriptions expand individually and submit state survives list refresh for the same account', async () => {
  const { page, state, render } = setup('recruitment', { getRecruitmentPosts: async () => ({ items: [{ team_id: 2, team_name: '白桦', content: '需要队友\n'.repeat(5) }], has_more: false }) })
  page.onLoad({ tab: 'posts' })
  await page.load()
  assert.equal(page.data.posts[0].descriptionLong, true)
  page.toggleDescription(click({ kind: 'post', id: 2 }))
  assert.equal(page.data.expandedPostId, 2)
  page.apply(click({ id: 2 }))
  await state.modals[0].success({ confirm: true, content: '我可以辅助' })
  assert.deepEqual(state.sends, [[2, '我可以辅助']])
  await page.load()
  assert.equal(page.data.posts[0].applied, true)
  assert.match(render(), /申请已提交/)
  page.apply(click({ id: 2 }))
  assert.equal(state.modals.length, 1)
})

test('teams application labels distinguish login, own team, another team and unapproved teams', async () => {
  const { page, state, api } = setup('teams')
  state.token = ''
  await page.loadAccount()
  assert.equal(page.joinAction({ id: 2, status: 'approved' }).applyLabel, '登录申请')
  await page.applyJoin(click({ id: 2 }))
  assert.deepEqual(state.routes, ['/pages/login/login'])
  state.token = 'community-token'
  api.getMyTeam = async () => ({ id: 2 })
  await page.loadAccount()
  assert.equal(page.joinAction({ id: 2 }).applyLabel, '我的队伍')
  assert.equal(page.joinAction({ id: 3 }).applyLabel, '已有队伍')
  api.getMyTeam = async () => null
  await page.loadAccount()
  assert.equal(page.joinAction({ id: 2, status: 'pending' }).applyDisabled, true)
  assert.equal(page.joinAction({ id: 2, status: 'approved' }).applyDisabled, false)
})

test('teams accept an explicit application once and ignore a confirmation after logout', async () => {
  const { page, state } = setup('teams')
  await page.loadTeams()
  await page.loadAccount()
  await page.applyJoin(click({ id: 2, name: '白桦' }))
  await state.modals[0].success({ confirm: true, content: '晚间在线' })
  assert.deepEqual(state.sends, [[2, '晚间在线']])
  assert.equal(page.data.filtered[0].applyLabel, '申请已提交')
  await page.applyJoin(click({ id: 2 }))
  assert.equal(state.modals.length, 1)
  page.setData({ appliedTeamIds: [] })
  await page.applyJoin(click({ id: 2 }))
  state.token = ''
  await state.modals[1].success({ confirm: true, content: '不应发出' })
  assert.equal(state.sends.length, 1)
})

test('a delayed team-account check cannot restore the previous account membership', async () => {
  const old = deferred()
  const { page, state, api } = setup('teams', { getMyTeam: () => old.promise })
  const pending = page.loadAccount()
  state.token = 'new-account'
  api.getMyTeam = async () => ({ id: 9 })
  await page.loadAccount()
  old.resolve({ id: 2 })
  await pending
  assert.equal(page.data.myTeamId, 9)
})

test('message filters do not mark notices read and preserve only actionable or unread rows', () => {
  const { page, state, render } = setup('messages')
  page.setData({ token: state.token, invitations: [{ id: 1, team_name: '待处理邀请', status: 'pending' }],
    teamApplications: [{ id: 2, nickname: '待处理申请', status: 'pending' }], rankApplications: [{ id: 3, ai_rank: 'A+' }],
    adminNotices: [{ id: 4, title: '已读通知', is_read: true }, { id: 5, title: '未读通知', is_read: false }] })
  page.switchFilter(click({ filter: 'pending' }))
  assert.equal(page.data.pendingCount, 2)
  assert.match(render(), /待处理邀请|待处理申请/)
  assert.doesNotMatch(render(), /已读通知|未读通知|申请段位/)
  page.switchFilter(click({ filter: 'unread' }))
  assert.match(render(), /未读通知/)
  assert.doesNotMatch(render(), /已读通知|待处理邀请/)
  page.switchFilter(click({ filter: 'all' }))
  assert.match(render(), /已读通知|未读通知|申请段位/)
  assert.ok(render().indexOf('待处理申请') < render().indexOf('已读通知'))
  assert.ok(render().indexOf('待处理邀请') < render().indexOf('已读通知'))
  assert.deepEqual(state.reads, [])
})

test('empty notice categories collapse while a deep link keeps its destination visible', async () => {
  const { page, state, render } = setup('messages')
  page.setData({ token: state.token })
  page.updateVisible()
  assert.doesNotMatch(render(), /admin-inbox-card/)
  page.onLoad({ section: 'admin' })
  await page.loadAdminNotices()
  assert.match(render(), /admin-inbox-card/)
  assert.deepEqual(state.scrolls, ['#notice-admin'])
  page.switchFilter(click({ filter: 'pending' }))
  assert.match(render(), /暂无待处理事项/)
  assert.doesNotMatch(render(), /admin-inbox-card/)
})

test('chat contact results identify platform IDs without rendering student identifiers', () => {
  const { page, render } = setup('chats')
  page.setData({ logged: true, searched: true, loading: false, people: [{ id: 114, nickname: '选手甲', game_id: 'GameX', student_id: 'PRIVATE-STUDENT' }] })
  const html = render()
  assert.match(html, /平台 ID 114/)
  assert.match(html, /GameX/)
  assert.doesNotMatch(html, /PRIVATE-STUDENT/)
})

test('a notification deep link waits for earlier actionable sections before scrolling once', async () => {
  const pending = deferred()
  const { page, state } = setup('messages', { getMyInvitations: () => pending.promise })
  page.onLoad({ section: 'admin' })
  page.setData({ token: state.token })
  const loading = page.loadMessages()
  await page.loadAdminNotices()
  assert.deepEqual(state.scrolls, [])
  pending.resolve([{ id: 1, team_name: '白桦', status: 'pending' }])
  await loading
  assert.deepEqual(state.scrolls, ['#notice-admin'])
})

test('choosing a message filter while a deep link loads cancels the pending automatic scroll', async () => {
  const pending = deferred()
  const { page, state } = setup('messages', { getMyInvitations: () => pending.promise })
  page.onLoad({ section: 'admin' })
  page.setData({ token: state.token })
  const loading = page.loadMessages()
  await page.loadAdminNotices()
  page.switchFilter(click({ filter: 'pending' }))
  pending.resolve([{ id: 1, team_name: '白桦', status: 'pending' }])
  await loading
  assert.equal(page.data.messageFilter, 'pending')
  assert.equal(page.data.focusedSection, '')
  assert.deepEqual(state.scrolls, [])
})
