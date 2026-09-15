const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm')
const { parseWxml, createRenderer } = require('../../tools/preview-miniprogram.js')
const root = path.resolve(__dirname, '../../miniprogram')
const plain = value => JSON.parse(JSON.stringify(value))
const user = id => ({ id, nickname: '选手' + id, game_id: 'Player' + id, is_verified: true })
const notice = id => ({ id, title: '赛事安排' + id, content: '请查看赛事详情，确认本轮时间。', is_read: false, created_at: '2026-09-15T18:00:00' })
const inbox = (items, more = false) => ({ items, unread_count: items.filter(n => !n.is_read).length, has_more: more, next_cursor: more ? items.at(-1).id : null })
const click = id => ({ currentTarget: { dataset: { id } } })
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no }); return { promise, resolve, reject } }

function load(relative, overrides = {}) {
  const state = { token: 'notice-admin-token', confirm: true, modals: [], sends: [], reads: [], toasts: [], navigations: [], scrolls: [] }
  const api = {
    getMe: async () => ({ id: 1, role: 'admin' }),
    getAdminNoticeRecipients: async () => ({ items: [user(2), user(3)], has_more: false, next_cursor: null }),
    getAdminNoticeHistory: async () => ({ items: [] }),
    sendAdminNotice: async body => { state.sends.push(plain(body)); return { id: 1, recipient_count: body.recipient_ids.length } },
    getAdminNotices: async () => inbox([notice(4), notice(3)]),
    readAdminNotice: async id => { state.reads.push(id) },
    getMyInvitations: async () => [], getMyTeamApplications: async () => [], getMyRankApplications: async () => [],
    getScheduleNotifications: async () => inbox([]), ...overrides
  }
  let page
  vm.runInNewContext(fs.readFileSync(path.join(root, relative + '.js'), 'utf8'), {
    Page: value => { page = value }, console,
    require: name => name.endsWith('/api.js') ? api : require(path.join(root, 'utils/rank.js')),
    wx: { getStorageSync: () => state.token, showModal: options => {
      state.modals.push(options)
      if (state.confirm !== null) options.success({ confirm: state.confirm })
    }, showToast: value => state.toasts.push(value), navigateTo: value => state.navigations.push(value.url),
    pageScrollTo: value => state.scrolls.push(value.selector), stopPullDownRefresh() {} }
  })
  page.data = plain(page.data)
  page.setData = (values, callback) => { Object.assign(page.data, values); if (callback) callback() }
  return { page, state, api }
}
async function editor(overrides) {
  const result = load('pages/admin/notices/notices', overrides)
  await result.page.initialize()
  return result
}
function compose(page) {
  page.toggleRecipient(click(2))
  page.onTitle({ detail: { value: ' 比赛安排 ' } })
  page.onContent({ detail: { value: ' 请确认本轮时间。 ' } })
}
function messages(overrides) {
  const result = load('pages/messages/messages', overrides)
  result.page.data.token = result.state.token
  return result
}

test('only administrator can load recipient picker, and failed permission exposes retry', async () => {
  for (const role of ['user', 'reviewer']) {
    let queried = false
    const { page } = await editor({ getMe: async () => ({ role }), getAdminNoticeRecipients: async () => { queried = true } })
    assert.equal(page.data.allowed, false)
    assert.match(page.data.error, /仅管理员/)
    assert.equal(queried, false)
    await page.sendNotice()
  }
})

test('selection survives search and old search results cannot replace the newest results', async () => {
  const { page, api } = await editor()
  page.toggleRecipient(click(2))
  const waiting = deferred()
  api.getAdminNoticeRecipients = query => query === 'old' ? waiting.promise : Promise.resolve({ items: [user(3)], has_more: false })
  page.data.keyword = 'old'
  const old = page.search()
  page.data.keyword = 'new'
  await page.search()
  page.toggleRecipient(click(3))
  waiting.resolve({ items: [user(99)], has_more: false })
  await old
  assert.deepEqual(plain(page.data.users.map(u => u.id)), [3])
  assert.deepEqual(plain(page.data.selected.map(u => u.id)), [2, 3])
  page.toggleRecipient(click(2))
  assert.equal(page.data.selectedCount, 1)
})

