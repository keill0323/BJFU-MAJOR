const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const { buildTeamRanking, buildTeamHistory } = require('../../miniprogram/utils/tournament.js')
const { parseWxml, createRenderer } = require('../../tools/preview-miniprogram.js')
const root = path.resolve(__dirname, '../..')
const clone = value => JSON.parse(JSON.stringify(value))
const teams = () => Array.from({ length: 6 }, (_, i) => ({ team_id: i + 1, team_name: '队伍' + (i + 1),
  seed: i + 1, stage: 'playoff', registration_status: 'approved', group_name: i < 3 ? '上区' : '下区', wins: 999 - i, diff: 999 - i }))
const round = (id, a, b, winner, group = '淘汰赛', status = 'finished') => ({ id, round_number: id,
  team1_id: a, team2_id: b, winner_id: winner, group_name: group, status,
  team1_name: '队伍' + a, team2_name: '队伍' + b, team1_score: winner === a ? 2 : 0, team2_score: winner === b ? 2 : 0 })
const bracket = () => [round(1, 2, 3, 2), round(2, 5, 6, 5), round(3, 1, 5, 5), round(4, 4, 2, 4)]

test('active teams show their stage and ties are ordered only by seed', () => {
  const rows = [{ team_id: 1, seed: 8, stage: 'legend', wins: 99, diff: 999, rating: 999 },
    { team_id: 2, seed: 1, stage: 'legend', wins: 0, diff: -999, rating: 0 }]
  const ranking = buildTeamRanking(rows, [])
  assert.deepEqual(ranking.map(t => t.team_id), [2, 1])
  assert.equal(ranking[0].rank_weight, ranking[1].rank_weight)
  assert.deepEqual(ranking.map(t => t.rank_badge), ['传奇', '传奇'])
  assert.deepEqual(rows.map(t => t.team_id), [1, 2], 'formatting must not mutate server records')
})

test('missing group results do not invent prize placements, and unapproved teams stay unranked', () => {
  const remaining = [
    { team_id: 7, seed: 7, stage: 'eliminated', group_name: '上区' },
    { team_id: 8, seed: 8, stage: 'eliminated', group_name: '下区' },
    { team_id: 9, seed: 9, stage: 'eliminated', group_name: '附加赛' },
    { team_id: 10, seed: 10, stage: 'eliminated', group_name: '附加赛' },
    { team_id: 11, seed: 11, stage: 'eliminated', group_name: '竹林' },
    { team_id: 12, stage: 'challenger', registration_status: 'approved' },
    { team_id: 13, registration_status: 'pending' },
    { team_id: 14, registration_status: 'rejected' }
  ]
  const ranking = buildTeamRanking(teams().concat(remaining), bracket().concat(round(5, 5, 4, 5)))
  assert.deepEqual(ranking.map(t => t.rank_badge), ['冠', '亚', '四强', '四强', '六强', '六强', '待定', '待定', '待定', '待定', '待定', '—', '—', '—'])
})

test('semifinal winners reach the final before its fixture exists; losers remain tied fourth-place finishers', () => {
  const ranking = buildTeamRanking(teams(), bracket())
  assert.deepEqual(ranking.map(t => t.team_id), [4, 5, 1, 2, 3, 6])
  assert.deepEqual(ranking.map(t => t.ko_rank), ['决赛', '决赛', '四强', '四强', '六强', '六强'])
  assert.equal(ranking[2].progress_text, '已淘汰')
})

test('six-team bracket recognizes two semifinal byes without adding wins', () => {
  const rounds = bracket().slice(0, 2).map(r => ({ ...r, status: 'pending', winner_id: null }))
  const ranking = buildTeamRanking(teams(), rounds)
  assert.deepEqual(ranking.slice(0, 2).map(t => t.team_id), [1, 4])
  assert.ok(ranking.slice(0, 2).every(t => t.ko_rank === '四强'))
  assert.deepEqual(buildTeamHistory(1, rounds), [])
})

test('pending and missing-winner finals award no medals; finished final resolves champion and runner-up', () => {
  const final = round(5, 5, 4, null, '淘汰赛', 'pending')
  for (const status of ['pending', 'in_progress', 'finished']) {
    final.status = status
    assert.ok(buildTeamRanking(teams(), bracket().concat(final)).every(t => !['冠军', '亚军'].includes(t.ko_rank)))
  }
  final.winner_id = 5
  const ranking = buildTeamRanking(teams(), bracket().concat(final))
  assert.deepEqual(ranking.slice(0, 2).map(t => [t.team_id, t.ko_rank]), [[5, '冠军'], [4, '亚军']])
})

