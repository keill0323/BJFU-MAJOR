// New UI state transitions, executed against the real pages with no network.
const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const root = path.resolve(__dirname, '../..')

function load(relative, api, token = '') {
  let definition
  const events = []
  const storage = { token }
  const file = path.join(root, 'miniprogram', relative)
  vm.runInNewContext(fs.readFileSync(file, 'utf8'), {
    Page: d => { definition = d }, Component: d => { definition = d },
    require: p => p.endsWith('api.js') ? api : require(path.resolve(path.dirname(file), p)),
    wx: { getStorageSync: k => storage[k], getWindowInfo: () => ({ statusBarHeight: 24, windowWidth: 390 }),
      getMenuButtonBoundingClientRect: () => ({ top: 30, left: 290, width: 88, height: 32 }) },
    getCurrentPages: () => [{ route: 'pages/index/index' }], console
  }, { filename: file })
  const page = Object.assign({}, definition, definition.methods)
  page.data = JSON.parse(JSON.stringify(definition.data))
  page.setData = value => Object.assign(page.data, value)
  page.triggerEvent = (name, detail) => events.push({ name, detail })
  return { page, storage, events }
}

test('home status filters and clear-search preserve real counts without extra filter requests', async () => {
  let requests = 0
  const { page } = load('pages/index/index.js', { getMatches: async () => {
    requests++
    return [{ id: 1, status: 'registering', registered_count: 6, max_teams: 16, register_end: '2026-09-23T18:00:00' },
      { id: 2, status: 'in_progress' }, { id: 3, status: 'finished' }]
  } })
  await page.loadMatches()
  assert.equal(page.data.total, 3)
  assert.equal(page.data.matches[0].progress, 38)
  assert.equal(page.data.matches[0].timing, '报名截止 09.23 18:00')
  page.setFilter({ currentTarget: { dataset: { value: 'finished' } } })
  assert.equal(page.data.groups.length, 1)
  assert.equal(page.data.groups[0].matches[0].id, 3)
  assert.equal(page.data.total, 3)
  assert.equal(requests, 1)
  page.data.keyword = '不存在'
  await page.clearSearch()
  assert.equal(page.data.activeFilter, 'all')
  assert.equal(page.data.keyword, '')
  assert.equal(page.data.groups.length, 3)
})

test('late home search responses cannot overwrite newer results or hide their loading state', async () => {
  const pending = {}
  const { page } = load('pages/index/index.js', { searchMatches: k => new Promise(resolve => { pending[k] = resolve }) })
  page.data.keyword = '旧'
  const oldRequest = page.loadMatches()
  page.data.keyword = '新'
  const newRequest = page.loadMatches()
  pending['新']([{ id: 2, status: 'registering' }])
  await newRequest
  pending['旧']([{ id: 1, status: 'finished' }])
  await oldRequest
  assert.equal(page.data.matches[0].id, 2)
  assert.equal(page.data.loading, false)
})

test('home distinguishes a failed request from a successful empty list and supports retry', async () => {
  let fail = true
  const { page } = load('pages/index/index.js', { getMatches: async () => { if (fail) throw Error('offline'); return [] } })
  await page.loadMatches()
  assert.equal(page.data.loadError, true)
  assert.equal(page.data.loading, false)
  fail = false
  await page.loadMatches()
  assert.equal(page.data.loadError, false)
  assert.equal(page.data.groups.length, 0)
})

test('navigation reserves capsule space and shares the in-flight profile read with home', async () => {
  let resolveUser, requests = 0
  const { page, events, storage } = load('components/nav-drawer/nav-drawer.js', {
    BASE: 'https://example.invalid', getMe: () => { requests++; return new Promise(r => { resolveUser = r }) }
  }, 'local-token')
  page.lifetimes.attached.call(page)
  const work = page.loadUser()
  assert.equal(requests, 1)
  assert.equal(page.data.navHeight, 44)
  assert.equal(page.data.capsuleSpace, 108)
  resolveUser({ id: 1, role: 'captain', rank: 'S32', avatar: 'https://example.invalid/avatar.png' })
  await work
  assert.equal(page.data.user.avatar_full, 'https://example.invalid/avatar.png')
  assert.equal(page.data.user.display_rank, '钻S32星')
  assert.equal(page.data.isManager, false)
  assert.equal(events.length, 1)
  storage.token = ''
  await page.loadUser()
  assert.equal(page.data.user, null)
  assert.equal(events[1].detail.user, null)
})

test('navigation ignores a profile response from a logged-out session', async () => {
  let resolveUser
  const { page, storage, events } = load('components/nav-drawer/nav-drawer.js', {
    getMe: () => new Promise(r => { resolveUser = r })
  }, 'local-token')
  const work = page.loadUser()
  storage.token = ''
  await page.loadUser()
  resolveUser({ id: 1, role: 'admin' })
  await work
  assert.equal(page.data.user, null)
  assert.equal(page.data.isManager, false)
  assert.equal(events.length, 1)
})

test('message guest view makes no authenticated requests and partial failures remain visible', async () => {
  let calls = 0
  const { page, storage } = load('pages/messages/messages.js', {
    getMyTeamApplications: async () => { calls++; return [] },
    getMyInvitations: async () => { calls++; return [{ id: 2 }] },
    getMyRankApplications: async () => { calls++; throw Error('offline') },
    getScheduleNotifications: async () => { calls++; return { items: [], unread_count: 0, next_cursor: null, has_more: false } }
  })
  await page.loadMessages()
  assert.equal(calls, 0)
  page.data.token = 'local-token'
  storage.token = 'local-token'
  await page.loadMessages()
  assert.equal(calls, 4)
  assert.equal(page.data.loadError, true)
  assert.equal(page.data.loading, false)
  assert.equal(page.data.invitations[0].id, 2)
})
