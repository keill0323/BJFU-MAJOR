// Real component/API tests with isolated responses; no browser or production requests.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const test = require('node:test')
const { parseWxml, createRenderer } = require('../../tools/preview-miniprogram.js')
const root = path.resolve(__dirname, '../..')
const componentPath = path.join(root, 'miniprogram/components/player-stats/player-stats')
const plain = value => JSON.parse(JSON.stringify(value))
const deferred = () => {
  let resolve, reject
  const promise = new Promise((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}
const player = (key, overrides = {}) => ({ player_key: key, nickname: '选手' + key,
  teams: [{ team_id: 33, team_name: '拉面队' }], maps_played: 3, kills: 50, deaths: 40,
  assists: 12, kd: 1.25, adr: 83.456, average_rating: 1.234, ...overrides })
const response = (items = [player('opaque-A')], overrides = {}) => ({
  match_id: 8, source: 'perfect_match', source_label: '完美赛事平台', map_count: 6, player_count: 20,
  total: items.length, offset: 0, limit: 50, has_more: false, updated_at: '2026-10-30T02:15:00+00:00',
  notices: ['数据只涵盖已同步的赛事地图。'], items, ...overrides
})

function load(fetch = async () => response(), matchId = 8) {
  let definition
  const calls = [], updates = []
  vm.runInNewContext(fs.readFileSync(componentPath + '.js', 'utf8'), {
    Component: value => { definition = value },
    require(name) {
      assert.ok(name.endsWith('/api.js'))
      return { getMatchPlayerStats: (id, query) => { calls.push({ id, query: plain(query) }); return fetch(id, query) } }
    }
  }, { filename: componentPath + '.js' })
  const instance = { ...definition, ...definition.methods, data: { ...plain(definition.data), matchId } }
  instance.setData = values => { updates.push(plain(values)); Object.assign(instance.data, plain(values)) }
  definition.lifetimes.created.call(instance)
  instance._attached = true
  return { instance, calls, updates, definition }
}
function render(instance) {
  const tree = parseWxml(fs.readFileSync(componentPath + '.wxml', 'utf8'))
  tree.scope = instance.data
  return createRenderer({})(tree)
}

test('public stats show only source fields with coverage and Beijing sync time', async () => {
  const raw = player('opaque-A', { student_id: 'SECRET-STUDENT', steam_id: 'SECRET-STEAM',
    wx_openid: 'SECRET-OPENID', credentials: { token: 'SECRET-TOKEN' } })
  const original = plain(raw)
  const { instance, calls } = load(async () => response([raw]))
  await instance.reloadStats()
  assert.deepEqual(calls, [{ id: 8, query: { keyword: '', offset: 0, limit: 50 } }])
  assert.equal(instance.data.mapCountText, '6')
  assert.equal(instance.data.playerCountText, '20')
  assert.equal(instance.data.updatedText, '2026-10-30 10:15')
  assert.equal(instance.data.updatedZone, '北京时间')
  assert.deepEqual(instance.data.rows[0], { key: 'opaque-A', nickname: '选手opaque-A', team_names: '拉面队',
    maps_text: '3', kda_text: '50 / 40 / 12', kd_text: '1.25', adr_text: '83.5', rating_text: '1.23' })
  assert.doesNotMatch(JSON.stringify(instance.data), /SECRET-|student_id|openid|steam_id|credentials/)
  assert.deepEqual(raw, original)
  const html = render(instance)
  assert.match(html, /完美赛事平台/)
  assert.match(html, /已同步 6 张地图/)
  assert.match(html, /1\.25/)
  assert.match(html, /83\.5/)
  assert.doesNotMatch(html, /SECRET-|Infinity|NaN|undefined/)
})

test('unknown values and zero deaths stay unknown, and never fabricate a KD', async () => {
  const { instance } = load(async () => response([
    player('zero', { kills: 0, deaths: 0, assists: 0, kd: Infinity, adr: 0, average_rating: 0 }),
    player('missing', { nickname: '', teams: [], maps_played: null, kills: null, deaths: undefined,
      assists: '', kd: 25, adr: null, average_rating: undefined }),
    player('bad', { kills: -1, deaths: 10, assists: false, kd: NaN, adr: 'oops', average_rating: -2 })
  ]))
  await instance.reloadStats()
  const [zero, missing, bad] = instance.data.rows
  assert.equal(zero.kda_text, '0 / 0 / 0')
  assert.equal(zero.kd_text, '—')
  assert.equal(zero.adr_text, '0.0')
  assert.equal(zero.rating_text, '0.00')
  assert.equal(missing.nickname, '未命名选手')
  assert.equal(missing.team_names, '未关联战队')
  assert.equal(missing.maps_text, '—')
  assert.equal(missing.kda_text, '— / — / —')
  assert.equal(missing.kd_text, '—')
  assert.equal(bad.kda_text, '— / 10 / —')
  assert.equal(bad.kd_text, '—')
  assert.equal(bad.rating_text, '—')
  assert.doesNotMatch(render(instance), /Infinity|NaN|undefined|null/)
})

test('editing a search is local until submitted; submit and clear reset pagination', async () => {
  const { instance, calls } = load(async () => response())
  await instance.reloadStats()
  instance.onSearchInput({ detail: { value: '  拉面 A&B  ' } })
  assert.equal(calls.length, 1)
  await instance.submitSearch()
  assert.deepEqual(calls[1], { id: 8, query: { keyword: '拉面 A&B', offset: 0, limit: 50 } })
  await instance.clearSearch()
  assert.deepEqual(calls[2], { id: 8, query: { keyword: '', offset: 0, limit: 50 } })
  assert.equal(instance.data.searchDraft, '')
})

test('load-more merges by player key and prevents concurrent page reads', async () => {
  const pending = deferred()
  const { instance, calls } = load((id, query) => query.offset === 0
    ? Promise.resolve(response([player('A'), player('B')], { total: 4, has_more: true })) : pending.promise)
  await instance.reloadStats()
  const loading = instance.loadMore()
  instance.loadMore()
  assert.equal(calls.length, 2)
  assert.equal(instance.data.loadingMore, true)
  assert.deepEqual(calls[1].query, { keyword: '', offset: 2, limit: 50 })
  pending.resolve(response([player('B', { kills: 60 }), player('C')], { offset: 2, total: 4 }))
  await loading
  assert.deepEqual(instance.data.rows.map(row => row.key), ['A', 'B', 'C'])
  assert.equal(instance.data.rows[1].kda_text, '60 / 40 / 12')
  assert.equal(instance.data.loadingMore, false)
  assert.equal(instance.data.hasMore, false)
})

test('a failed next page keeps loaded rows and retries the same offset', async () => {
  let failure = true
  const { instance, calls } = load(async (id, query) => {
    if (!query.offset) return response([player('A')], { total: 2, has_more: true })
    if (failure) throw { detail: 'SECRET-SERVER-DETAIL', statusCode: 500 }
    return response([player('B')], { offset: 1, total: 2 })
  })
  await instance.reloadStats()
  await instance.loadMore()
  assert.equal(instance.data.rows.length, 1)
  assert.match(instance.data.moreError, /加载失败/)
  assert.doesNotMatch(render(instance), /SECRET-SERVER-DETAIL/)
  assert.match(render(instance), /重试加载更多/)
  failure = false
  await instance.loadMore()
  assert.deepEqual(calls.map(call => call.query.offset), [0, 1, 1])
  assert.deepEqual(instance.data.rows.map(row => row.key), ['A', 'B'])
})

test('late success or failure from a previous keyword cannot replace the new results', async () => {
  for (const failOld of [false, true]) {
    const old = deferred()
    const { instance } = load((id, query) => query.keyword === '' ? old.promise
      : Promise.resolve(response([player('new')], { total: 1 })))
    const first = instance.reloadStats()
    instance.onSearchInput({ detail: { value: 'new' } })
    await instance.submitSearch()
    const expected = plain(instance.data)
    if (failOld) old.reject(new Error('late failure'))
    else old.resolve(response([player('old')]))
    await first
    assert.deepEqual(instance.data, expected)
  }
})

test('changing events invalidates the old event request and clears its search', async () => {
  const old = deferred()
  const { instance, definition, calls } = load((id, query) => id === 8 ? old.promise
    : Promise.resolve(response([player('event-9')], { match_id: 9 })))
  const first = instance.reloadStats()
  instance.data.matchId = 9
  instance.data.keyword = 'old-search'
  instance.data.searchDraft = 'old-search'
  await definition.properties.matchId.observer.call(instance)
  await new Promise(resolve => setImmediate(resolve))
  old.resolve(response([player('old-event')]))
  await first
  assert.equal(instance.data.rows[0].key, 'event-9')
  assert.equal(instance.data.keyword, '')
  assert.deepEqual(calls.map(call => call.id), [8, 9])
})

test('detach stops both late success and late error from updating the component', async () => {
  for (const failure of [false, true]) {
    const pending = deferred()
    const { instance, definition, updates } = load(() => pending.promise)
    const loading = instance.reloadStats()
    definition.lifetimes.detached.call(instance)
    const count = updates.length
    if (failure) pending.reject({ detail: 'after detach' })
    else pending.resolve(response())
    await loading
    assert.equal(updates.length, count)
  }
})

test('unselected events make no request and mismatched or malformed results stay hidden', async () => {
  const missing = load(undefined, 0)
  await missing.instance.reloadStats()
  assert.equal(missing.calls.length, 0)
  assert.match(render(missing.instance), /尚未选择赛事/)
  for (const payload of [response(undefined, { match_id: 9 }), response(undefined, { source: 'unknown' }),
    response(undefined, { offset: 2 }), response([], { has_more: true, total: 2 }),
    response([player('')]), response(undefined, { total: -1 })]) {
    const { instance } = load(async () => payload)
    await instance.reloadStats()
    assert.equal(instance.data.rows.length, 0)
    assert.match(instance.data.error, /加载失败/)
  }
})

test('empty and failure states are explicit and a retry can recover', async () => {
  let mode = 'loading'
  const pending = deferred()
  const { instance } = load(() => mode === 'loading' ? pending.promise : Promise.resolve(response([], { map_count: 0, player_count: 0 })))
  const loading = instance.reloadStats()
  assert.match(render(instance), /正在加载选手数据/)
  pending.reject({ detail: 'SECRET-DIAGNOSTIC', code: 'BACKEND_UPGRADE_REQUIRED' })
  await loading
  assert.match(render(instance), /尚未上线/)
  assert.match(render(instance), /重新加载/)
  mode = 'empty'
  await instance.reloadStats()
  assert.match(render(instance), /暂无已同步的选手数据/)
  instance.onSearchInput({ detail: { value: 'nobody' } })
  await instance.submitSearch()
  assert.match(render(instance), /没有找到相关选手/)
})

test('statistical definitions distinguish weighted approximate ADR from source Rating', async () => {
  const { instance } = load(async () => response())
  await instance.reloadStats()
  assert.doesNotMatch(render(instance), /按逐图回合数加权/)
  instance.toggleMethod()
  const html = render(instance)
  assert.match(html, /按逐图回合数加权的近似值/)
  assert.match(html, /逐图 Rating 的均值/)
  assert.match(html, /不代表选手全部历史战绩/)
  assert.match(html, /数据只涵盖已同步的赛事地图/)
})

test('timestamps with no timezone are not mislabeled as Beijing, and UTC dates roll over correctly', async () => {
  for (const [stamp, expected, zone] of [
    ['2026-10-30T08:00:00', '2026-10-30 08:00', '来源时间'],
    ['2026-10-31T23:45:00Z', '2026-11-01 07:45', '北京时间'],
    ['2026-10-30T15:10:00+08:00', '2026-10-30 15:10', '北京时间']
  ]) {
    const { instance } = load(async () => response(undefined, { updated_at: stamp }))
    await instance.reloadStats()
    assert.equal(instance.data.updatedText, expected)
    assert.equal(instance.data.updatedZone, zone)
  }
})

test('component registration and horizontal table keep the player column readable', () => {
  const config = JSON.parse(fs.readFileSync(path.join(root, 'miniprogram/pages/match/match.json'), 'utf8'))
  assert.equal(config.usingComponents['player-stats'], '/components/player-stats/player-stats')
  const template = fs.readFileSync(componentPath + '.wxml', 'utf8')
  assert.match(template, /scroll-view scroll-x/)
  assert.match(template, /bindconfirm="submitSearch"/)
  assert.match(template, /bindtap="loadMore"/)
  const css = fs.readFileSync(componentPath + '.wxss', 'utf8')
  assert.match(css, /\.stats-person\s*\{[^}]*position:\s*sticky;[^}]*left:\s*0/)
})

test('API helper uses the public endpoint and encodes the search query without account calls', async () => {
  let exported
  const calls = []
  vm.runInNewContext(fs.readFileSync(path.join(root, 'miniprogram/utils/api.js'), 'utf8'), {
    module: { get exports() { return exported }, set exports(value) { exported = value } }, console,
    wx: { getStorageSync: () => '', request(options) { calls.push(options); options.success({ statusCode: 200, data: response() }) } }
  })
  await exported.getMatchPlayerStats(8, { keyword: ' 拉面 A&B ', offset: 50, limit: 50 })
  assert.equal(calls.length, 1)
  assert.equal(calls[0].method, 'GET')
  assert.equal(calls[0].header.Authorization, '')
  assert.match(calls[0].url, /\/api\/matches\/8\/player-stats\?offset=50&limit=50&keyword=%E6%8B%89%E9%9D%A2%20A%26B$/)
})
