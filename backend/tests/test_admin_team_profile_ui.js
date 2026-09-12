// Exercise the real read-only component and admin entry points with isolated APIs.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const test = require('node:test')
const { parseWxml, createRenderer } = require('../../tools/preview-miniprogram.js')
const root = path.resolve(__dirname, '../../miniprogram')
const componentPath = 'components/team-profile/team-profile'
const plain = value => JSON.parse(JSON.stringify(value))
const deferred = () => {
  let resolve, reject
  const promise = new Promise((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}
const member = (user_id, overrides = {}) => ({
  id: user_id + 100, user_id, nickname: '队员' + user_id, game_id: 'Player_' + user_id,
  rank: 'B', rating: 20, role: 'member', is_verified: true, identity: 'senior', ...overrides
})
const team = (id, overrides = {}) => ({
  id, name: '队伍' + id, captain_id: 2, status: 'approved', description: '认真打好每一回合。',
  members: [member(1), member(2, { role: 'captain', nickname: '队长' })], ...overrides
})

function load(file, getTeam = async id => team(id), component = false) {
  let definition
  const reads = [], updates = []
  const api = new Proxy({ BASE: 'https://example.test', getTeam: id => {
    reads.push(id)
    return getTeam(id)
  } }, { get(target, key) {
    if (key in target) return target[key]
    throw new Error('Unexpected API access: ' + String(key))
  } })
  const absolute = path.join(root, file + '.js')
  vm.runInNewContext(fs.readFileSync(absolute, 'utf8'), {
    Page: value => { definition = value }, Component: value => { definition = value }, console,
    require(name) {
      if (name.endsWith('/api.js')) return api
      if (name.endsWith('/rank.js')) return require(path.join(root, 'utils/rank.js'))
      if (name.endsWith('/tournament.js')) return require(path.join(root, 'utils/tournament.js'))
      throw new Error('Unexpected module: ' + name)
    },
    wx: new Proxy({}, { get(target, key) { throw new Error('Unexpected wx call: ' + String(key)) } })
  }, { filename: absolute })
  const instance = { ...definition, ...definition.methods, data: plain(definition.data) }
  if (component) instance.data.teamId = 1
  instance.setData = values => { updates.push(plain(values)); Object.assign(instance.data, values) }
  if (component && definition.lifetimes.created) definition.lifetimes.created.call(instance)
  return { instance, reads, updates }
}

function profile(getTeam) { return load(componentPath, getTeam, true) }
function render(instance, file = componentPath) {
  const tree = parseWxml(fs.readFileSync(path.join(root, file + '.wxml'), 'utf8'))
  tree.scope = instance.data
  return createRenderer({})(tree)
}

test('the detail API roster supplies the missing captain name and highest-five rating', async () => {
  const raw = team(1, { captain_id: 7, members: [95, 82, 71, 60, 55, 20, null].map((rating, index) =>
    member(index + 1, { rating, role: index === 6 ? 'captain' : 'member', nickname: index === 6 ? '林间队长' : '队员' + index })) })
  assert.equal(raw.rating, undefined)
  assert.equal(raw.captain_name, undefined)
  const { instance, reads } = profile(async () => raw)
  await instance.loadTeam()
  assert.equal(instance.data.team.display_rating, 363)
  assert.equal(instance.data.team.member_count, 7)
  assert.equal(instance.data.team.captain_name, '林间队长')
  assert.deepEqual(plain(instance.data.members.map(item => item.user_id)), [7, 1, 2, 3, 4, 5, 6])
  assert.equal(instance.data.members[0].is_captain, true)
  assert.equal(instance.data.members[0].score_text, '—')
  const html = render(instance)
  assert.match(html, /队长 · 林间队长/)
  assert.match(html, />363</)
  assert.match(html, /评分最高的 5 名队员/)
  assert.deepEqual(reads, [1])
})

test('unverified and unknown identities are counted separately from confirmed seniors', async () => {
  const { instance } = profile(async () => team(1, { members: [
    member(1, { identity: 'new_student' }), member(2, { identity: 'senior' }),
    member(3, { identity: null }), member(4, { identity: 'senior', is_verified: false }),
    member(5, { identity: 'new_student', is_verified: false })
  ] }))
  await instance.loadTeam()
  assert.equal(instance.data.team.new_student_count, 1)
  assert.equal(instance.data.team.senior_count, 1)
  assert.equal(instance.data.team.unknown_count, 3)
  assert.equal(instance.data.team.verified_count, 3)
  for (const id of [3, 4, 5]) {
    const item = instance.data.members.find(value => value.user_id === id)
    assert.equal(item.identity, 'unknown')
    assert.match(item.identity_text, /待确认/)
  }
  const html = render(instance)
  assert.match(html, /新生 1/)
  assert.match(html, /老生 1/)
  assert.match(html, /待确认 3/)
  assert.match(html, /身份待确认/)
  assert.match(html, /学籍未认证/)
})

test('member cards render the real plus and high-S badges with full names and game IDs', async () => {
  const longName = '一位名字很长但是管理员仍应看到完整昵称的队员'
  const cases = [['A+', 'a-plus'], ['C+', 'c-plus'], ['S10', 's-gold'], ['S25', 's-diamond'], ['S50', 's-demon']]
  const { instance } = profile(async () => team(1, { members: cases.map(([rank], index) =>
    member(index + 1, { rank, nickname: index === 0 ? longName : '队员' + index, game_id: 'Game_' + index })) }))
  await instance.loadTeam()
  for (let index = 0; index < cases.length; index++) {
    const item = instance.data.members.find(value => value.user_id === index + 1)
    assert.equal(item.rank_icon, '/images/ranks/' + cases[index][1] + '.svg')
  }
  const html = render(instance)
  assert.ok(html.includes(longName))
  assert.match(html, /游戏 ID：Game_0/)
  assert.match(html, /金S10星/)
  assert.match(html, /钻S25星/)
  assert.match(html, /魔王S50星/)
  assert.equal((html.match(/class="rank-badge"[^>]+src="data:image\/svg\+xml;base64,/g) || []).length, 5)
})

test('team and member view data discard private fields even if an API unexpectedly includes them', async () => {
  const privateFields = { student_id: 'SECRET-STUDENT', openid: 'SECRET-OPENID', verify_image: 'SECRET-PROOF',
    rank_image: 'SECRET-RANK-PROOF', credentials: { token: 'SECRET-TOKEN' } }
  const raw = team(1, { ...privateFields, members: [member(2, { ...privateFields, nickname: '公开昵称' })] })
  const original = plain(raw)
  const { instance } = profile(async () => raw)
  await instance.loadTeam()
  const displayed = JSON.stringify(instance.data)
  assert.doesNotMatch(displayed, /SECRET-|student_id|openid|verify_image|rank_image|credentials/)
  assert.doesNotMatch(render(instance), /SECRET-/)
  assert.match(displayed, /公开昵称/)
  assert.deepEqual(raw, original)
})

test('missing roster details and an empty team have explicit placeholders instead of invented values', async () => {
  const { instance } = profile(async () => team(1, { name: '', description: '', captain_id: null, members: [
    member(8, { nickname: '', game_id: '', rank: null, rating: null, identity: null, is_verified: false })
  ] }))
  await instance.loadTeam()
  assert.equal(instance.data.team.ranked_count, 0)
  assert.equal(instance.data.team.display_rating, 0)
  assert.equal(instance.data.team.captain_name, '暂未设置')
  let html = render(instance)
  assert.match(html, /未命名队伍/)
  assert.match(html, /玩家8/)
  assert.match(html, /游戏 ID：暂未设置/)
  assert.match(html, /队长还没有填写队伍简介/)
  assert.match(html, /待认证/)
  assert.match(html, /身份待确认/)
  assert.doesNotMatch(html, /NaN|undefined|null/)
  const empty = profile(async () => team(1, { members: [] })).instance
  await empty.loadTeam()
  assert.equal(empty.data.team.member_count, 0)
  html = render(empty)
  assert.match(html, /暂无成员信息/)
})

test('loading and failed reads clear previous team data and expose a working retry', async () => {
  let response = Promise.resolve(team(1))
  const { instance, reads } = profile(() => response)
  await instance.loadTeam()
  const pending = deferred()
  response = pending.promise
  const failed = instance.loadTeam()
  assert.equal(instance.data.loading, true)
  assert.equal(instance.data.team, null)
  assert.deepEqual(plain(instance.data.members), [])
  assert.match(render(instance), /正在加载队伍/)
  pending.reject({ statusCode: 500, detail: 'server internal SECRET-DIAGNOSTIC' })
  await failed
  assert.equal(instance.data.loading, false)
  assert.match(instance.data.error, /加载失败/)
  assert.match(render(instance), /重新加载/)
  assert.doesNotMatch(render(instance), /SECRET-DIAGNOSTIC|队伍1/)
  const tree = parseWxml(fs.readFileSync(path.join(root, componentPath + '.wxml'), 'utf8'))
  const findRetry = node => node.tag === 'button' && node.attrs.bindtap === 'loadTeam'
    ? node : (node.children || []).map(findRetry).find(Boolean)
  assert.ok(findRetry(tree))
  response = Promise.resolve(team(1))
  await instance.loadTeam()
  assert.equal(instance.data.error, '')
  assert.equal(instance.data.team.id, 1)
  assert.deepEqual(reads, [1, 1, 1])
})

test('invalid selection and mismatched responses never display another team', async () => {
  const { instance, reads } = profile(async () => team(2))
  instance.data.teamId = 0
  await instance.loadTeam()
  assert.deepEqual(reads, [])
  assert.equal(instance.data.team, null)
  instance.data.teamId = 1
  await instance.loadTeam()
  assert.equal(instance.data.team, null)
  assert.match(instance.data.error, /加载失败/)
  assert.doesNotMatch(render(instance), /队伍2/)
})

test('switching from team A to B ignores both late success and late failure from A', async () => {
  for (const failOld of [false, true]) {
    const old = deferred()
    const { instance, reads } = profile(id => id === 1 ? old.promise : Promise.resolve(team(id)))
    const loadingOld = instance.loadTeam()
    instance.data.teamId = 2
    await instance.loadTeam()
    const expected = plain(instance.data)
    if (failOld) old.reject({ detail: '旧队伍读取失败' })
    else old.resolve(team(1))
    await loadingOld
    assert.deepEqual(plain(instance.data), expected)
    assert.equal(instance.data.team.id, 2)
    assert.deepEqual(reads, [1, 2])
  }
})

test('closing the detail component prevents pending responses from updating its data', async () => {
  for (const fail of [false, true]) {
    const pending = deferred()
    const { instance, updates } = profile(() => pending.promise)
    const loading = instance.loadTeam()
    instance.lifetimes.detached.call(instance)
    const updatesBeforeResponse = updates.length
    if (fail) pending.reject({ detail: '关闭后失败' })
    else pending.resolve(team(1))
    await loading
    assert.equal(updates.length, updatesBeforeResponse)
  }
})

test('both admin surfaces register and embed the shared team profile under their detail dialogs', () => {
  const cases = [
    ['pages/admin/admin', '{{detailTeamId}}', 'showTeamDetailPanel'],
    ['pages/admin/mdetail/mdetail', '{{historyTeam.team_id}}', 'showHistory']
  ]
  for (const [file, idExpression, visible] of cases) {
    const config = JSON.parse(fs.readFileSync(path.join(root, file + '.json'), 'utf8'))
    assert.equal(config.usingComponents['team-profile'], '/components/team-profile/team-profile')
    const tree = parseWxml(fs.readFileSync(path.join(root, file + '.wxml'), 'utf8'))
    let found = false
    function visit(node, ancestors = []) {
      if (node.tag === 'team-profile') {
        assert.equal(node.attrs['team-id'], idExpression)
        assert.ok(ancestors.some(parent => String(parent.attrs && parent.attrs['wx:if']).includes(visible)))
        found = true
      }
      for (const child of node.children || []) visit(child, [...ancestors, node])
    }
    visit(tree)
    assert.ok(found, file + ' must expose the team information')
  }
})

test('opening admin team details immediately mounts the selected team without a competing page request', async () => {
  const { instance, reads } = load('pages/admin/admin')
  await instance.showTeamDetail({ currentTarget: { dataset: { id: 34 } } })
  assert.equal(instance.data.showTeamDetailPanel, true)
  assert.equal(Number(instance.data.detailTeamId), 34)
  assert.deepEqual(reads, [])
  instance.closeTeamDetail()
  assert.equal(instance.data.showTeamDetailPanel, false)
})

test('tournament team details reopen on current team information after viewing another team history', () => {
  const { instance, reads } = load('pages/admin/mdetail/mdetail')
  instance.data.rankingTeams = [{ team_id: 1, team_name: '甲队' }, { team_id: 2, team_name: '乙队' }]
  instance.data.allRounds = []
  instance.showTeam({ currentTarget: { dataset: { id: 1 } } })
  assert.equal(instance.data.historyTab, 'profile')
  instance.switchHistoryTab({ currentTarget: { dataset: { tab: 'history' } } })
  assert.equal(instance.data.historyTab, 'history')
  instance.closeHistory()
  instance.showTeam({ currentTarget: { dataset: { id: 2 } } })
  assert.equal(instance.data.historyTab, 'profile')
  assert.equal(instance.data.historyTeam.team_id, 2)
  assert.deepEqual(reads, [])
})
