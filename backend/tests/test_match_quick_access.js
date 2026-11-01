// Real public match-page data/lifecycle with synthetic fixtures; never calls a live API.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const test = require('node:test')

const root = path.resolve(__dirname, '../..')
const plain = value => JSON.parse(JSON.stringify(value))
const click = dataset => ({ currentTarget: { dataset } })
const deferred = () => {
  let resolve, reject
  const promise = new Promise((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}
const team = (id, group = 'A', stage = 'challenger') => ({ team_id: id, team_name: '队伍 ' + id,
  group_name: group, stage, registration_status: 'approved', seed: id })
const round = (id, group = 'A', first = 1, second = 2, values = {}) => ({ id, round_number: id,
  group_name: group, team1_id: first, team2_id: second, team1_name: first == null ? null : '队伍 ' + first,
  team2_name: second == null ? null : '队伍 ' + second, status: 'pending', ...values })
const detail = (teams = [], rounds = [], values = {}) => ({ match: { id: 7, name: '赛事', status: 'in_progress', ...values }, teams, rounds })

function load(apiMethods = {}, token = 'token-a') {
  let page
  const calls = { toasts: [], modals: [], chat: [], refreshes: 0, scrolls: [], actionSheets: [], navigation: [] }
  const storage = { token }
  const api = new Proxy({
    getMatchDetail: async () => detail(),
    getMyRegistration: async () => ({ registered: false, registration: null }),
    getMyTeam: async () => null,
    ...apiMethods
  }, { get(target, name) { if (name in target) return target[name]; throw new Error('Unexpected API ' + name) } })
  const file = path.join(root, 'miniprogram/pages/match/match.js')
  vm.runInNewContext(fs.readFileSync(file, 'utf8'), {
    Page: value => { page = value },
    require(name) {
      if (name.endsWith('/api.js')) return api
      if (name.endsWith('/tournament.js')) return require(path.join(root, 'miniprogram/utils/tournament.js'))
      if (name.endsWith('/chat-client.js')) return { contact: async (...args) => calls.chat.push(args) }
      throw new Error('Unexpected require ' + name)
    },
    wx: { getStorageSync: key => storage[key], setStorageSync: (key, value) => { storage[key] = value },
      showToast: value => calls.toasts.push(value), showModal: value => calls.modals.push(value),
      showActionSheet: value => calls.actionSheets.push(value),
      pageScrollTo: value => calls.scrolls.push(plain(value)),
      navigateTo: value => calls.navigation.push(plain(value)),
      stopPullDownRefresh: () => calls.refreshes++ },
    console
  }, { filename: file })
  page.data = plain(page.data)
  page.setData = (values, callback) => { Object.assign(page.data, plain(values)); if (callback) callback() }
  return { page, calls, storage }
}

function apply(page, value, id = null, captain = false) {
  page.detail = value
  page.setData({ match: value.match, myTeamId: id, isCaptain: captain })
  page.buildData(value, id, captain)
}

function knockoutRounds() {
  return [
    round(20, '淘汰赛', 3, 5, { status: 'finished', winner_id: 3 }),
    round(21, '淘汰赛', 4, 6, { status: 'finished', winner_id: 6 }),
    round(22, '淘汰赛', 1, 6, { status: 'finished', winner_id: 1 }),
    round(23, '淘汰赛', 2, 3, { status: 'finished', winner_id: 2 }),
    round(24, '淘汰赛', 1, 2)
  ]
}

test('16 teams expose four complete naturally ordered rosters and no playoff stage', () => {
  const { page } = load()
  const teams = ['D', 'B', 'A', 'C'].flatMap((group, g) => Array.from({ length: 4 }, (_, i) => team(g * 4 + i + 1, group)))
  const rounds = teams.filter((_, i) => i % 4 === 0).map((t, i) => round(i + 1, t.group_name, t.team_id, t.team_id + 1))
  apply(page, detail(teams, rounds), 9, true)
  assert.equal(page.data.groupCount, 4)
  assert.equal(page.data.hasPlayoffStage, false)
  assert.deepEqual(page.data.challengerOverviewGroups.map(s => s.short_label), ['A组', 'B组', 'C组', 'D组'])
  assert.deepEqual(page.data.challengerOverviewGroups.map(s => s.team_count), [4, 4, 4, 4])
  assert.equal(page.data.myMatchSummary.team_name, '队伍 9')
  assert.equal(page.data.myMatchSummary.group_text, '挑战者组 · A组')
  const own = page.data.groupOverview.find(s => s.is_mine)
  assert.deepEqual(own.teams.map(t => t.team_id), [9, 10, 11, 12])
  assert.deepEqual(own.teams.filter(t => t.is_mine).map(t => t.team_id), [9])
})

test('actual straight-to-legend teams have a separate pending division without speculative counts', () => {
  const { page } = load()
  apply(page, detail([team(1, null, 'legend'), team(2, null, 'legend')]), 1, true)
  assert.equal(page.data.groupCount, 0)
  assert.equal(page.data.legendOverviewGroups.length, 1)
  const section = page.data.legendOverviewGroups[0]
  assert.equal(section.label, '传奇组 · 待分区')
  assert.equal(section.group_name, '')
  assert.equal(section.can_view, false)
  assert.equal(section.team_count, 2)
  assert.match(page.data.myMatchSummary.hint, /已直升传奇组/)
  assert.equal(page.data.myMatchSummary.next_empty_text, page.data.myMatchSummary.hint)
  assert.equal(page.data.myMatchSummary.total_count, 0)
})

test('promotion retains original challenger membership and uses promotion wording while awaiting division', () => {
  const { page } = load()
  apply(page, detail([team(1, null, 'legend'), team(2, 'A', 'eliminated')], [
    round(1, 'A', 1, 2, { status: 'finished', winner_id: 1, team1_score: 13, team2_score: 5 })
  ]), 1, true)
  assert.deepEqual(page.data.challengerOverviewGroups[0].teams.map(t => t.team_id), [2, 1])
  assert.equal(page.data.myMatchSummary.group_text, '传奇组 · 待分区')
  assert.match(page.data.myMatchSummary.hint, /已晋级传奇组/)
  assert.doesNotMatch(page.data.myMatchSummary.hint, /直升/)
  assert.equal(page.data.myMatchSummary.next_empty_text, page.data.myMatchSummary.hint)
})

test('history-only fixtures recover full names and exclude a null bye from group members', () => {
  const { page } = load()
  apply(page, detail([], [round(1, '松林组', 10, 11), round(2, '松林组', 12, null)]), 11, false)
  const section = page.data.groupOverview[0]
  assert.deepEqual(section.teams.map(t => t.team_name), ['队伍 10', '队伍 11', '队伍 12'])
  assert.equal(section.short_label, '松林组')
  assert.equal(page.data.myMatchSummary.team_name, '队伍 11')
  assert.equal(page.data.myRounds[0].group_label, '挑战者组 · 松林组')
})

test('numeric group names sort naturally and prototype-like groups retain every roster field', () => {
  const { page } = load()
  const names = ['A10', 'constructor', 'A2', '__proto__', 'A1']
  apply(page, detail(names.map((name, i) => team(i + 1, name)), names.map((name, i) => round(i + 1, name, i + 1, 99))))
  assert.deepEqual(Object.keys(page.data.challengerSwiper[0].groups).filter(name => name.startsWith('A')), ['A1', 'A2', 'A10'])
  for (const name of names) {
    const section = page.data.groupOverview.find(s => s.group_name === name)
    assert.equal(section.teams.length, 2)
    assert.equal(section.round_count, 1)
    assert.deepEqual(Object.keys(section).sort(), ['key', 'label', 'short_label', 'group_name', 'tab', 'teams', 'team_count',
      'round_count', 'finished_count', 'is_mine', 'can_view', 'hint'].sort())
    assert.equal(page.data.challengerSwiper[0].groups[name].length, 1)
  }
})

test('unassigned approved teams never appear as challenger A and do not offer group round navigation', () => {
  const { page } = load()
  apply(page, detail([{ ...team(1, null), seed: null }]), 1, false)
  assert.equal(page.data.challengerOverviewGroups.length, 0)
  assert.equal(page.data.groupCount, 0)
  assert.equal(page.data.groupOverview[0].tab, 'teams')
  assert.equal(page.data.groupOverview[0].group_name, '')
  assert.equal(page.data.groupOverview[0].can_view, false)
  assert.equal(page.data.myMatchSummary.group_text, '待分组')
})

test('a second-side participant sees own score and each BO3 map before the opponent', () => {
  const { page } = load()
  const value = detail([team(2, '淘汰赛', 'playoff')], [round(1, '淘汰赛', 1, 2, {
    status: 'finished', winner_id: 2, team1_score: 1, team2_score: 2,
    bo3_scores: [{ t1: 13, t2: 9 }, { t1: 5, t2: 13 }, { t1: 7, t2: 13 }]
  })])
  apply(page, value, '2', true)
  const own = page.data.myRounds[0]
  assert.equal(own.team2_is_mine, true)
  assert.equal(own.my_score, 2)
  assert.equal(own.opponent_score, 1)
  assert.equal(own.opponent_name, '队伍 1')
  assert.equal(own.result_text, '胜')
  assert.equal(own.bo3_text, '9-13 / 13-5 / 13-7')
  page.showBo3(click({ id: 1 }))
  assert.equal(page.data.showBo3, true)
  assert.deepEqual(page.data.bo3Detail.games, value.rounds[0].bo3_scores)
})

test('duel display preserves actual team order while the personal display follows own side', () => {
  const { page } = load()
  const source = round(1, 'A', 1, 2, { team1_name: 'Team SumitaMana', team2_name: '六道口泥头车青训' })
  const value = detail([team(1), team(2)], [source])
  apply(page, value, 2, true)
  const displayed = page.data.allRounds[0]
  assert.equal(displayed.team1_display_name, 'Team SumitaMana')
  assert.equal(displayed.team2_display_name, '六道口泥头车青训')
  assert.equal(displayed.team1_initial, 'TE')
  assert.equal(displayed.team2_initial, '六')
  assert.equal(displayed.my_team_name, '六道口泥头车青训')
  assert.equal(displayed.my_team_initial, '六')
  assert.equal(displayed.opponent_name, 'Team SumitaMana')
  assert.equal(displayed.opponent_initial, 'TE')
  assert.deepEqual(value.rounds, [source], 'rendering never changes server round data')
  const visitor = page.decorateRound(source, null, false)
  assert.equal(visitor.my_team_name, '')
  assert.equal(visitor.my_team_initial, '')
  assert.equal(visitor.opponent_initial, '')
})

test('duel placeholders distinguish unnamed real teams, equal names and byes without splitting Unicode', () => {
  const { page } = load()
  const unnamed = page.decorateRound(round(1, 'A', 1, 2, { team1_name: '   ', team2_name: null }), 1, true)
  assert.equal(unnamed.team1_display_name, '队伍 1')
  assert.equal(unnamed.team2_display_name, '队伍 2')
  assert.equal(unnamed.opponent_name, '队伍 2')
  assert.equal(unnamed.opponent_initial, '队')
  const equalNames = page.decorateRound(round(2, 'A', 1, 2, { team1_name: '  🐼战队 ', team2_name: '🐼战队' }), 2, true)
  assert.equal(equalNames.team1_initial, '🐼')
  assert.equal(equalNames.team2_initial, '🐼')
  assert.equal(equalNames.team1_is_mine, false)
  assert.equal(equalNames.team2_is_mine, true)
  assert.equal(equalNames.my_team_name, '🐼战队')
  const bye = page.decorateRound(round(3, 'A', null, 2, { team1_name: '过期名称', team2_name: 'xy队伍' }), 2, true)
  assert.equal(bye.team1_display_name, '轮空')
  assert.equal(bye.team1_initial, '—')
  assert.equal(bye.my_team_initial, 'XY')
  assert.equal(bye.opponent_name, '轮空')
  assert.equal(bye.opponent_initial, '—')
  assert.equal(bye.can_chat, false)
  assert.equal(Object.hasOwn(bye, 'can_operate'), false)
})

test('six-team bracket connects the true cross-division order and labels ungenerated stages only as dependencies', () => {
  const { page } = load()
  const rounds = knockoutRounds().slice(0, 2).map(r => ({ ...r, status: 'pending', winner_id: null }))
  const original = plain(rounds)
  apply(page, detail([], rounds), 6, true)
  const columns = page.data.knockoutBracketColumns
  assert.deepEqual(columns.map(c => [c.key, c.title, c.format, c.nodes.length]), [
    ['quarter', '1/4决赛', 'BO3', 2], ['semi', '半决赛', 'BO3', 2], ['final', '决赛', 'BO3', 1]
  ])
  assert.deepEqual(columns[0].nodes.map(n => n.round.id), [21, 20], 'second quarterfinal winner feeds the upper semifinal')
  assert.deepEqual(columns[0].nodes.map(n => n.label), ['第二场', '第一场'], 'labels follow match identity rather than division')
  assert.equal(columns[0].nodes[0].team1_name, '队伍 4')
  assert.equal(columns[0].nodes[0].team2_name, '队伍 6')
  assert.equal(columns[0].nodes[0].round.can_chat, true)
  assert.equal(Object.hasOwn(columns[0].nodes[0].round, 'can_operate'), false)
  assert.deepEqual(columns[1].nodes.map(n => [n.team1_name, n.team2_name]), [
    ['上区第1', '第二场胜者'], ['下区第1', '第一场胜者']
  ])
  const placeholders = columns.slice(1).flatMap(c => c.nodes)
  assert.equal(placeholders.every(n => n.is_placeholder && n.round === null && n.status_text === '尚未生成' &&
    !Object.hasOwn(n, 'id') && !Object.hasOwn(n, 'can_operate')), true)
  assert.deepEqual(rounds, original, 'building a display tree does not add future rounds to source data')
  assert.equal(page.data.allRounds.length, 2)
  assert.equal(page.data.roundCount, 2)
  assert.equal(page.data.knockoutGroups.length, 1)
  assert.deepEqual(page.data.knockoutGroups[0].rounds.map(r => r.id), [20, 21], 'list fallback retains server order')
  assert.deepEqual(page.data.knockoutGroups[0].rounds.map(r => r.ko_label), ['第一场', '第二场'])
})

test('real semifinals and final replace placeholders while map scores retain the server side order', () => {
  const { page } = load()
  const rounds = knockoutRounds()
  rounds[2] = { ...rounds[2], team1_score: 2, team2_score: 1,
    bo3_scores: [{ t1: 13, t2: 5 }, { t1: 4, t2: 13 }, { t1: 13, t2: 10 }] }
  apply(page, detail([], rounds.slice(0, 4)), 6, true)
  let columns = page.data.knockoutBracketColumns
  assert.deepEqual(columns[1].nodes.map(n => n.round.id), [22, 23])
  assert.deepEqual(columns[1].nodes.map(n => n.label), ['上区第1 vs 第二场胜者', '下区第1 vs 第一场胜者'])
  assert.deepEqual(page.data.knockoutGroups[1].rounds.map(r => r.ko_label), ['上区第1 vs 第二场胜者', '下区第1 vs 第一场胜者'])
  assert.equal(columns[1].nodes[0].team1_name, '队伍 1')
  assert.equal(columns[1].nodes[0].team2_name, '队伍 6')
  assert.equal(columns[1].nodes[0].round.bo3_text, '13-5 / 4-13 / 13-10')
  assert.equal(page.data.myRounds.find(r => r.id === 22).bo3_text, '5-13 / 13-4 / 10-13')
  assert.equal(columns[2].nodes[0].is_placeholder, true)
  apply(page, detail([], rounds), 2, false)
  columns = page.data.knockoutBracketColumns
  assert.equal(columns[2].nodes[0].is_placeholder, false)
  assert.equal(columns[2].nodes[0].round.id, 24)
  assert.equal(columns[2].nodes[0].team1_name, '队伍 1')
  assert.equal(columns[2].nodes[0].team2_name, '队伍 2')
  assert.equal(columns[2].nodes[0].status_text, '待比赛')
  assert.equal(columns[2].nodes[0].round.can_chat, false)
})

test('missing bracket slots and malformed real rounds fall back to original lists without guessed positions', () => {
  const { page } = load()
  const rounds = knockoutRounds()
  const cases = [[], rounds.slice(0, 1), rounds.slice(0, 3), rounds.concat(round(25, '淘汰赛', 8, 9)),
    [{ ...rounds[0], id: null }, rounds[1]],
    [rounds[0], { ...rounds[1], id: rounds[0].id }],
    [rounds[0], { ...rounds[1], round_number: rounds[0].round_number }],
    [{ ...rounds[0], round_number: null }, rounds[1]],
    [{ ...rounds[0], team2_id: 3 }, rounds[1]],
    [{ ...rounds[0], team2_id: null }, rounds[1]],
    [rounds[0], { ...rounds[1], team2_id: 3 }]]
  for (const source of cases) {
    const before = plain(source)
    apply(page, detail([], source))
    assert.deepEqual(page.data.knockoutBracketColumns, [])
    assert.equal(page.data.allRounds.length, source.length)
    assert.equal(page.data.knockoutGroups.reduce((count, g) => count + g.rounds.length, 0), Math.min(source.length, 5))
    assert.deepEqual(source, before)
  }
})

test('inconsistent or unresolved knockout advancement never draws a misleading path', () => {
  const { page } = load()
  const rounds = knockoutRounds()
  const cases = [
    rounds.slice(0, 4).map((r, i) => i === 0 ? { ...r, status: 'pending' } : r),
    rounds.slice(0, 4).map((r, i) => i === 1 ? { ...r, winner_id: 99 } : r),
    rounds.slice(0, 4).map((r, i) => i === 2 ? { ...r, team2_id: 4 } : r),
    rounds.slice(0, 4).map((r, i) => i === 3 ? { ...r, team1_id: 1 } : r),
    rounds.slice(0, 4).map((r, i) => i === 2 ? { ...r, team1_id: 5 } : r),
    rounds.map((r, i) => i === 3 ? { ...r, status: 'pending' } : r),
    rounds.map((r, i) => i === 4 ? { ...r, team1_id: 6 } : r),
    rounds.map((r, i) => i === 4 ? { ...r, team1_id: 2, team2_id: 1 } : r)
  ]
  for (const source of cases) {
    apply(page, detail([], source))
    assert.deepEqual(page.data.knockoutBracketColumns, [])
    assert.equal(page.data.knockoutGroups.reduce((count, g) => count + g.rounds.length, 0), source.length)
  }
})

test('unfinished rounds with stale winner fields never count as wins and invalid finished winners stay unresolved', () => {
  const { page } = load()
  apply(page, detail([team(1)], [round(1, 'A', 1, 2, { winner_id: 1 }),
    round(2, 'A', 1, 3, { status: 'in_progress', winner_id: 1 }),
    round(3, 'A', 1, 4, { status: 'finished', winner_id: 999 }),
    round(4, 'A', 1, 5, { status: 'finished', winner_id: 5 })]), 1, false)
  assert.deepEqual(page.data.myRounds.map(r => r.result_text), ['待比赛', '进行中', '已结束', '负'])
})

test('members see personal rounds while captain contact stays restricted and byes offer no contact', async () => {
  const { page, calls } = load()
  const value = detail([team(1)], [round(1), round(2, 'A', 1, null)])
  apply(page, value, 1, false)
  assert.equal(page.data.myRounds.length, 2)
  assert.equal(page.data.myRounds.every(r => r.is_mine && !Object.hasOwn(r, 'can_operate') && !r.can_chat), true)
  await page.contactRound(click({ id: 1 }))
  assert.equal(calls.chat.length, 0)
  apply(page, value, 1, true)
  assert.equal(page.data.myRounds[0].can_chat, true)
  assert.equal(page.data.myRounds[1].result_text, '轮空')
  assert.equal(page.data.myRounds[1].can_chat, false)
  assert.equal(Object.hasOwn(page.data.myRounds[1], 'can_operate'), false)
})

test('personal rounds prioritize formally scheduled pending matches, then stable round order, then results', () => {
  const { page } = load()
  apply(page, detail([team(1)], [round(1, 'A', 1, 2, { status: 'finished', winner_id: 2 }),
    round(5, 'A', 1, 5), round(4, 'A', 1, 4, { scheduled_time: '2026-10-12T12:00:00', schedule_status: 'confirmed' }),
    round(3, 'A', 1, 3, { scheduled_time: '2026-10-11T12:00:00', schedule_status: 'confirmed' }), round(2, 'A', 1, 6)]), 1, true)
  assert.deepEqual(page.data.myRounds.map(r => r.id), [3, 4, 2, 5, 1])
  assert.equal(page.data.myRoundsPendingCount, 4)
  assert.equal(page.data.myMatchSummary.opponent_text, '队伍 3、队伍 4、队伍 6、队伍 5')
  assert.doesNotMatch(page.data.myMatchSummary.opponent_text, /队伍 2/)
})

test('own participation is required for a personal summary, independent of merely owning another team', () => {
  const { page } = load()
  apply(page, detail([team(1)], [round(1)]), 99, true)
  assert.equal(page.data.myMatchSummary, null)
  assert.deepEqual(page.data.myRounds, [])
  apply(page, detail([team(1)], [round(1)]), null, false)
  assert.equal(page.data.groupOverview[0].is_mine, false)
  assert.equal(page.data.myMatchSummary, null)
})

test('stage labels distinguish every knockout step and only confirmed administrator times are shown', () => {
  const { page } = load()
  const rounds = Array.from({ length: 5 }, (_, i) => round(i + 1, '淘汰赛', 1, i + 2))
  apply(page, detail([team(1, '淘汰赛', 'playoff')], rounds), 1, true)
  assert.deepEqual(page.data.myRounds.map(r => r.stage_label), ['淘汰赛 · 1/4决赛', '淘汰赛 · 1/4决赛',
    '淘汰赛 · 半决赛', '淘汰赛 · 半决赛', '淘汰赛 · 决赛'])
  for (const values of [{}, { schedule_status: 'pending', scheduled_time: '2026-10-10T13:00:00' },
    { schedule_status: 'pending', scheduled_time: '2026-10-10T13:00:00', team1_confirmed: true },
    { schedule_status: 'confirmed' }]) {
    const displayed = page.decorateRound(round(1, 'A', 1, 2, values), 1, true)
    assert.equal(displayed.schedule_text, '待管理员安排')
    assert.equal(Object.hasOwn(displayed, 'schedule_action'), false)
    assert.equal(Object.hasOwn(displayed, 'can_operate'), false)
    assert.equal(Object.hasOwn(displayed, 'i_confirmed'), false)
  }
  const confirmed = page.decorateRound(round(1, 'A', 1, 2,
    { schedule_status: 'confirmed', scheduled_time: '2026-10-10T13:00:00' }), 1, false)
  assert.equal(confirmed.schedule_text, '比赛时间 10-10 13:00')
})

test('selecting an assigned group without rounds reports zero, invalid names reset, and refresh removes stale filters', () => {
  const { page } = load()
  apply(page, detail([team(1, 'A'), team(2, 'B')], [round(1, 'A', 1, 3)]))
  page.openGroupRounds(click({ tab: 'challenger', group: 'B' }))
  assert.equal(page.data.tab, 'challenger')
  assert.equal(page.data.selectedChallengerGroup, 'B')
  assert.equal(page.data.selectedChallengerRoundCount, 0)
  page.selectChallengerGroup(click({ group: 'not-existing' }))
  assert.equal(page.data.selectedChallengerGroup, '')
  assert.equal(page.data.selectedChallengerRoundCount, 1)
  page.selectChallengerGroup(click({ group: 'B' }))
  apply(page, detail([team(1, 'A')], [round(1, 'A', 1, 3)]))
  assert.equal(page.data.selectedChallengerGroup, '')
  assert.equal(page.data.selectedChallengerRoundCount, 1)
})

test('group links select the matching legend division and cannot spoof an unrelated stage', () => {
  const { page } = load()
  apply(page, detail([team(1, '下区', 'legend'), team(2, 'A')]))
  page.openGroupRounds(click({ tab: 'legend', group: '下区' }))
  assert.equal(page.data.legendStage, 1)
  assert.equal(page.data.tab, 'legend')
  page.openGroupRounds(click({ tab: 'knockout', group: '下区' }))
  assert.equal(page.data.tab, 'legend')
})

test('four-group historical playoff rosters stay visible as history without navigating to a removed stage', () => {
  const { page } = load()
  const value = detail(['A', 'B', 'C', 'D'].map((group, i) => team(i + 1, group)), [round(5, '附加赛', 1, 2)])
  apply(page, value)
  const old = page.data.groupOverview.find(s => s.group_name === '附加赛')
  assert.equal(old.round_count, 1)
  assert.equal(old.can_view, false)
  assert.match(old.hint, /历史/)
  page.openGroupRounds(click({ tab: 'challenger', group: '附加赛' }))
  assert.equal(page.data.tab, 'teams')
})

test('a historical playoff notification cannot navigate a four-group event to unrelated group games', () => {
  const { page, calls } = load()
  const value = detail(['A', 'B', 'C', 'D'].map((group, i) => team(i + 1, group)), [
    round(1, 'A', 1, 2), round(5, '附加赛', 1, 2)
  ])
  apply(page, value, 1, true)
  page.switchTab(click({ tab: 'legend' }))
  page._notificationRoundId = 5
  page.openNotificationRound()
  assert.equal(page.data.hasPlayoffStage, false)
  assert.equal(page.data.tab, 'legend', 'an obsolete stage must not replace the selected page')
  assert.equal(page.data.focusedRoundId, null)
  assert.deepEqual(calls.scrolls, [])
  assert.equal(calls.toasts[0].title, '本赛事已不设附加赛，该通知仅作历史记录')
  assert.equal(page._notificationRoundId, null, 'the consumed historical link must not retry on refresh')
})

test('the user match page has no scheduling state or handlers for captains, members or visitors', () => {
  for (const [id, captain] of [[1, true], [1, false], [null, false]]) {
    const { page } = load()
    apply(page, detail([team(1)], [round(1)]), id, captain)
    for (const name of ['showSchedule', 'scheduleRound', 'scheduleDate', 'scheduleTime']) {
      assert.equal(Object.hasOwn(page.data, name), false, name)
    }
    for (const name of ['openSchedule', 'closeSchedule', 'currentScheduleRound', 'submitSchedule', 'confirmSchedule', 'rejectSchedule']) {
      assert.equal(typeof page[name], 'undefined', name)
    }
    assert.equal(page.roundActions(page.data.allRounds[0]).some(action => action.key === 'schedule'), false)
  }
})

test('notification links select and scroll to their exact challenger match without captain actions', () => {
  const { page, calls } = load()
  apply(page, detail([team(1, 'B')], [round(1, 'A', 5, 6), round(2, 'B', 1, 2)]), 1, true)
  page.selectChallengerGroup(click({ group: 'A' }))
  page._notificationRoundId = 2
  page.openNotificationRound()
  assert.equal(page.data.selectedChallengerGroup, 'B')
  assert.equal(page.data.selectedChallengerRoundCount, 1)
  assert.equal(page.data.challengerStage, 0)
  assert.equal(page.data.tab, 'challenger')
  assert.equal(Object.hasOwn(page.data, 'showSchedule'), false)
  assert.ok(calls.scrolls.some(value => value.selector === '#round-2'))
  assert.equal(calls.toasts.length, 0)
})

test('initial onShow shares onLoad, later page returns refresh both details and membership', async () => {
  const pending = deferred()
  let requests = 0, teamId = 1
  const { page } = load({ getMatchDetail: () => ++requests === 1 ? pending.promise : Promise.resolve(detail([team(2)], [round(2, 'A', 2, 3)])),
    getMyTeam: async () => ({ id: teamId, captain_id: 10 }), getMe: async () => ({ id: 10 }) })
  const initial = page.onLoad({ id: '7' })
  page.onShow()
  assert.equal(requests, 1)
  pending.resolve(detail([team(1)], [round(1)]))
  await initial
  assert.equal(page.data.myTeamId, 1)
  page.onHide()
  teamId = 2
  await page.onShow()
  assert.equal(requests, 2)
  assert.equal(page.data.myTeamId, 2)
  assert.equal(page.data.myRounds[0].id, 2)
})

test('logout clears all personal controls and summaries while public grouping survives', async () => {
  const { page, storage } = load()
  apply(page, detail([team(1)], [round(1)]), 1, true)
  page._identityToken = 'token-a'
  page._shownOnce = true
  page.data.tab = 'mine'
  page.data.myReg = { team_id: 1 }
  storage.token = ''
  // Logout is cleared synchronously even when the public refresh is still pending.
  const pending = deferred()
  page.refreshDetail = () => pending.promise
  const shown = page.onShow()
  assert.equal(page.data.tab, 'teams')
  assert.equal(page.data.myTeamId, null)
  assert.equal(page.data.myReg, null)
  assert.equal(page.data.isCaptain, false)
  assert.equal(page.data.myMatchSummary, null)
  assert.deepEqual(page.data.myRounds, [])
  assert.equal(page.data.groupOverview.length, 1)
  assert.equal(page.data.allRounds[0].can_chat, false)
  assert.equal(Object.hasOwn(page.data.allRounds[0], 'can_operate'), false)
  pending.resolve()
  await shown
})

test('overlapping detail refresh APIs, including pull-down, allow only the newest detail and identity', async () => {
  const older = deferred()
  let calls = 0
  const { page } = load({ getMatchDetail: () => ++calls === 1 ? older.promise : Promise.resolve(detail([team(2, 'B')], [round(2, 'B')])) }, '')
  apply(page, detail())
  const first = page.refreshDetail()
  await page.onPullDownRefresh()
  older.resolve(detail([team(1, 'A')], [round(1)]))
  await first
  assert.equal(page.data.teams[0].team_id, 2)
  assert.deepEqual(page.data.challengerOverviewGroups.map(s => s.group_name), ['B'])
})

test('hidden and unloaded pages ignore late detail requests and do not render them later', async () => {
  for (const action of ['onHide', 'onUnload']) {
    const pending = deferred()
    const { page } = load({ getMatchDetail: () => pending.promise }, '')
    apply(page, detail([team(2, 'B')]))
    const refresh = page.refreshDetail()
    page[action]()
    pending.resolve(detail([team(1, 'A')]))
    await refresh
    assert.equal(page.data.teams[0].team_id, 2)
  }
})

test('an old identity response cannot restore a captain after a token change', async () => {
  const pending = deferred()
  const { page, storage } = load({ getMyRegistration: () => pending.promise,
    getMyTeam: async () => ({ id: 1, captain_id: 10 }), getMe: async () => ({ id: 10 }) })
  apply(page, detail([team(1)], [round(1)]), 1, true)
  const status = page.loadMyStatus()
  storage.token = 'token-b'
  pending.resolve({ registered: true, registration: { team_id: 1 } })
  await status
  assert.equal(page.data.myTeamId, null)
  assert.equal(page.data.isCaptain, false)
  assert.equal(page.data.myMatchSummary, null)
})

test('a stale token detail response cannot clear a newer account that already completed its refresh', async () => {
  const pending = deferred()
  let requests = 0
  const { page, storage } = load({ getMatchDetail: () => ++requests === 1 ? pending.promise : Promise.resolve(detail([team(2, 'B')], [round(2, 'B', 2, 3)])),
    getMyTeam: async () => ({ id: 2, captain_id: 20 }), getMe: async () => ({ id: 20 }) })
  apply(page, detail())
  const old = page.refreshDetail()
  storage.token = 'token-b'
  await page.refreshDetail()
  assert.equal(page.data.myTeamId, 2)
  pending.resolve(detail([team(1)], [round(1)]))
  await old
  assert.equal(page.data.myTeamId, 2)
  assert.equal(page.data.myMatchSummary.team_name, '队伍 2')
})

test('double round encounters use the same opponent pair and local group positions, never global round numbers', () => {
  const { page } = load()
  apply(page, detail([team(1, 'A'), team(2, 'A'), team(3, 'A'), team(4, 'B'), team(5, 'B')], [
    round(150, 'A', 2, 1, { round_number: 150 }),
    round(135, 'B', 4, 5, { round_number: 135 }),
    round(134, 'A', 1, 3, { round_number: 134 }),
    round(149, 'A', 3, 1, { round_number: 149 }),
    round(133, 'A', 1, 2, { round_number: 133 }),
    round(151, 'B', 5, 4, { round_number: 151 })
  ]), 1, true)
  const groups = page.data.challengerSwiper[0].groups
  assert.deepEqual(groups.A.map(r => r.group_match_index), [1, 2, 3, 4])
  assert.deepEqual(groups.A.map(r => r.group_round_label), ['第1次交手', '第1次交手', '第2次交手', '第2次交手'])
  assert.deepEqual(groups.A.map(r => r.group_match_text), ['本组第 1 场', '本组第 2 场', '本组第 3 场', '本组第 4 场'])
  assert.deepEqual(groups.B.map(r => r.group_match_index), [1, 2])
  assert.deepEqual(groups.B.map(r => r.group_round_label), ['第1次交手', '第2次交手'])
  assert.equal(page.data.myMatchSummary.next_round.group_match_index, 1)
  assert.equal(page.data.myMatchSummary.next_round.group_round_label, '第1次交手')
})

test('single, excessive, self, bye and other-stage fixtures do not invent a double round encounter label', () => {
  const { page } = load()
  apply(page, detail([team(1)], [
    round(1), round(2, 'A', 1, 3), round(3, 'A', 3, 1), round(4, 'A', 1, 3),
    round(5, 'A', 1, null), round(6, 'A', 1, 1), round(7, '上区', 1, 2), round(8, '上区', 2, 1),
    round(9, '附加赛', 1, 2), round(10, '附加赛', 2, 1), round(11, '淘汰赛', 1, 2)
  ]), 1, true)
  assert.equal(page.data.allRounds.every(r => r.group_round_label === ''), true)
  assert.equal(page.data.allRounds.filter(r => ['上区', '附加赛', '淘汰赛'].includes(r.group_name)).every(r =>
    r.group_match_index === null && r.group_match_text === ''), true)
  assert.equal(page.data.challengerSwiper[0].groups.A.length, 6)
})

test('next match summary prioritizes an active opponent and shows only administrator-confirmed times', () => {
  const { page } = load()
  const value = detail([team(1)], [
    round(1, 'A', 1, null),
    round(2, 'A', 1, 2, { scheduled_time: '2026-10-10T13:00:00', schedule_status: 'pending' }),
    round(3, 'A', 3, 1, { status: 'in_progress' })
  ])
  apply(page, value, 1, true)
  let summary = page.data.myMatchSummary
  assert.equal(summary.next_label, '正在比赛')
  assert.equal(summary.next_round.id, 3)
  assert.equal(summary.next_round.opponent_name, '队伍 3')
  assert.equal(summary.next_round.stage_label, '挑战者组 · A组')
  assert.equal(summary.notice_title, '本队有比赛正在进行')
  assert.equal(Object.hasOwn(summary.next_round, 'can_operate'), false)
  assert.equal(summary.next_round.can_chat, true)
  apply(page, { ...value, rounds: value.rounds.filter(r => r.id !== 3) }, 1, true)
  summary = page.data.myMatchSummary
  assert.equal(summary.next_label, '下一场对阵')
  assert.equal(summary.next_round.id, 2)
  assert.equal(summary.next_round.schedule_text, '待管理员安排')
  assert.equal(summary.notice_title, '本队有待赛对阵，查看赛程')
  assert.equal(Object.hasOwn(summary.next_round, 'schedule_action'), false)
  assert.equal(Object.hasOwn(summary.next_round, 'can_operate'), false)
  apply(page, { ...value, rounds: [{ ...value.rounds[1], schedule_status: 'confirmed' }] }, 1, true)
  assert.equal(page.data.myMatchSummary.next_round.schedule_text, '比赛时间 10-10 13:00')
  assert.equal(page.data.myMatchSummary.notice_title, '比赛时间已安排，查看对阵')
  apply(page, { ...value, rounds: value.rounds.filter(r => r.id !== 3) }, 1, false)
  assert.equal(Object.hasOwn(page.data.myMatchSummary.next_round, 'can_operate'), false)
  assert.equal(page.data.myMatchSummary.next_round.can_chat, false)
})

test('old captain proposals neither outrank formal match times nor request confirmation', () => {
  const { page } = load()
  apply(page, detail([team(1)], [
    round(1, 'A', 1, 2, { schedule_status: 'pending', scheduled_time: '2026-10-01T13:00:00' }),
    round(2, 'A', 1, 3, { schedule_status: 'confirmed', scheduled_time: '2026-10-12T13:00:00' })
  ]), 1, true)
  assert.deepEqual(page.data.myRounds.map(r => r.id), [2, 1])
  assert.equal(page.data.myMatchSummary.next_round.id, 2)
  assert.equal(page.data.myMatchSummary.notice_title, '比赛时间已安排，查看对阵')
  assert.equal(page.data.myRounds[1].schedule_text, '待管理员安排')
  assert.doesNotMatch(page.data.myMatchSummary.hint, /约定|确认|约赛/)
  assert.match(page.data.myMatchSummary.hint, /管理员统一安排/)
})

test('next match never substitutes previous-stage pending fixtures for promoted or eliminated teams', () => {
  const { page } = load()
  const rounds = [round(1, 'A', 1, 2, { status: 'in_progress' }), round(2, '上区', 1, 3), round(3, '淘汰赛', 1, 4)]
  apply(page, detail([team(1, null, 'legend')], rounds), 1, true)
  assert.equal(page.data.myMatchSummary.next_round, null)
  assert.match(page.data.myMatchSummary.next_empty_text, /等待传奇组分区/)
  assert.doesNotMatch(page.data.myMatchSummary.notice_title, /正在进行|已安排/)
  apply(page, detail([team(1, '上区', 'legend')], rounds), 1, true)
  assert.equal(page.data.myMatchSummary.next_round.id, 2)
  apply(page, detail([team(1, '上区', 'playoff')], rounds), 1, true)
  assert.equal(page.data.myMatchSummary.next_round.id, 3)
  apply(page, detail([team(1, 'A', 'eliminated')], rounds), 1, true)
  assert.equal(page.data.myMatchSummary.next_round, null)
  assert.match(page.data.myMatchSummary.next_empty_text, /已出局/)
  assert.equal(page.data.myMatchSummary.notice_title, '本队已出局，查看战绩')
})

test('three-group playoff remains a current challenger fixture while byes and completed matches yield no next opponent', () => {
  const { page } = load()
  apply(page, detail([team(1, '附加赛'), team(4, 'B'), team(5, 'C')], [round(1, 'A', 1, 2), round(2, '附加赛', 1, 3)]), 1, true)
  assert.equal(page.data.myMatchSummary.next_round.id, 2)
  apply(page, detail([team(1)], [round(1, 'A', 1, null), round(2, 'A', 1, 2, { status: 'finished', winner_id: 1 }), round(3, 'A', 1, 1)]), 1, true)
  assert.equal(page.data.myMatchSummary.next_round, null)
  assert.equal(page.data.myMatchSummary.next_label, '')
  assert.match(page.data.myMatchSummary.next_empty_text, /暂无待赛/)
})

test('participants and guests first land on the public overview while only participants get a reminder', async () => {
  const value = detail([team(1)], [round(1)])
  const { page } = load({ getMatchDetail: async () => value, getMyTeam: async () => ({ id: 1, captain_id: 10 }),
    getMe: async () => ({ id: 10 }) })
  await page.onLoad({ id: '7' })
  assert.equal(page.data.tab, 'teams')
  assert.ok(page.data.myMatchSummary.notice_title)
  const visitor = load({}, '').page
  apply(visitor, value)
  assert.equal(visitor.data.tab, 'teams')
  assert.equal(visitor.data.myMatchSummary, null)
})

test('manual tabs and group choices survive membership refreshes, including before first identity response', async () => {
  const value = detail([team(1)], [round(1)])
  const identity = deferred()
  const { page } = load({ getMatchDetail: async () => value, getMyTeam: () => identity.promise, getMe: async () => ({ id: 10 }) })
  const request = page.onLoad({ id: '7' })
  await new Promise(resolve => setImmediate(resolve))
  page.switchTab(click({ tab: 'challenger' }))
  identity.resolve({ id: 1, captain_id: 10 })
  await request
  assert.equal(page.data.tab, 'challenger')
  apply(page, value, 1, true)
  assert.equal(page.data.tab, 'challenger')
  page.switchTab(click({ tab: 'teams' }))
  apply(page, value, 1, true)
  assert.equal(page.data.tab, 'teams')
  const filtered = load().page
  apply(filtered, value)
  filtered.openGroupRounds(click({ tab: 'challenger', group: 'A' }))
  apply(filtered, value, 1, true)
  assert.equal(filtered.data.tab, 'challenger')
})

test('a notification chooses its exact fixture instead of auto-opening mine and keeps that destination on refresh', async () => {
  const value = detail([team(1, 'B')], [round(1, 'A', 2, 3), round(2, 'B', 1, 4)])
  const { page } = load({ getMatchDetail: async () => value, getMyTeam: async () => ({ id: 1, captain_id: 10 }),
    getMe: async () => ({ id: 10 }) })
  await page.onLoad({ id: '7', roundId: '2' })
  assert.equal(page.data.tab, 'challenger')
  assert.equal(page.data.selectedChallengerGroup, 'B')
  assert.equal(page.data.focusedRoundId, 2)
  assert.equal(Object.hasOwn(page.data, 'scheduleRound'), false)
  await page.refreshDetail()
  assert.equal(page.data.tab, 'challenger')
})

test('leaving a tournament clears mine and an account switch never forces personal fixtures open', () => {
  const { page, storage } = load()
  const value = detail([team(1)], [round(1)])
  apply(page, value, 1, true)
  assert.equal(page.data.tab, 'teams')
  page.switchTab(click({ tab: 'mine', jump: 'true' }))
  assert.equal(page.data.tab, 'mine')
  apply(page, value, 99, false)
  assert.equal(page.data.tab, 'teams')
  assert.equal(page.data.myMatchSummary, null)
  page.switchTab(click({ tab: 'legend' }))
  storage.token = 'token-b'
  apply(page, value, 1, false)
  assert.equal(page.data.tab, 'legend', 'refreshing another identity cannot force the personal list open')
  page.switchTab(click({ tab: 'mine', jump: 'true' }))
  assert.equal(page.data.tab, 'mine')
  storage.token = ''
  page.clearMyStatus()
  assert.equal(page.data.tab, 'teams')
  assert.equal(page.data.myMatchSummary, null)
})

test('the personal reminder scrolls only after rendering; regular tabs and refreshes keep the viewport', () => {
  const { page, calls } = load()
  const value = detail([team(1)], [round(1)])
  apply(page, value, 1, true)
  assert.deepEqual(calls.scrolls, [], 'initial data rendering must not move the viewport')
  const rendered = []
  page.setData = (values, callback) => {
    Object.assign(page.data, plain(values))
    if (callback) rendered.push(callback)
  }
  page.switchTab(click({ tab: 'mine', jump: 'true' }))
  assert.equal(page.data.tab, 'mine')
  assert.equal(calls.scrolls.length, 0, 'wait for setData to render the selected content')
  assert.equal(rendered.length, 1)
  rendered.shift()()
  assert.deepEqual(calls.scrolls, [{ selector: '#round-1', duration: 180 }])
  page.switchTab(click({ tab: 'teams' }))
  page.switchTab(click({ tab: 'teams', jump: 'false' }))
  page.buildData(value, 1, true)
  assert.equal(rendered.length, 0)
  assert.equal(calls.scrolls.length, 1, 'regular selection and refreshed data must not steal scrolling')
})

test('valid group navigation scrolls after rendering and invalid or spoofed group links do not scroll', () => {
  const { page, calls } = load()
  apply(page, detail([team(1, 'A'), team(2, '下区', 'legend')], [round(1, 'A', 1, 3)]))
  const rendered = []
  page.setData = (values, callback) => {
    Object.assign(page.data, plain(values))
    if (callback) rendered.push(callback)
  }
  page.openGroupRounds(click({ tab: 'challenger', group: 'A' }))
  assert.equal(page.data.tab, 'challenger')
  assert.equal(page.data.selectedChallengerGroup, 'A')
  assert.equal(calls.scrolls.length, 0)
  rendered.shift()()
  assert.deepEqual(calls.scrolls, [{ selector: '#match-content', duration: 180 }])
  page.openGroupRounds(click({ tab: 'legend', group: 'not-existing' }))
  page.openGroupRounds(click({ tab: 'knockout', group: '下区' }))
  assert.equal(rendered.length, 0)
  assert.equal(calls.scrolls.length, 1)
  assert.equal(page.data.tab, 'challenger')
})

test('group captain operations directly contact the opponent regardless of formal or legacy schedule status', async () => {
  for (const values of [{}, { schedule_status: 'pending' },
    { schedule_status: 'pending', team1_confirmed: true }, { schedule_status: 'confirmed' }]) {
    const { page, calls } = load()
    apply(page, detail([team(1)], [round(1, 'A', 1, 2, values)]), 1, true)
    page.onShow()
    await page.openRoundActions(click({ id: 1 }))
    assert.equal(calls.actionSheets.length, 0, 'the single chat action executes directly')
    assert.equal(Object.hasOwn(page.data, 'showSchedule'), false)
    assert.equal(calls.chat.length, 1)
    assert.deepEqual(calls.chat[0].slice(0, 3), [1, 'round', true])
  }
})

test('a knockout captain has one menu for contact and maps using the original handlers', async () => {
  for (const selected of [0, 1]) {
    const { page, calls } = load()
    apply(page, detail([team(1, '淘汰赛', 'playoff')], [round(1, '淘汰赛')]), 1, true)
    page.onShow()
    const choice = page.openRoundActions(click({ id: 1 }))
    assert.deepEqual(plain(calls.actionSheets[0].itemList), ['联系对方队长', '小局详情'])
    calls.actionSheets[0].success({ tapIndex: selected })
    await choice
    assert.equal(Object.hasOwn(page.data, 'showSchedule'), false)
    if (selected === 0) {
      assert.equal(calls.chat.length, 1)
      assert.deepEqual(calls.chat[0].slice(0, 3), [1, 'round', true])
      assert.equal(page.data.showBo3, false)
    } else {
      assert.equal(calls.chat.length, 0)
      assert.equal(page.data.showBo3, true)
      assert.equal(page.data.bo3Detail.team1_name, '队伍 1')
    }
  }
})

test('a knockout member directly opens maps without gaining captain actions, and unrelated group or bye rows expose no menu', async () => {
  const { page, calls } = load()
  const value = detail([team(1, '淘汰赛', 'playoff')], [round(1, '淘汰赛'), round(2, 'A', 1, null), round(3, 'A', 4, 5)])
  apply(page, value, 1, false)
  page.onShow()
  await page.openRoundActions(click({ id: 1 }))
  assert.equal(calls.actionSheets.length, 0, 'the only permitted action executes directly')
  assert.equal(page.data.showBo3, true)
  assert.equal(Object.hasOwn(page.data, 'showSchedule'), false)
  assert.equal(calls.chat.length, 0)
  page.closeBo3()
  await page.openRoundActions(click({ id: 2 }))
  assert.equal(calls.actionSheets.length, 0)
  assert.equal(page.data.showBo3, false)
  await page.openRoundActions(click({ id: 3 }))
  assert.equal(calls.actionSheets.length, 0)
  assert.equal(page.data.showBo3, false)
})

test('canceling the native operations menu is quiet and dispatches no action', async () => {
  const { page, calls } = load()
  apply(page, detail([team(1, '淘汰赛', 'playoff')], [round(1, '淘汰赛')]), 1, true)
  page.onShow()
  const choice = page.openRoundActions(click({ id: 1 }))
  calls.actionSheets[0].fail({ errMsg: 'showActionSheet:fail cancel' })
  await choice
  assert.equal(Object.hasOwn(page.data, 'showSchedule'), false)
  assert.equal(page.data.showBo3, false)
  assert.equal(calls.chat.length, 0)
  assert.equal(calls.toasts.length, 0)
})

test('a native menu cannot contact after token, visibility, captain membership or fixture participation changes', async () => {
  for (const changed of ['logout', 'switch-account', 'hidden', 'unloaded', 'member', 'left-team', 'unrelated-round', 'deleted']) {
    const { page, calls, storage } = load()
    const value = detail([team(1, '淘汰赛', 'playoff')], [round(1, '淘汰赛')])
    apply(page, value, 1, true)
    page.onShow()
    const choice = page.openRoundActions(click({ id: 1 }))
    assert.equal(calls.actionSheets.length, 1)
    if (changed === 'logout') storage.token = ''
    else if (changed === 'switch-account') storage.token = 'token-b'
    else if (changed === 'hidden') page.onHide()
    else if (changed === 'unloaded') page.onUnload()
    else if (changed === 'member') apply(page, value, 1, false)
    else if (changed === 'left-team') apply(page, value, 99, false)
    else if (changed === 'unrelated-round') apply(page, detail([team(1)], [round(1, '淘汰赛', 3, 4)]), 1, true)
    else apply(page, detail([team(1)], []), 1, true)
    calls.actionSheets[0].success({ tapIndex: 0 })
    await choice
    assert.equal(Object.hasOwn(page.data, 'showSchedule'), false, changed)
    assert.equal(page.data.showBo3, false, changed)
    assert.equal(calls.chat.length, 0, changed)
  }
})

test('finished knockout matches retain captain contact and public map details', async () => {
  const { page, calls } = load()
  const value = detail([team(1, '淘汰赛', 'playoff')], [round(1, '淘汰赛')])
  apply(page, value, 1, true)
  page.onShow()
  const choice = page.openRoundActions(click({ id: 1 }))
  apply(page, detail(value.teams, [{ ...value.rounds[0], status: 'finished', winner_id: 1 }]), 1, true)
  calls.actionSheets[0].success({ tapIndex: 0 })
  await choice
  assert.equal(calls.chat.length, 1)
  const visitor = load({}, '').page
  apply(visitor, page.detail)
  visitor.onShow()
  await visitor.openRoundActions(click({ id: 1 }))
  assert.equal(visitor.data.showBo3, true, 'BO3 map scores are public read access')
})

test('matchup dates include only valid administrator-confirmed days and explicitly separate unarranged games', () => {
  const { page } = load()
  apply(page, detail([team(1)], [
    round(1, 'A', 1, 2, { schedule_status: 'confirmed', scheduled_time: '2026-10-11T14:00:00' }),
    round(2, 'A', 1, 3, { schedule_status: 'confirmed', scheduled_time: '2026-10-10T13:00:00' }),
    round(3, 'A', 1, 4, { schedule_status: 'pending', scheduled_time: '2026-10-09T13:00:00' }),
    round(4, 'A', 1, 5, { schedule_status: 'confirmed', scheduled_time: 'invalid' })
  ]), 1, true)
  page.switchTab(click({ tab: 'matches' }))
  assert.deepEqual(page.data.matchupDates.map(d => d.value), ['', '2026-10-10', '2026-10-11', 'unscheduled'])
  page.changeMatchupDate({ detail: { value: '1' } })
  assert.equal(page.data.matchupCount, 1)
  assert.deepEqual(page.data.matchupSections.flatMap(s => s.rounds.map(r => r.id)), [2])
  page.changeMatchupDate({ detail: { value: '3' } })
  assert.deepEqual(page.data.matchupSections.flatMap(s => s.rounds.map(r => r.id)), [3, 4])
})

test('stage, group, day and own-team filters combine without mixing unrelated opponents', () => {
  const { page } = load()
  const time = { schedule_status: 'confirmed', scheduled_time: '2026-10-10T13:00:00' }
  const value = detail([team(1), team(2), team(3, 'B')], [
    round(1, 'A', 1, 2, time), round(2, 'A', 2, 4, time), round(3, 'B', 1, 3, time),
    round(4, '上区', 1, 5, time), round(5, 'A', 1, 6, { ...time, scheduled_time: '2026-10-11T13:00:00' })
  ])
  const original = plain(value)
  apply(page, value, 1, true)
  page.changeMatchupStage(click({ stage: 'challenger' }))
  page.changeMatchupGroup(click({ group: 'A' }))
  page.toggleMyTeam()
  page.changeMatchupDate({ detail: { value: '1' } })
  assert.equal(page.data.mainTab, 'matches')
  assert.equal(page.data.matchupDate, '2026-10-10')
  assert.deepEqual(page.data.matchupSections.flatMap(s => s.rounds.map(r => r.id)), [1])
  assert.equal(page.data.matchupCount, 1)
  assert.deepEqual(value, original, 'filtering does not reorder or add fields to API records')
  page.toggleMyTeam()
  assert.equal(page.data.matchupDate, '', 'a broader identity filter resets the selected day')
  assert.deepEqual(page.data.matchupSections.flatMap(s => s.rounds.map(r => r.id)), [1, 2, 5])
})

test('filter selections survive refreshes while disappeared groups/dates and personal identity are cleared', () => {
  const { page, storage } = load()
  const value = detail([team(1, 'B'), team(2, 'B')], [round(1, 'B', 1, 2,
    { schedule_status: 'confirmed', scheduled_time: '2026-10-10T13:00:00' })])
  apply(page, value, 1, true)
  page.changeMatchupStage(click({ stage: 'challenger' }))
  page.changeMatchupGroup(click({ group: 'B' }))
  page.toggleMyTeam()
  page.changeMatchupDate({ detail: { value: '1' } })
  page.buildData(value, 1, true)
  assert.equal(page.data.matchupGroup, 'B')
  assert.equal(page.data.matchupDate, '2026-10-10')
  assert.equal(page.data.onlyMyTeam, true)
  page.buildData(detail([team(1, 'A')], [round(2, 'A', 1, 3)]), 1, true)
  assert.equal(page.data.matchupGroup, '')
  assert.equal(page.data.matchupDate, '')
  storage.token = ''
  page.clearMyStatus()
  assert.equal(page.data.onlyMyTeam, false)
  assert.equal(page.data.myMatchSummary, null)
  assert.equal(page.data.matchupSections.flatMap(s => s.rounds).every(r => !r.is_mine && !r.can_chat), true)
})

test('notification positioning resets conflicting day and own-team filters so another group game remains visible', () => {
  const { page, calls } = load()
  apply(page, detail([team(1), team(2, 'B')], [round(1, 'A', 1, 3,
    { schedule_status: 'confirmed', scheduled_time: '2026-10-10T13:00:00' }), round(2, 'B', 2, 4)]), 1, true)
  page.switchTab(click({ tab: 'mine' }))
  page.changeMatchupDate({ detail: { value: '1' } })
  page._notificationRoundId = 2
  page.openNotificationRound()
  assert.equal(page.data.mainTab, 'matches')
  assert.equal(page.data.matchupGroup, 'B')
  assert.equal(page.data.onlyMyTeam, false)
  assert.equal(page.data.matchupDate, '')
  assert.deepEqual(page.data.matchupSections.flatMap(s => s.rounds.map(r => r.id)), [2])
  assert.equal(calls.scrolls.at(-1).selector, '#round-2')
})

test('legacy tab deep links open the new match column after identity loading and explicit round links take priority', async () => {
  const value = detail([team(1, '上区', 'playoff')], [round(1, '淘汰赛', 1, 2)])
  const methods = { getMatchDetail: async () => value, getMyTeam: async () => ({ id: 1, captain_id: 1 }), getMe: async () => ({ id: 1 }) }
  const { page } = load(methods)
  await page.onLoad({ id: 7, tab: 'mine' })
  assert.equal(page.data.mainTab, 'matches')
  assert.equal(page.data.onlyMyTeam, true)
  assert.equal(page.data.matchupCount, 1)
  const { page: linked } = load(methods)
  await linked.onLoad({ id: 7, tab: 'info', roundId: 1 })
  assert.equal(linked.data.mainTab, 'matches')
  assert.equal(linked.data.matchupStage, 'knockout')
  assert.equal(linked.data.focusedRoundId, 1)
  assert.equal(linked._initialTab, '')
})

for (const requestedTab of ['info', 'mine']) {
  test(`a failed ${requestedTab} deep link opens its original destination on the first successful refresh`, async () => {
    let attempts = 0
    const value = detail([team(1)], [round(1)])
    const { page } = load({ getMatchDetail: async () => { if (++attempts === 1) throw Error('offline'); return value },
      getMyTeam: async () => ({ id: 1, captain_id: 1 }), getMe: async () => ({ id: 1 }) })
    await page.onLoad({ id: 7, tab: requestedTab })
    assert.equal(page.data.loadFailed, true)
    assert.equal(page._initialTab, requestedTab)
    await page.onPullDownRefresh()
    assert.equal(page.data.loadFailed, false)
    assert.equal(page.data.mainTab, requestedTab === 'mine' ? 'matches' : 'info')
    assert.equal(page.data.onlyMyTeam, requestedTab === 'mine')
    assert.equal(page._initialTab, '')
  })
}

test('a background-interrupted initial deep link is consumed on return and a late initial response cannot replace it', async () => {
  const first = deferred()
  let attempts = 0
  const value = detail([team(1)], [round(1)])
  const { page } = load({ getMatchDetail: () => ++attempts === 1 ? first.promise : Promise.resolve(value) })
  const initial = page.onLoad({ id: 7, tab: 'info' })
  page.onShow()
  page.onHide()
  await page.onShow()
  assert.equal(page.data.mainTab, 'info')
  assert.equal(page._initialTab, '')
  first.resolve(detail([], []))
  await initial
  assert.equal(page.data.mainTab, 'info')
  assert.equal(page.data.roundCount, 1)
})

test('manual navigation after failure or during initial loading supersedes an unconsumed tab deep link', async () => {
  const value = detail([team(1)], [round(1)])
  let attempts = 0
  const { page } = load({ getMatchDetail: async () => { if (++attempts === 1) throw Error('offline'); return value } })
  await page.onLoad({ id: 7, tab: 'info' })
  page.switchTab(click({ tab: 'teams' }))
  await page.onPullDownRefresh()
  assert.equal(page.data.mainTab, 'teams')
  assert.equal(page._initialTab, '')
  const first = deferred()
  const { page: pending } = load({ getMatchDetail: () => first.promise,
    getMyTeam: async () => ({ id: 1, captain_id: 1 }), getMe: async () => ({ id: 1 }) })
  const work = pending.onLoad({ id: 7, tab: 'mine' })
  pending.switchTab(click({ tab: 'info' }))
  first.resolve(value)
  await work
  assert.equal(pending.data.mainTab, 'info')
  assert.equal(pending.data.onlyMyTeam, false)
  assert.equal(pending._initialTab, '')
})

test('a recovered round deep link takes precedence and discards its initial tab for later refreshes', async () => {
  let attempts = 0
  const value = detail([team(1)], [round(1)])
  const { page } = load({ getMatchDetail: async () => { if (++attempts === 1) throw Error('offline'); return value } })
  await page.onLoad({ id: 7, tab: 'info', roundId: 1 })
  await page.onPullDownRefresh()
  assert.equal(page.data.mainTab, 'matches')
  assert.equal(page.data.focusedRoundId, 1)
  assert.equal(page._initialTab, '')
  await page.refreshDetail()
  assert.equal(page.data.mainTab, 'matches')
})

test('event information has a lightweight route to the existing complete rules page', () => {
  const { page, calls } = load()
  page.goRules()
  assert.deepEqual(calls.navigation, [{ url: '/pages/rules/rules' }])
})