test('phase records retain earlier wins after promotion and BO3 is one match with own-team-first map scores', () => {
  const rounds = [round(10, 9, 1, 1, '竹林'), round(11, 1, 8, 1, '竹林'), round(12, 8, 1, 8, '附加赛'),
    round(13, 1, 6, 1, '上区'), { ...round(14, 5, 1, 5), bo3_scores: [{ t1: 13, t2: 9 }, { t1: 13, t2: 7 }] }]
  const history = buildTeamHistory('1', rounds)
  assert.deepEqual(history.map(s => [s.title, s.wins, s.losses]), [
    ['挑战者组 · 竹林组', 2, 0], ['附加赛', 0, 1], ['传奇组 · 上区', 1, 0], ['淘汰赛', 0, 1]])
  assert.equal(history[3].rounds[0].score_text, '0 : 2')
  assert.equal(history[3].rounds[0].maps_text, '图1 9 : 13 / 图2 7 : 13')
})

test('pending, in-progress, missing results and byes never fabricate losses or wins', () => {
  const history = buildTeamHistory(1, [round(1, 1, 2, null, '__proto__', 'pending'),
    round(2, 1, 2, null, '__proto__', 'in_progress'), round(3, 1, 2, null, '__proto__'), round(4, 1, null, 1, '__proto__')])
  assert.equal(history.length, 1)
  assert.deepEqual([history[0].wins, history[0].losses, history[0].pending, history[0].unresolved, history[0].byes], [0, 0, 2, 1, 1])
  assert.equal(history[0].rounds[0].score_text, '—')
  assert.deepEqual(history[0].rounds.map(r => r.outcome), ['待比赛', '进行中', '结果待确认', '轮空'])
})

for (const admin of [false, true]) {
  test(`${admin ? 'admin' : 'public'} team card opens phase records and closes without write requests`, async () => {
    const file = path.join(root, admin ? 'miniprogram/pages/admin/mdetail/mdetail.js' : 'miniprogram/pages/match/match.js')
    const detail = { match: { id: 7, status: 'in_progress' }, teams: teams(), rounds: bracket().concat(round(9, 1, 8, 1, '竹林')) }
    let page
    vm.runInNewContext(fs.readFileSync(file, 'utf8'), { Page: value => { page = value }, console,
      require(name) {
        if (name.endsWith('/tournament.js')) return require(path.resolve(path.dirname(file), name))
        return new Proxy({}, { get(_, key) { if (key === 'adminMatchDetail') return async () => detail; throw Error('Unexpected API: ' + String(key)) } })
      }, wx: { showModal: value => { throw Error(JSON.stringify(value)) } } }, { filename: file })
    page.data = clone(page.data)
    page.setData = values => Object.assign(page.data, clone(values))
    if (admin) { page.data.matchId = 7; await page.load() }
    else { page.data.match = detail.match; page.buildData(detail, null, false) }
    const source = fs.readFileSync(file.replace(/\.js$/, '.wxml'), 'utf8')
    const render = () => { const tree = parseWxml(source); tree.scope = page.data; return createRenderer({})(tree) }
    assert.equal((render().match(/class="standing-row"/g) || []).length, 6)
    assert.doesNotMatch(render(), /净胜|999|队伍状态|teams-swiper/)
    page.showTeam({ currentTarget: { dataset: { id: '1' } } })
    assert.equal(page.data.showHistory, true)
    if (admin) page.switchHistoryTab({ currentTarget: { dataset: { tab: 'history' } } })
    assert.deepEqual(page.data.historySections.map(s => [s.wins, s.losses]), [[1, 0], [0, 1]])
    assert.match(render(), /1 胜 · 0 负/)
    assert.match(render(), /0 胜 · 1 负/)
    if (admin) assert.match(render(), /导出该队/)
    page.closeHistory()
    assert.equal(page.data.showHistory, false)
    assert.deepEqual(page.data.historySections, [])
  })
}