test('pagination preserves the submitted query, avoids duplicates and caps explicit selection', async () => {
  const calls = []
  const { page, api, state } = await editor()
  api.getAdminNoticeRecipients = async (query, cursor) => {
    calls.push([query, cursor]); return cursor ? { items: [user(2), user(1)], has_more: false } : { items: [user(2)], has_more: true, next_cursor: 2 }
  }
  page.data.keyword = 'name'
  await page.search()
  page.data.keyword = 'unsubmitted'
  await page.loadMore()
  assert.deepEqual(calls, [['name', null], ['name', 2]])
  assert.deepEqual(plain(page.data.users.map(u => u.id)), [2, 1])
  page.data.selected = Array.from({ length: 100 }, (_, i) => user(i + 100))
  page.toggleRecipient(click(1))
  assert.equal(page.data.selected.length, 100)
  assert.match(state.toasts[0].title, /100/)
})

test('missing fields and cancelling the concrete send preview never send', async () => {
  const { page, state } = await editor()
  await page.sendNotice()
  assert.match(page.data.sendError, /请选择/)
  compose(page)
  state.confirm = false
  await page.sendNotice()
  assert.equal(state.sends.length, 0)
  assert.equal(page.data.selectedCount, 1)
  assert.match(state.modals[0].content, /选手2（ID 2）/)
  assert.match(state.modals[0].content, /比赛安排/)
  assert.equal(page.data.sending, false)
})

test('double-click sends once and timeout retry reuses the same request id', async () => {
  const { page, state, api } = await editor()
  compose(page)
  const waiting = deferred(), requests = []
  api.sendAdminNotice = body => { requests.push(plain(body)); return waiting.promise }
  const first = page.sendNotice()
  await page.sendNotice()
  await Promise.resolve()
  assert.equal(requests.length, 1)
  waiting.reject({ detail: '网络超时' })
  await first
  assert.equal(page.data.selectedCount, 1)
  api.sendAdminNotice = async body => { requests.push(plain(body)); return { recipient_count: 1 } }
  await page.sendNotice()
  assert.equal(requests[0].request_id, requests[1].request_id)
  assert.equal(requests[0].title, '比赛安排')
  assert.deepEqual(requests[0].recipient_ids, [2])
  assert.match(page.data.sentMessage, /1 位/)
  assert.equal(page.data.selectedCount, 0)
  assert.equal(state.modals.length, 2)
})

test('changed content after failed send gets a different request id and history failure preserves success', async () => {
  const requests = []
  const { page, api } = await editor({ sendAdminNotice: async body => { requests.push(plain(body)); throw { detail: '失败' } } })
  compose(page)
  await page.sendNotice()
  page.onContent({ detail: { value: '更新后的时间' } })
  api.sendAdminNotice = async body => { requests.push(plain(body)); return { recipient_count: 1 } }
  api.getAdminNoticeHistory = async () => { throw { detail: '记录暂不可用' } }
  await page.sendNotice()
  assert.notEqual(requests[0].request_id, requests[1].request_id)
  assert.match(page.data.sentMessage, /已送入/)
  assert.equal(page.data.sendError, '')
  assert.match(page.data.historyError, /记录/)
})

test('logout or unload during send confirmation prevents transmission', async () => {
  for (const action of ['logout', 'unload']) {
    const { page, state } = await editor()
    compose(page)
    state.confirm = null
    const pending = page.sendNotice()
    if (action === 'logout') state.token = ''
    else page.onUnload()
    state.modals[0].success({ confirm: true })
    await pending
    assert.equal(state.sends.length, 0)
  }
})

