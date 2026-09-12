const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')

const root = path.resolve(__dirname, '../..')
const plain = value => JSON.parse(JSON.stringify(value))
const deferred = () => {
  let resolve, reject
  const promise = new Promise((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}
const result = (ids, filters = { rank: '', identity: '' }, more = false) => ({
  items: ids.map(id => ({ id, nickname: '选手' + id, rank: 'A+', is_verified: true, identity: 'senior' })),
  filters, has_more: more, next_cursor: more ? ids[ids.length - 1] : null
})

function load(overrides = {}) {
  const state = { token: 'token', calls: [], postsReads: 0, accountReads: 0, toasts: [] }
  const api = {
    BASE: '',
    getRecruitmentPlayers: async (_cursor, _query, filters) => result([80], filters, true),
    getRecruitmentPosts: async () => { state.postsReads++; return { items: [{ team_id: 2, team_name: '白桦' }], has_more: false } },
    getMyTeam: async () => { state.accountReads++; return { id: 2 } },
    getMyRecruitment: async () => { state.accountReads++; return { team_id: 2, content: '已发布内容' } },
    ...overrides
  }
  const readPlayers = api.getRecruitmentPlayers
  api.getRecruitmentPlayers = (...args) => {
    state.calls.push(plain(args))
    return readPlayers(...args)
  }
  let page
  vm.runInNewContext(fs.readFileSync(path.join(root, 'miniprogram/pages/recruitment/recruitment.js'), 'utf8'), {
    Page: definition => { page = definition }, console,
    require: name => name.endsWith('/rank.js') ? require(path.join(root, 'miniprogram/utils/rank.js')) : api,
    wx: { getStorageSync: () => state.token, showToast: value => state.toasts.push(value), stopPullDownRefresh() {} }
  })
  page.data = plain(page.data)
  page.setData = (changes, callback) => { Object.assign(page.data, changes); if (callback) callback() }
  return { page, state, api }
}

function choose(page, type, value) {
  const options = page.data[type + 'Options']
  const index = options.findIndex(option => option.value === value)
  assert.notEqual(index, -1, type + ' option must exist: ' + value)
  return page[type === 'rank' ? 'onRankFilter' : 'onIdentityFilter']({ detail: { value: String(index) } })
}

test('recruitment API encodes plus ranks and combines identity, keyword and cursor without changing values', async () => {
  const requests = []
  const module = { exports: {} }
  vm.runInNewContext(fs.readFileSync(path.join(root, 'miniprogram/utils/api.js'), 'utf8'), {
    module, console, wx: { getStorageSync: () => '', request: options => {
      requests.push(options)
      options.success({ statusCode: 200, data: result([]) })
    } }
  })
  for (const rank of ['C+', 'A++', 's_gold']) {
    await module.exports.getRecruitmentPlayers(75, '小白 & A+', { rank, identity: 'new_student' })
    const request = requests[requests.length - 1]
    const url = new URL(request.url)
    assert.equal(request.method, 'GET')
    assert.equal(url.pathname, '/api/recruitment/players')
    assert.equal(url.searchParams.get('rank'), rank)
    assert.equal(url.searchParams.get('identity'), 'new_student')
    assert.equal(url.searchParams.get('keyword'), '小白 & A+')
    assert.equal(url.searchParams.get('before_id'), '75')
  }
  await module.exports.getRecruitmentPlayers(null, '')
  const unfiltered = new URL(requests[requests.length - 1].url)
  assert.ok(!unfiltered.searchParams.get('rank'))
  assert.ok(!unfiltered.searchParams.get('identity'))
  assert.ok(!unfiltered.searchParams.has('before_id'))
})

test('unfiltered browsing stays compatible with the previous directory response', async () => {
  const { page } = load({ getRecruitmentPlayers: async () => ({ items: [{ id: 9 }], has_more: false }) })
  await page.load()
  assert.equal(page.data.playersFailed, false)
  assert.equal(page.data.players[0].id, 9)
  for (const rank of ['D', 'C', 'C+', 'C++', 'B', 'B+', 'B++', 'A', 'A+', 'A++', 's', 's_gold', 's_diamond', 's_demon', 'unranked']) {
    assert.ok(page.data.rankOptions.some(option => option.value === rank), rank)
  }
  for (const identity of ['new_student', 'senior', 'unknown']) {
    assert.ok(page.data.identityOptions.some(option => option.value === identity), identity)
  }
})

test('search, rank and identity are combined for the first page and every later page', async () => {
  const { page, state } = load({ getRecruitmentPlayers: async (cursor, _query, filters) => result(cursor ? [79] : [80], filters, !cursor) })
  await page.load()
  await choose(page, 'rank', 'C+')
  await choose(page, 'identity', 'new_student')
  page.onKeyword({ detail: { value: '  辅助玩家  ' } })
  await page.searchPlayers()
  assert.deepEqual(state.calls[state.calls.length - 1], [null, '辅助玩家', { rank: 'C+', identity: 'new_student' }])
  await page.loadMorePlayers()
  assert.deepEqual(state.calls[state.calls.length - 1], [80, '辅助玩家', { rank: 'C+', identity: 'new_student' }])
  assert.deepEqual(plain(page.data.players.map(player => player.id)), [80, 79])
})

test('changing a filter clears the old cursor immediately and preserves recruitment editing and account data', async () => {
  const pending = deferred()
  const { page, state, api } = load()
  await page.load()
  page.setData({ editing: true, draft: '尚未保存的招募内容' })
  const posts = plain(page.data.posts), mine = plain(page.data.mine), myTeam = plain(page.data.myTeam)
  const postsReads = state.postsReads, accountReads = state.accountReads
  api.getRecruitmentPlayers = (...args) => { state.calls.push(plain(args)); return pending.promise }
  const changing = choose(page, 'rank', 'A++')
  assert.deepEqual(state.calls[state.calls.length - 1], [null, '', { rank: 'A++', identity: '' }])
  assert.equal(page.data.playersCursor, null)
  assert.equal(page.data.playersMore, false)
  assert.equal(page.data.players.length, 0)
  assert.equal(page.data.editing, true)
  assert.equal(page.data.draft, '尚未保存的招募内容')
  assert.deepEqual(plain(page.data.posts), posts)
  assert.deepEqual(plain(page.data.mine), mine)
  assert.deepEqual(plain(page.data.myTeam), myTeam)
  assert.equal(state.postsReads, postsReads)
  assert.equal(state.accountReads, accountReads)
  pending.resolve(result([7], { rank: 'A++', identity: '' }))
  await changing
  assert.equal(page.data.players[0].id, 7)
})

test('a slow previous rank response cannot replace a newer rank selection', async () => {
  const first = deferred(), second = deferred()
  const { page, api } = load()
  await page.load()
  api.getRecruitmentPlayers = (_cursor, _query, filters) => filters.rank === 'A' ? first.promise : second.promise
  const choosingA = choose(page, 'rank', 'A')
  const choosingB = choose(page, 'rank', 'B')
  second.resolve(result([42], { rank: 'B', identity: '' }))
  await choosingB
  first.resolve(result([41], { rank: 'A', identity: '' }))
  await choosingA
  assert.equal(page.data.rankFilter, 'B')
  assert.deepEqual(plain(page.data.players.map(player => player.id)), [42])
  assert.equal(page.data.playersFailed, false)
})

test('a previous filter pagination response cannot append to the new filtered list', async () => {
  const pending = deferred()
  const { page, api } = load()
  await page.load()
  await choose(page, 'rank', 'B')
  api.getRecruitmentPlayers = (cursor, _query, filters) => cursor ? pending.promise : Promise.resolve(result([60], filters))
  const paging = page.loadMorePlayers()
  await choose(page, 'identity', 'senior')
  pending.resolve(result([79], { rank: 'B', identity: '' }))
  await paging
  assert.equal(page.data.identityFilter, 'senior')
  assert.deepEqual(plain(page.data.players.map(player => player.id)), [60])
  assert.equal(page.data.playersCursor, null)
  assert.equal(page.data.playersMore, false)
})

test('a delayed initial lobby load cannot overwrite a filter applied before account and posts finish', async () => {
  const personal = deferred(), initial = deferred()
  let reads = 0
  const { page } = load({
    getMyRecruitment: () => personal.promise,
    getRecruitmentPlayers: (_cursor, _query, filters) => ++reads === 1 ? initial.promise : Promise.resolve(result([22], filters))
  })
  const loading = page.load()
  await choose(page, 'identity', 'unknown')
  assert.deepEqual(plain(page.data.players.map(player => player.id)), [22])
  initial.resolve(result([80]))
  personal.resolve({ team_id: 2, content: '已发布内容' })
  await loading
  assert.equal(page.data.identityFilter, 'unknown')
  assert.deepEqual(plain(page.data.players.map(player => player.id)), [22])
})

test('failed filtering can be retried with the same conditions without reading posts or discarding draft text', async () => {
  const { page, state, api } = load()
  await page.load()
  page.setData({ editing: true, draft: '保留招募草稿', keyword: '队友', query: '队友' })
  api.getRecruitmentPlayers = async () => { throw { detail: '临时网络故障' } }
  await choose(page, 'rank', 's_gold')
  assert.equal(page.data.playersFailed, true)
  assert.match(page.data.playersError, /临时网络故障/)
  assert.equal(page.data.players.length, 0)
  const reads = [state.postsReads, state.accountReads]
  api.getRecruitmentPlayers = async (...args) => { state.calls.push(plain(args)); return result([20], args[2]) }
  await page.loadPlayers()
  assert.deepEqual(state.calls[state.calls.length - 1], [null, '队友', { rank: 's_gold', identity: '' }])
  assert.equal(page.data.playersFailed, false)
  assert.equal(page.data.players[0].id, 20)
  assert.equal(page.data.editing, true)
  assert.equal(page.data.draft, '保留招募草稿')
  assert.deepEqual([state.postsReads, state.accountReads], reads)
})

test('clearing rank and identity retains the submitted query and leaves unsent search text alone', async () => {
  const { page, state } = load()
  await page.load()
  page.onKeyword({ detail: { value: '  狙击手  ' } })
  await page.searchPlayers()
  await choose(page, 'rank', 's_diamond')
  await choose(page, 'identity', 'senior')
  page.onKeyword({ detail: { value: '尚未搜索' } })
  await page.clearPlayerFilters()
  assert.equal(page.data.rankFilter, '')
  assert.equal(page.data.identityFilter, '')
  assert.equal(page.data.rankIndex, 0)
  assert.equal(page.data.identityIndex, 0)
  assert.equal(page.data.query, '狙击手')
  assert.equal(page.data.keyword, '尚未搜索')
  assert.deepEqual(state.calls[state.calls.length - 1], [null, '狙击手', { rank: '', identity: '' }])
})

test('missing or mismatched server filter confirmation is an explicit error instead of unfiltered results', async () => {
  for (const echo of [undefined, { rank: 'B', identity: '' }, { rank: 'A+', identity: 'senior' }]) {
    const { page, api } = load()
    await page.load()
    api.getRecruitmentPlayers = async () => {
      const response = result([99])
      if (echo === undefined) delete response.filters
      else response.filters = echo
      return response
    }
    await choose(page, 'rank', 'A+')
    assert.equal(page.data.playersFailed, true)
    assert.equal(page.data.players.length, 0)
    assert.equal(page.data.playersMore, false)
    assert.match(page.data.playersError, /筛选|后端/)
  }
})

test('a pagination response without filter confirmation preserves the existing page and cursor for retry', async () => {
  const { page, state, api } = load()
  await page.load()
  await choose(page, 'rank', 'A+')
  api.getRecruitmentPlayers = async () => ({ items: [{ id: 79 }], has_more: false, next_cursor: null })
  await page.loadMorePlayers()
  assert.deepEqual(plain(page.data.players.map(player => player.id)), [80])
  assert.equal(page.data.playersCursor, 80)
  assert.equal(page.data.playersMore, true)
  assert.ok(state.toasts.some(toast => /筛选|后端/.test(toast.title)) || /筛选|后端/.test(page.data.playersError))
  api.getRecruitmentPlayers = async (...args) => { state.calls.push(plain(args)); return result([79], args[2]) }
  await page.loadMorePlayers()
  assert.deepEqual(state.calls[state.calls.length - 1], [80, '', { rank: 'A+', identity: '' }])
  assert.deepEqual(plain(page.data.players.map(player => player.id)), [80, 79])
})

test('an error from an obsolete pagination request cannot announce failure after new results have loaded', async () => {
  const pending = deferred()
  const { page, state, api } = load()
  await page.load()
  api.getRecruitmentPlayers = (cursor, _query, filters) => cursor ? pending.promise : Promise.resolve(result([18], filters))
  const paging = page.loadMorePlayers()
  await choose(page, 'rank', 'D')
  pending.reject({ detail: '旧请求失败，不应显示' })
  await paging
  assert.deepEqual(plain(page.data.players.map(player => player.id)), [18])
  assert.equal(page.data.playersFailed, false)
  assert.equal(state.toasts.length, 0)
})