// Complete fixtures preserve original group membership in rounds after promotion.
function prizeFixture(groupCount, groupSize) {
  let nextRound = 1
  const rows = Array.from({ length: 4 + groupCount * groupSize }, (_, i) => ({ team_id: i + 1, team_name: '参赛队' + (i + 1),
    seed: i + 1, registration_status: 'approved', stage: i < 4 ? 'legend' : 'eliminated', group_name: null, rating: 0 }))
  const rounds = []
  const byId = id => rows.find(t => t.team_id === id)
  const play = (a, b, group, ko = false) => {
    const r = round(nextRound++, a, b, a, group)
    r.team1_score = ko ? 2 : 13; r.team2_score = ko ? 0 : 1
    rounds.push(r)
  }
  const robin = (ids, group) => {
    for (let i = 0; i < ids.length; i++) for (let j = i + 1; j < ids.length; j++) play(ids[i], ids[j], group)
  }
  const winners = [], runners = []
  for (let g = 0; g < groupCount; g++) {
    const ids = Array.from({ length: groupSize }, (_, j) => 5 + g * groupSize + j)
    ids.forEach(id => { byId(id).group_name = String.fromCharCode(65 + g) })
    robin(ids, String.fromCharCode(65 + g))
    winners.push(ids[0]); runners.push(ids[1])
  }
  if (groupCount === 3) {
    runners.forEach(id => { byId(id).group_name = '附加赛' })
    robin(runners, '附加赛')
    winners.push(runners[0])
  }
  const legend = [1, 2, 3, 4].concat(winners)
  const upper = legend.slice(0, 4), lower = legend.slice(4)
  for (const [ids, group] of [[upper, '上区'], [lower, '下区']]) {
    ids.forEach((id, index) => { byId(id).stage = index < 3 ? 'playoff' : 'eliminated'; byId(id).group_name = group })
    robin(ids, group)
  }
  play(upper[1], upper[2], '淘汰赛', true)
  play(lower[1], lower[2], '淘汰赛', true)
  play(upper[0], lower[1], '淘汰赛', true)
  play(lower[0], upper[1], '淘汰赛', true)
  play(upper[0], lower[0], '淘汰赛', true)
  return { teams: rows, rounds }
}

for (const [groups, size, bands] of [
  [3, 4, { '7–8': 2, '9–10': 2, '11–13': 3, '14–16': 3 }],
  [4, 3, { '7–8': 2, '9–12': 4, '13–16': 4 }],
  [4, 4, { '7–8': 2, '9–12': 4, '13–16': 4, '17–20': 4 }]
]) {
  test(`${groups} groups of ${size} produce the agreed prize bands with original group positions`, () => {
    const fixture = prizeFixture(groups, size)
    const ranking = buildTeamRanking(fixture.teams, fixture.rounds)
    const actual = {}
    ranking.filter(t => t.rank_weight >= 5).forEach(t => { actual[t.rank_badge] = (actual[t.rank_badge] || 0) + 1 })
    assert.deepEqual(actual, bands)
    assert.ok(ranking.filter(t => t.rank_weight === 7).every(t => /小组第 [234] 名/.test(t.group_text)))
    const last = ranking.filter(t => t.rank_weight === 7)
    assert.ok(last.every((t, i) => !i || t.placement_order >= last[i - 1].placement_order))
    assert.equal(ranking[0].rank_badge, '冠')
    assert.equal(ranking[1].rank_badge, '亚')
  })
}

test('prize bands do not shift when a higher-placed team is absent from the current team list', () => {
  const fixture = prizeFixture(3, 4)
  const before = buildTeamRanking(fixture.teams, fixture.rounds).find(t => t.team_id === 7)
  const after = buildTeamRanking(fixture.teams.filter(t => t.team_id !== 1), fixture.rounds).find(t => t.team_id === 7)
  assert.equal(before.rank_badge, '11–13')
  assert.equal(after.rank_badge, before.rank_badge)
})

test('incomplete or fully tied group scores require confirmation rather than assigning a prize by seed', () => {
  const fixture = prizeFixture(4, 3)
  const own = fixture.rounds.filter(r => r.group_name === 'A')
  own[0].status = 'pending'
  let rows = buildTeamRanking(fixture.teams, fixture.rounds)
  assert.equal(rows.find(t => t.team_id === 6).rank_badge, '待定')
  own[0].status = 'finished'
  own[1].winner_id = own[1].team2_id
  own[1].team1_score = 1; own[1].team2_score = 13
  rows = buildTeamRanking(fixture.teams, fixture.rounds)
  assert.equal(rows.find(t => t.team_id === 6).rank_badge, '待定')
  assert.equal(rows.find(t => t.team_id === 7).rank_badge, '待定')
})

test('completed groups separate third and fourth even if displayed API wins and seeds suggest the reverse', () => {
  const fixture = prizeFixture(3, 4)
  const third = fixture.teams.find(t => t.team_id === 7), fourth = fixture.teams.find(t => t.team_id === 8)
  third.wins = 0; third.seed = 99
  fourth.wins = 999; fourth.seed = 1
  const rows = buildTeamRanking(fixture.teams, fixture.rounds)
  assert.equal(rows.find(t => t.team_id === 7).rank_badge, '11–13')
  assert.equal(rows.find(t => t.team_id === 8).rank_badge, '14–16')
  assert.ok(rows.findIndex(t => t.team_id === 7) < rows.findIndex(t => t.team_id === 8))
})