test('read-only list does not mark notices read; opening and confirming marks just that notice once', async () => {
  const { page, state } = messages()
  page.onLoad({ section: 'admin' })
  await page.loadAdminNotices()
  assert.equal(page.data.adminUnread, 2)
  assert.deepEqual(state.reads, [])
  assert.deepEqual(state.scrolls, ['#notice-admin'])
  await page.openAdminNotice(click(4))
  await page.openAdminNotice(click(4))
  assert.deepEqual(state.reads, [4])
  assert.equal(page.data.adminUnread, 1)
  assert.equal(page.data.adminNotices[1].is_read, false)
  assert.match(state.modals[0].content, /确认本轮时间/)
})

test('failed read keeps the badge and retry succeeds without changing other notifications', async () => {
  const { page, api, state } = messages({ readAdminNotice: async () => { throw { detail: '保存失败' } } })
  await page.loadAdminNotices()
  await page.openAdminNotice(click(4))
  assert.equal(page.data.adminUnread, 2)
  assert.equal(page.data.adminNotices[0].is_read, false)
  assert.match(state.toasts[0].title, /保存失败/)
  api.readAdminNotice = async id => state.reads.push(id)
  await page.openAdminNotice(click(4))
  assert.equal(page.data.adminUnread, 1)
})

test('account changes clear inbox and late reads or loads cannot leak the previous inbox', async () => {
  const { page, api, state } = messages()
  await page.loadAdminNotices()
  state.token = 'another-account'
  await page.openAdminNotice(click(4))
  assert.equal(state.modals.length, 0)
  const waiting = deferred()
  api.getAdminNotices = () => waiting.promise
  page.data.token = state.token
  const pending = page.loadAdminNotices()
  assert.equal(page.data.adminNotices.length, 0)
  page.onUnload()
  waiting.resolve(inbox([notice(99)]))
  await pending
  assert.equal(page.data.adminNotices.length, 0)
})

test('inbox pagination deduplicates and failure preserves the current list for retry', async () => {
  const { page, api } = messages({ getAdminNotices: async () => inbox([notice(4)], true) })
  await page.loadAdminNotices()
  api.getAdminNotices = async () => { throw { detail: '网络失败' } }
  await page.loadMoreAdminNotices()
  assert.equal(page.data.adminNotices.length, 1)
  assert.match(page.data.adminError, /网络失败/)
  api.getAdminNotices = async cursor => { assert.equal(cursor, 4); return inbox([notice(4), notice(3)]) }
  await page.loadMoreAdminNotices()
  assert.deepEqual(plain(page.data.adminNotices.map(n => n.id)), [4, 3])
})

test('home displays targeted notice count and title and opens the correct inbox section', async () => {
  const { page, state } = load('pages/index/index', { getPersonalNoticeSummary: async () => ({ admin_notice_count: 2, admin_notice_title: '请确认赛程', total: 2 }) })
  await page.loadPersonalNotices()
  const tree = parseWxml(fs.readFileSync(path.join(root, 'pages/index/index.wxml'), 'utf8'))
  tree.scope = page.data
  const html = createRenderer({})(tree)
  assert.match(html, /管理员通知/)
  assert.match(html, /请确认赛程/)
  page.goNotice({ currentTarget: { dataset: { section: 'admin' } } })
  assert.deepEqual(state.navigations, ['/pages/messages/messages?section=admin'])
  state.token = ''
  await page.loadPersonalNotices()
  assert.equal(page.data.personalNotice, null)
})

test('workbench notice entry is administrator-only and editor renders selected people and fields', async () => {
  const { page, state } = load('pages/admin/admin')
  page.data.user = { role: 'reviewer' }
  page.openNotices()
  assert.equal(state.navigations.length, 0)
  page.data.user = { role: 'admin' }
  page.openNotices()
  assert.deepEqual(state.navigations, ['/pages/admin/notices/notices'])
  const { page: form } = await editor()
  compose(form)
  const tree = parseWxml(fs.readFileSync(path.join(root, 'pages/admin/notices/notices.wxml'), 'utf8'))
  tree.scope = form.data
  const html = createRenderer({})(tree)
  assert.match(html, /选手2 · ID 2 ×/)
  assert.match(html, /标题/)
  assert.match(html, /预览并发送/)
  assert.doesNotMatch(html, /学号|QQ/)
})
