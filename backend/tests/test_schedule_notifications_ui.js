const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const test = require('node:test')
const root = path.resolve(__dirname, '../..')
const plain = value => JSON.parse(JSON.stringify(value))
const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r }); return { promise, resolve } }
const notice = (id = 1, overrides = {}) => ({ id, match_id: 7, round_id: 42, kind: 'proposed',
  match_name: '测试赛事', team1_name: '测试队伍', team2_name: '对手队伍',
  scheduled_time: '2036-09-12T19:00:00', created_at: '2036-09-11T18:00:00',
  is_read: false, round_available: true, ...overrides })
const inbox = (items = [notice()], more = false) => ({ items, unread_count: items.filter(n => !n.is_read).length,
  has_more: more, next_cursor: more ? items[items.length - 1].id : null })

function load(relative, methods = {}) {
  const state = { token: 'token', navigations: [], toasts: [], reads: [] }
  const api = { getMyTeamApplications: async () => [], getMyInvitations: async () => [], getMyRankApplications: async () => [],
    getScheduleNotifications: async () => inbox(),
    readScheduleNotification: async id => { state.reads.push(id) }, ...methods }
  let page
  vm.runInNewContext(fs.readFileSync(path.join(root, relative), 'utf8'), {
    Page: value => { page = value }, console,
    require: name => name.endsWith('/api.js') ? api : require(path.join(root, 'miniprogram/utils', name.endsWith('/rank.js') ? 'rank.js' : 'tournament.js')),
    wx: { getStorageSync: key => key === 'token' ? state.token : true,
      navigateTo: options => state.navigations.push(options), showToast: value => state.toasts.push(value),
      stopPullDownRefresh() {}, showModal() {} }
  })
  page.data = plain(page.data)
  page.setData = changes => Object.assign(page.data, changes)
  page.data.token = state.token
  return { page, state, api }
}
const messages = methods => load('miniprogram/pages/messages/messages.js', methods)
const click = id => ({ currentTarget: { dataset: { id } } })

test('schedule notices coexist with invitations and show event titles, time and unread count', async () => {
  const { page } = messages({ getMyInvitations: async () => [{ id: 8 }],
    getScheduleNotifications: async () => inbox(['proposed', 'confirmed', 'rejected', 'cancelled', 'window_changed'].map((kind, i) => notice(i + 1, { kind }))) })
  await page.loadMessages()
  assert.equal(page.data.invitations.length, 1)
  assert.equal(page.data.unreadSchedules, 5)
  assert.match(page.data.scheduleNotices[0].title, /请确认/)
  assert.match(page.data.scheduleNotices[1].title, /已确定/)
  assert.match(page.data.scheduleNotices[4].title, /原约定已作废/)
  assert.equal(page.data.scheduleNotices[0].timeText, '2036-09-12 19:00')
})

test('an inbox failure is displayed instead of masquerading as no messages', async () => {
  const { page } = messages({ getScheduleNotifications: async () => { throw { detail: '服务暂不可用' } },
    getMyInvitations: async () => [{ id: 8 }] })
  await page.loadMessages()
  assert.equal(page.data.loadError, true)
  assert.equal(page.data.noticeError, '服务暂不可用')
  assert.equal(page.data.invitations.length, 1)
})

test('read failure preserves the unread card and does not navigate', async () => {
  const { page, state } = messages({ readScheduleNotification: async () => { throw { detail: '稍后再试' } } })
  await page.loadMessages()
  await page.openScheduleNotice(click(1))
  assert.equal(page.data.unreadSchedules, 1)
  assert.equal(page.data.scheduleNotices[0].is_read, false)
  assert.equal(state.navigations.length, 0)
  assert.equal(state.toasts[0].title, '稍后再试')
})

test('opening a notice marks only that event read and deep-links the round without submitting a decision', async () => {
  const { page, state } = messages({ getScheduleNotifications: async () => inbox([notice(2), notice(1)]) })
  await page.loadMessages()
  await page.openScheduleNotice(click(2))
  assert.deepEqual(state.reads, [2])
  assert.equal(page.data.unreadSchedules, 1)
  assert.equal(page.data.scheduleNotices[1].is_read, false)
  assert.equal(state.navigations[0].url, '/pages/match/match?id=7&roundId=42')
})

test('duplicate taps cannot mark one event twice or double-decrement unread count', async () => {
  const pending = deferred()
  let requests = 0
  const { page } = messages({ readScheduleNotification: () => { requests++; return pending.promise } })
  await page.loadMessages()
  const first = page.openScheduleNotice(click(1))
  await page.openScheduleNotice(click(1))
  assert.equal(requests, 1)
  pending.resolve({})
  await first
  assert.equal(page.data.unreadSchedules, 0)
})

