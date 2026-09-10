const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const test = require('node:test')
const { parseWxml, createRenderer } = require('../../tools/preview-miniprogram.js')

const root = path.resolve(__dirname, '../..')
const pageFile = path.join(root, 'miniprogram/pages/hall/hall.js')
const template = fs.readFileSync(pageFile.replace(/\.js$/, '.wxml'), 'utf8')
const clone = value => JSON.parse(JSON.stringify(value))
const event = values => ({ currentTarget: { dataset: values } })
const player = (id, rank = 'S25', position = 1) => ({ user_id: id, nickname: '选手' + id, avatar: '/uploads/u' + id + '.png', rank, position, individual_rating: 1500 - id })
const champion = (id = 8) => ({ match_id: id, match_name: '秋季赛事' + id, match_type: 'freshman', event_date: '2026-09-09', awarded_at: '2026-09-09T20:00:00', champion_team_id: 30, champion_name: '林间回响', champion_logo: '/uploads/champion.png', runner_up_name: '白桦竞技', champion_score: 2, runner_up_score: 0, roster: [player(1), player(2, 'A+')], snapshot_source: 'final' })
const response = (items, has_more = false, total = items.length, offset = 0) => ({ items, has_more, total, offset, limit: 20 })
function deferred() {
  let resolve, reject
  const promise = new Promise((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}

function loadPage(methods = {}) {
  let page
  const calls = { navigations: [], refreshes: 0 }
  const api = new Proxy({ BASE: 'https://example.test', ...methods }, { get(target, key) {
    if (key in target) return target[key]
    throw new Error('Unexpected API: ' + String(key))
  } })
  vm.runInNewContext(fs.readFileSync(pageFile, 'utf8'), {
    Page: value => { page = value },
    require(name) {
      if (name.endsWith('/api.js')) return api
      if (name.endsWith('/rank.js')) return require(path.resolve(path.dirname(pageFile), name))
      throw new Error('Unexpected module: ' + name)
    },
    wx: { navigateTo: value => calls.navigations.push(value), stopPullDownRefresh: () => { calls.refreshes++ } }, console
  }, { filename: pageFile })
  page.data = clone(page.data)
  page.setData = values => Object.assign(page.data, clone(values))
  return { page, calls }
}

function render(page) {
  const tree = parseWxml(template)
  tree.scope = page.data
  return createRenderer({})(tree)
}

test('public hall shares the homepage shortcuts and drawer, preserving every existing destination', () => {
  const config = JSON.parse(fs.readFileSync(path.join(root, 'miniprogram/app.json'), 'utf8'))
  assert.ok(config.pages.includes('pages/hall/hall'))
  let home, drawer
  const calls = []
  const wx = { navigateTo: value => calls.push(value) }
  vm.runInNewContext(fs.readFileSync(path.join(root, 'miniprogram/pages/index/index.js'), 'utf8'), { Page: value => { home = value }, require: () => ({}), wx })
  assert.deepEqual(Array.from(home.data.shortcuts, item => item.url), [
    '/pages/team/team', '/pages/teams/teams', '/pages/hall/hall', '/pages/messages/messages', '/pages/verify/verify'
  ])
  const shortcut = home.data.shortcuts.find(item => item.title === '名人堂')
  home.goShortcut(event({ url: shortcut.url }))
  assert.ok(fs.existsSync(path.join(root, 'miniprogram/images/brand', shortcut.icon + '.svg')))
  vm.runInNewContext(fs.readFileSync(path.join(root, 'miniprogram/components/nav-drawer/nav-drawer.js'), 'utf8'), { Component: value => { drawer = value }, require: () => ({}), wx })
  const entry = drawer.data.navigation.find(item => item.title === '名人堂')
  assert.equal(entry.url, '/pages/hall/hall')
  drawer.methods.go.call({ setData() {} }, event({ url: entry.url }))
  assert.equal(calls.length, 2)
  assert.ok(calls.every(value => value.url === '/pages/hall/hall'))
  const homeTree = parseWxml(fs.readFileSync(path.join(root, 'miniprogram/pages/index/index.wxml'), 'utf8'))
  homeTree.scope = home.data
  const html = createRenderer({})(homeTree)
  assert.match(html, /名人堂/)
  assert.equal((html.match(/class="shortcut"/g) || []).length, 5)
  assert.doesNotMatch(html, /hall-entry/)
})

test('hall APIs use explicit independent read-only pagination endpoints without login', async () => {
  const requests = []
  const exported = { exports: {} }
  vm.runInNewContext(fs.readFileSync(path.join(root, 'miniprogram/utils/api.js'), 'utf8'), { module: exported, wx: {
    getStorageSync: () => '', request(options) { requests.push(options); options.success({ statusCode: 200, data: response([]) }) }
  } })
  await exported.exports.getHallChampions(20, 20)
  await exported.exports.getHallPlayers(50, 50)
  assert.ok(requests[0].url.endsWith('/api/hall/champions?offset=20&limit=20'))
  assert.ok(requests[1].url.endsWith('/api/hall/players?offset=50&limit=50'))
  assert.ok(requests.every(value => value.method === 'GET' && value.header.Authorization === ''))
})

test('missing hall routes explain the server upgrade and never fall back to unrelated APIs', async () => {
  const requests = [], exported = { exports: {} }
  vm.runInNewContext(fs.readFileSync(path.join(root, 'miniprogram/utils/api.js'), 'utf8'), { module: exported, wx: {
    getStorageSync: () => '', request(options) { requests.push(options); options.success({ statusCode: 404, data: { detail: 'Not Found' } }) }
  } })
  for (const method of ['getHallChampions', 'getHallPlayers']) {
    await assert.rejects(exported.exports[method](), err => err.statusCode === 404 && err.code === 'BACKEND_UPGRADE_REQUIRED' && err.detail === '当前服务器版本不支持名人堂，请更新后端后重试')
  }
  assert.equal(requests.length, 2)
})

test('hall API business errors remain unchanged and are not mistaken for missing routes', async () => {
  const exported = { exports: {} }
  vm.runInNewContext(fs.readFileSync(path.join(root, 'miniprogram/utils/api.js'), 'utf8'), { module: exported, wx: {
    getStorageSync: () => '', request(options) { options.success({ statusCode: 404, data: { detail: '该冠军档案已不存在' } }) }
  } })
  await assert.rejects(exported.exports.getHallChampions(), err => err.detail === '该冠军档案已不存在' && err.code === undefined)
})

test('champions render actual event, winner, zero score and expandable certified roster', async () => {
  const { page, calls } = loadPage({ getHallChampions: async () => response([champion()]) })
  await page.onLoad()
  let html = render(page)
  assert.match(html, /林间回响/)
  assert.match(html, /秋季赛事8/)
  assert.match(html, /白桦竞技/)
  assert.match(html, /2026.09.09/)
  assert.equal(page.data.champions.items[0].hasScore, true)
  assert.doesNotMatch(html, /选手1/)
  page.toggleRoster(event({ id: '8' }))
  html = render(page)
  assert.match(html, /选手1/)
  assert.match(html, /钻S25星/)
  assert.match(html, /A\+/)
  assert.equal(page.data.champions.items[0].roster[0].badge.icon, '/images/ranks/s-diamond.svg')
  assert.match(html, /class="member-badge" src="data:image\/svg\+xml;base64,/)
  assert.doesNotMatch(html, /历史阵容为归档时记录/)
  assert.equal(page.data.champions.items[0].logoFull, 'https://example.test/uploads/champion.png')
  page.goMatch(event({ id: '8' }))
  assert.equal(calls.navigations[0].url, '/pages/match/match?id=8')
  page.toggleRoster(event({ id: 8 }))
  assert.doesNotMatch(render(page), /选手1/)
})

test('backfilled champions identify archival roster and missing scores honestly', async () => {
  const item = { ...champion(), snapshot_source: 'backfill', roster: [], champion_score: null, runner_up_score: null }
  const { page } = loadPage({ getHallChampions: async () => response([item]) })
  await page.onLoad()
  page.toggleRoster(event({ id: 8 }))
  const html = render(page)
  assert.match(html, /历史阵容为归档时记录/)
  assert.match(html, /本届夺冠阵容暂无记录/)
  assert.match(html, /比分未归档/)
})

test('archived champions survive deleted events without a broken details action', async () => {
  const { page, calls } = loadPage({ getHallChampions: async () => response([{ ...champion(), match_available: false }]) })
  await page.onLoad()
  assert.match(render(page), /林间回响/)
  assert.doesNotMatch(render(page), /赛事详情/)
  page.goMatch(event({ id: 8 }))
  assert.equal(calls.navigations.length, 0)
  page.toggleRoster(event({ id: 8 }))
  assert.match(render(page), /选手1/)
})

test('players preserve server positions and show tied ranks instead of a fabricated podium', async () => {
  const players = [player(2, 'S50', 1), { ...player(1, 'S50', 1), individual_rating: 9999 }, player(3, 'S10', 3), player(4, 'A+', 4), player(5, 'C+', 5)]
  const { page } = loadPage({ getHallPlayers: async () => response(players) })
  await page.onLoad({ tab: 'players' })
  assert.deepEqual(page.data.players.items.map(item => item.user_id), [2, 1, 3, 4, 5])
  assert.deepEqual(page.data.players.items.map(item => item.position), [1, 1, 3, 4, 5])
  assert.ok(page.data.players.items[0].tied && page.data.players.items[1].tied)
  assert.equal(page.data.highestPlayer.nickname, '选手2')
  const html = render(page)
  assert.match(html, /当前平台最高段位/)
  assert.match(html, /并列/)
  assert.match(html, /魔王S50星/)
  assert.match(html, /金S10星/)
  for (const key of ['s-demon', 's-gold', 'a-plus', 'c-plus']) assert.ok(page.data.players.items.some(item => item.badge.icon === '/images/ranks/' + key + '.svg'))
  assert.doesNotMatch(html, /9999/)
})

test('empty and failed boards are distinct and one board failure cannot hide the other', async () => {
  const { page } = loadPage({ getHallChampions: async () => { throw { detail: '请求超时' } }, getHallPlayers: async () => response([player(1)]) })
  await page.onLoad()
  assert.match(render(page), /冠军档案暂时未能加载/)
  assert.doesNotMatch(render(page), /第一份冠军荣耀/)
  await page.switchTab(event({ tab: 'players' }))
  assert.match(render(page), /选手1/)
  assert.doesNotMatch(render(page), /请求超时/)
  await page.switchTab(event({ tab: 'champions' }))
  assert.match(render(page), /请求超时/)
  const empty = loadPage({ getHallChampions: async () => response([]), getHallPlayers: async () => response([]) }).page
  await empty.onLoad()
  assert.match(render(empty), /第一份冠军荣耀/)
  await empty.switchTab(event({ tab: 'players' }))
  assert.match(render(empty), /巅峰之路/)
})

test('switching tabs reuses completed and in-flight requests', async () => {
  const pending = deferred()
  let championsCalls = 0, playerCalls = 0
  const { page } = loadPage({ getHallChampions: async () => { championsCalls++; return response([champion()]) }, getHallPlayers: () => { playerCalls++; return pending.promise } })
  await page.onLoad()
  const first = page.switchTab(event({ tab: 'players' }))
  page.switchTab(event({ tab: 'champions' }))
  page.switchTab(event({ tab: 'players' }))
  assert.equal(playerCalls, 1)
  pending.resolve(response([player(1)]))
  await first
  await page.switchTab(event({ tab: 'champions' }))
  await page.switchTab(event({ tab: 'players' }))
  assert.equal(playerCalls, 1)
  assert.equal(championsCalls, 1)
})

test('pagination uses raw page offsets, deduplicates IDs and coalesces repeated loads', async () => {
  const next = deferred(), offsets = []
  const { page } = loadPage({ getHallChampions: (offset, limit) => {
    offsets.push([offset, limit])
    return offset === 0 ? Promise.resolve(response([champion(1), champion(2)], true, 4)) : next.promise
  } })
  await page.onLoad()
  const loading = page.loadMore()
  const again = page.loadMore()
  assert.equal(again, loading)
  next.resolve(response([champion(2), champion(3)], false, 4, 2))
  await loading
  assert.deepEqual(offsets, [[0, 20], [2, 20]])
  assert.deepEqual(page.data.champions.items.map(item => item.match_id), [1, 2, 3])
  assert.equal(page.data.champions.nextOffset, 4)
  await page.loadMore()
  assert.equal(offsets.length, 2)
})

test('ties spanning pages are updated without changing either rank', async () => {
  const { page } = loadPage({ getHallPlayers: async offset => offset ? response([player(2, 'S25', 1)], false, 2, 1) : response([player(1, 'S25', 1)], true, 2) })
  await page.onLoad({ tab: 'players' })
  assert.equal(page.data.players.items[0].tied, false)
  await page.loadMore()
  assert.ok(page.data.players.items.every(item => item.tied && item.position === 1))
  assert.equal(page.data.highestPlayer.tied, true)
})

test('failed pagination keeps rows and retries the same offset, not the first page', async () => {
  const offsets = []
  let fail = true
  const { page } = loadPage({ getHallPlayers: async offset => {
    offsets.push(offset)
    if (!offset) return response([player(1)], true, 2)
    if (fail) throw { detail: '连接中断' }
    return response([player(2, 'A+', 2)], false, 2, offset)
  } })
  await page.onLoad({ tab: 'players' })
  await page.loadMore()
  assert.equal(page.data.players.items.length, 1)
  assert.match(render(page), /连接中断/)
  fail = false
  await page.retry()
  assert.deepEqual(offsets, [0, 1, 1])
  assert.equal(page.data.players.items.length, 2)
  assert.equal(page.data.players.error, '')
})

test('refresh replaces pages and late pagination responses cannot append stale rows', async () => {
  const oldPage = deferred(), refresh = deferred()
  let zeroCalls = 0
  const { page, calls } = loadPage({ getHallChampions: offset => offset ? oldPage.promise : (++zeroCalls === 1 ? Promise.resolve(response([champion(1)], true, 2)) : refresh.promise) })
  await page.onLoad()
  const more = page.loadMore()
  const refreshing = page.onPullDownRefresh()
  refresh.resolve(response([champion(10)]))
  await refreshing
  oldPage.resolve(response([champion(2)]))
  await more
  assert.deepEqual(page.data.champions.items.map(item => item.match_id), [10])
  assert.equal(page.data.champions.nextOffset, 1)
  assert.equal(calls.refreshes, 1)
})

test('latest refresh wins over a late initial request and stale error cannot erase success', async () => {
  const old = deferred(), fresh = deferred()
  let count = 0
  const { page } = loadPage({ getHallPlayers: () => (++count === 1 ? old.promise : fresh.promise) })
  const loading = page.onLoad({ tab: 'players' })
  const refreshing = page.onPullDownRefresh()
  fresh.resolve(response([player(2)]))
  await refreshing
  old.reject({ detail: '旧请求失败' })
  await loading
  assert.equal(page.data.players.error, '')
  assert.equal(page.data.players.loading, false)
  assert.equal(page.data.players.items[0].user_id, 2)
})

test('failed refresh retains the previous board and explicit retry restarts at zero', async () => {
  const offsets = []
  let requestCount = 0
  const { page } = loadPage({ getHallPlayers: async offset => {
    offsets.push(offset)
    if (++requestCount === 2) throw { detail: '刷新失败' }
    return response([player(requestCount)])
  } })
  await page.onLoad({ tab: 'players' })
  await page.onPullDownRefresh()
  assert.equal(page.data.players.items[0].user_id, 1)
  await page.retry()
  assert.deepEqual(offsets, [0, 0, 0])
  assert.equal(page.data.players.items[0].user_id, 3)
})

test('unloading stops pending reads from mutating a detached page', async () => {
  const pending = deferred()
  const { page } = loadPage({ getHallChampions: () => pending.promise })
  const loading = page.onLoad()
  page.onUnload()
  page.setData = () => { throw new Error('Detached page must not receive setData') }
  pending.resolve(response([champion()]))
  await loading
})

test('absolute images remain absolute, relative paths resolve and missing avatars use initials', async () => {
  const { page } = loadPage({ getHallPlayers: async () => response([
    { ...player(1), avatar: 'https://cdn.example.test/face.png' },
    { ...player(2), avatar: 'uploads/two.png' },
    { ...player(3), nickname: '', avatar: '' }
  ]) })
  await page.onLoad({ tab: 'players' })
  assert.equal(page.data.players.items[0].avatarFull, 'https://cdn.example.test/face.png')
  assert.equal(page.data.players.items[1].avatarFull, 'https://example.test/uploads/two.png')
  assert.equal(page.data.players.items[2].avatarFull, '')
  assert.equal(page.data.players.items[2].initial, '北')
  assert.match(render(page), /北林选手/)
})