test('a removed round can be read but cannot open a broken link', async () => {
  const { page, state } = messages({ getScheduleNotifications: async () => inbox([notice(1, { round_available: false })]) })
  await page.loadMessages()
  await page.openScheduleNotice(click(1))
  assert.equal(state.navigations.length, 0)
  assert.equal(page.data.unreadSchedules, 0)
  assert.match(state.toasts[0].title, /已移除/)
})

test('a late old-account inbox response never appears after logout', async () => {
  const pending = deferred()
  const { page, state } = messages({ getScheduleNotifications: () => pending.promise })
  const loading = page.loadMessages()
  state.token = ''
  page.onShow()
  pending.resolve(inbox())
  await loading
  assert.equal(page.data.scheduleNotices.length, 0)
  assert.equal(page.data.unreadSchedules, 0)
  assert.equal(page.data.loading, false)
})

test('pagination keeps distinct notices and does not overwrite a newer refresh', async () => {
  const pending = deferred()
  let calls = 0
  const { page } = messages({ getScheduleNotifications: async cursor => {
    calls++
    if (cursor) return pending.promise
    return calls === 1 ? inbox([notice(5)], true) : inbox([notice(9)], false)
  } })
  await page.loadMessages()
  const older = page.loadMoreNotices()
  await page.loadMessages()
  pending.resolve(inbox([notice(4)]))
  await older
  assert.deepEqual(plain(page.data.scheduleNotices.map(n => n.id)), [9])
})

function matchPage(changes = {}, captain = true) {
  const round = { id: 42, match_id: 7, round_number: 1, group_name: '淘汰赛', team1_id: 100, team2_id: 200,
    status: 'pending', schedule_status: 'pending', scheduled_time: '2036-09-12T21:00:00',
    team1_confirmed: false, team2_confirmed: true, ...changes }
  return load('miniprogram/pages/match/match.js', {
    getMatchDetail: async () => ({ match: { id: 7, name: '测试赛事' }, teams: [], rounds: [round] }),
    getMyRegistration: async () => ({ registered: true, registration: { team_id: 100 } }),
    getMyTeam: async () => ({ id: 100, captain_id: captain ? 10 : 20 }),
    getMe: async () => ({ id: 10 })
  })
}

test('deep link waits for captain identity and opens the current proposal rather than old notice time', async () => {
  const { page } = matchPage()
  await page.onLoad({ id: '7', roundId: '42' })
  assert.equal(page.data.tab, 'knockout')
  assert.equal(page.data.showSchedule, true)
  assert.equal(page.data.scheduleTime, '21:00')
  assert.equal(page.data.scheduleRound.i_confirmed, false)
})

test('finished rounds and former captains do not receive an active scheduling panel', async () => {
  for (const [changes, captain] of [[{ status: 'finished' }, true], [{}, false]]) {
    const { page, state } = matchPage(changes, captain)
    await page.onLoad({ id: '7', roundId: '42' })
    assert.equal(page.data.showSchedule, false)
    assert.equal(state.toasts.length, 1)
  }
})

test('missing notification round shows an explanation', async () => {
  const { page, state } = matchPage()
  await page.onLoad({ id: '7', roundId: '999' })
  assert.equal(page.data.showSchedule, false)
  assert.match(state.toasts[0].title, /已移除/)
})

test('notification API uses authenticated cursor/read routes and explains an outdated server', async () => {
  const module = { exports: {} }, requests = []
  let response = { statusCode: 200, data: inbox() }
  vm.runInNewContext(fs.readFileSync(path.join(root, 'miniprogram/utils/api.js'), 'utf8'), {
    module, wx: { getStorageSync: () => 'my-token', request(options) { requests.push(options); options.success(response) } }
  })
  await module.exports.getScheduleNotifications(23)
  assert.match(requests[0].url, /\/api\/notifications\/schedules\?before_id=23$/)
  assert.equal(requests[0].header.Authorization, 'Bearer my-token')
  await module.exports.readScheduleNotification(6)
  assert.equal(requests[1].method, 'PUT')
  assert.match(requests[1].url, /\/schedules\/6\/read$/)
  response = { statusCode: 404, data: { detail: 'Not Found' } }
  await assert.rejects(module.exports.getScheduleNotifications(), error => error.code === 'BACKEND_UPGRADE_REQUIRED')
  response = { statusCode: 404, data: { detail: '通知不存在' } }
  await assert.rejects(module.exports.readScheduleNotification(6), error => error.detail === '通知不存在')
})
