// Exercise the real match page and WXML with synthetic records and no network.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const test = require('node:test')
const { parseWxml, createRenderer } = require('../../tools/preview-miniprogram.js')

const root = path.resolve(__dirname, '../..')
const pageFile = path.join(root, 'miniprogram/pages/match/match.js')
const templateFile = pageFile.replace(/\.js$/, '.wxml')
const clone = value => JSON.parse(JSON.stringify(value))
const longName = '强起沙鹰特五个，第一把起连狙你怕不怕 LongTeamWithoutSpaces2026'

function loadPage(detail, myTeamId = null, isCaptain = false) {
  let page
  vm.runInNewContext(fs.readFileSync(pageFile, 'utf8'), {
    Page: definition => { page = definition },
    require(name) {
      if (name.endsWith('/tournament.js')) return require(path.resolve(path.dirname(pageFile), name))
      if (name.endsWith('/api.js')) return new Proxy({}, { get(_, key) { throw new Error('No API calls allowed in layout tests: ' + String(key)) } })
      throw new Error('No API calls allowed in layout tests: ' + name)
    },
    wx: { getStorageSync: () => '' }, console
  }, { filename: pageFile })
  page.data = clone(page.data)
  page.setData = values => Object.assign(page.data, clone(values))
  page.detail = clone(detail)
  page.setData({ match: detail.match, myTeamId, isCaptain })
  page.buildData(page.detail, myTeamId, isCaptain)
  return page
}

function render(page, tab, sectionClass = '') {
  if (tab) page.switchTab({ currentTarget: { dataset: { tab } } })
  const tree = parseWxml(fs.readFileSync(templateFile, 'utf8'))
  if (sectionClass) {
    function find(nodes) {
      for (const node of nodes) {
        if ((node.attrs && node.attrs.class || '').split(/\s+/).includes(sectionClass)) return node
        const nested = node.children && find(node.children)
        if (nested) return nested
      }
    }
    const section = find(tree.children)
    assert.ok(section, `the real template must contain ${sectionClass}`)
    tree.children = [section]
  }
  tree.scope = page.data
  return createRenderer({})(tree)
}

function fixture() {
  const names = new Map([[5, 'A组本队'], [6, 'B组队伍'], [7, 'C组队伍'], [8, 'D组队伍'], [11, longName]])
  const groups = [['A', [5, 12, 13]], ['B', [6, 11, 14]], ['C', [7, 10, 15]], ['D', [8, 9, 16]]]
  const teams = Array.from({ length: 16 }, (_, index) => {
    const id = index + 1
    return { team_id: id, team_name: names.get(id) || (id <= 4 ? '传奇直升' : '队伍') + id,
      registration_status: 'approved', stage: id <= 4 ? 'legend' : 'challenger', seed: id,
      group_name: id <= 4 ? null : groups.find(([, ids]) => ids.includes(id))[0] }
  })
  const byId = id => teams.find(t => t.team_id === id)
  let id = 100
  const rounds = groups.flatMap(([group, ids]) => [[ids[0], ids[1]], [ids[2], ids[0]], [ids[1], ids[2]]].map(([a, b]) => ({
    id: ++id, round_number: id - 100, group_name: group,
    team1_id: a, team2_id: b, team1_name: byId(a).team_name, team2_name: byId(b).team_name,
    team1_score: 0, team2_score: 0, winner_id: null, status: 'pending', schedule_status: 'unconfirmed'
  })))
  return { match: { id: 8, name: '示例起源杯', status: 'in_progress', statusText: '进行中', max_teams: 20, team_size: 6, roster_locked: true },
    teams: teams.reverse(), rounds: rounds.reverse() }
}

function classText(html, name) {
  const expression = new RegExp('<span class="' + name + '(?: [^"]*)?">([^<]*)</span>', 'g')
  return [...html.matchAll(expression)].map(match => match[1])
}

function hasClass(node, name) {
  return String(node.attrs && node.attrs.class || '').split(/\s+/).includes(name)
}

function nodesByClass(html, name) {
  const matches = []
  function visit(node) {
    if (hasClass(node, name)) matches.push(node)
    for (const child of node.children || []) visit(child)
  }
  // The preview emits normal HTML void tags; normalize them for the WXML parser.
  visit(parseWxml(html.replace(/<(?:img|input)\b[^>]*>/g, tag => tag.replace(/>$/, '/>'))))
  return matches
}

function textOf(node) {
  return 'text' in node ? node.text : (node.children || []).map(textOf).join('')
}

function descendantsOf(node) {
  return (node.children || []).flatMap(child => [child, ...descendantsOf(child)])
}

function knockoutFixture(complete = false) {
  const detail = fixture()
  detail.teams.filter(team => team.team_id <= 6).forEach(team => {
    team.stage = 'playoff'
    team.group_name = [1, 2, 5].includes(team.team_id) ? '上区' : '下区'
  })
  const names = new Map(detail.teams.map(team => [team.team_id, team.team_name]))
  const pairs = complete ? [[1, 2], [3, 4], [5, 3], [6, 1], [5, 6]] : [[1, 2], [3, 4]]
  const winners = [1, 3, 5, 6]
  detail.rounds = pairs.map(([team1_id, team2_id], index) => ({
    id: 200 + index, round_number: 20 + index, group_name: '淘汰赛', team1_id, team2_id,
    team1_name: names.get(team1_id), team2_name: names.get(team2_id),
    team1_score: complete && index < 4 ? 2 : 0, team2_score: complete && index < 4 ? 1 : 0,
    winner_id: complete && index < 4 ? winners[index] : null,
    status: complete && index < 4 ? 'finished' : 'pending', schedule_status: 'unconfirmed'
  }))
  return detail
}


function assertDuelLayout(html, expectedCount) {
  const duels = nodesByClass(html, 'matchup-duel')
  assert.equal(duels.length, expectedCount)
  for (const duel of duels) {
    const rows = duel.children.filter(node => node.tag)
    assert.equal(rows.length, 3, 'the two opponents flank a single shared score')
    assert.ok(hasClass(rows[1], 'schedule-duel-score'))
    const scoreNodes = descendantsOf(rows[1]).filter(node => hasClass(node, 'vteam-score'))
    assert.equal(scoreNodes.length, 2)
    for (const row of [rows[0], rows[2]]) {
      assert.ok(hasClass(row, 'vteam'))
      const children = row.children.filter(node => node.tag)
      assert.deepEqual(children.map(node => String(node.attrs.class).split(' ')[0]), ['team-mark', 'vteam-name'])
      assert.doesNotMatch(textOf(children[1]), /^\s*$/)
    }
  }
}

test('event navigation keeps rankings first, adds player statistics and puts membership in information', () => {
  const page = loadPage(fixture(), '5')
  const ranking = render(page)
  assert.deepEqual(nodesByClass(ranking, 'event-main-tabs')[0].children.filter(node => node.tag).map(textOf), ['赛事排名', '比赛对阵', '选手数据', '赛事信息'])
  assert.equal(nodesByClass(ranking, 'standing-row').length, 16)
  assert.equal(classText(ranking, 'group-member-name').length, 0)
  assert.equal(nodesByClass(ranking, 'round-row').length, 0)
  const info = render(page, 'info')
  assert.deepEqual(page.data.groupOverview.slice(0, 4).map(group => group.group_name), ['A', 'B', 'C', 'D'])
  assert.equal(classText(info, 'group-member-name').length, 16)
  assert.ok(classText(info, 'group-member-name').includes(longName))
  assert.match(info, /本队所在|双循环 BO1|单循环 BO1|BO3/)
  assert.match(info, /查看完整赛事规则/)
  assert.equal(nodesByClass(fs.readFileSync(templateFile, 'utf8'), 'event-rules-link')[0].attrs.bindtap, 'goRules')
  assert.equal(nodesByClass(info, 'standing-row').length, 0)
  const stats = render(page, 'stats')
  assert.equal(page.data.mainTab, 'stats')
  assert.equal(nodesByClass(stats, 'event-player-stats').length, 1)
  assert.equal(nodesByClass(stats, 'standing-row').length, 0)
  assert.equal(nodesByClass(stats, 'round-row').length, 0)
})

test('a group shortcut opens the matchup filters without repeating full rosters', () => {
  const page = loadPage(fixture())
  page.openGroupRounds({ currentTarget: { dataset: { tab: 'challenger', group: 'B' } } })
  const html = render(page)
  assert.equal(page.data.mainTab, 'matches')
  assert.equal(page.data.matchupGroup, 'B')
  assert.equal(classText(html, 'group-member-name').length, 0)
  assert.equal(classText(html, 'vteam-name').length, 6)
  assert.ok(classText(html, 'vteam-name').includes(longName))
  assert.doesNotMatch(html, /A组本队|C组队伍|D组队伍/)
  assertDuelLayout(html, 3)
  assert.deepEqual(classText(html, 'group-title'), ['时间待定'])
  assert.deepEqual(classText(html, 'schedule-stage-text'), Array(3).fill('挑战者组 · B组 · BO1'))
})

test('an assigned group without games displays its local empty state', () => {
  const detail = fixture()
  detail.rounds = detail.rounds.filter(r => r.group_name !== 'A')
  const page = loadPage(detail)
  page.selectChallengerGroup({ currentTarget: { dataset: { group: 'A' } } })
  const html = render(page)
  assert.match(html, /所选小组对阵尚未生成/)
  assert.equal(classText(html, 'vteam-name').length, 0)
  assert.equal(classText(render(page, 'info'), 'group-member-name').length, 16)
})

test('only-own mode shows actual opponents and one captain contact entry per game', () => {
  const page = loadPage(fixture(), 5, true)
  const html = render(page, 'mine', 'matchup-card')
  assert.equal(page.data.onlyMyTeam, true)
  assert.deepEqual(classText(html, 'vteam-name'), ['A组本队', '队伍12', 'A组本队', '队伍13'])
  assertDuelLayout(html, 2)
  assert.deepEqual(classText(html, 'vteam-score'), ['—', '—', '—', '—'])
  assert.equal(nodesByClass(html, 'round-menu-button').length, 2)
  assert.doesNotMatch(html, /B组队伍|C组队伍|D组队伍|约定时间|确认时间/)
  const member = render(loadPage(fixture(), 5), 'mine')
  assert.equal(nodesByClass(member, 'round-menu-button').length, 0)
})

test('a second-side own team keeps its own score and per-map scores first', () => {
  const detail = fixture()
  const r = detail.rounds.find(r => r.team2_id === 5)
  Object.assign(r, { status: 'finished', winner_id: 5, team1_score: 1, team2_score: 2,
    bo3_scores: [{ t1: 13, t2: 7 }, { t1: 5, t2: 13 }, { t1: 8, t2: 13 }] })
  const page = loadPage(detail, 5, true)
  const html = render(page, 'mine', 'matchup-card')
  const finished = nodesByClass(html, 'round-finished')[0]
  const scores = descendantsOf(finished).filter(n => hasClass(n, 'vteam-score')).map(textOf)
  assert.deepEqual(scores, ['2', '1'])
  assert.match(textOf(finished), /本队胜|小局（本队在前）：7-13 \/ 13-5 \/ 13-8/)
  assert.equal(nodesByClass(html, 'round-menu-button').length, 2)
})

test('legend entrants awaiting a draw never get invented opponents or duplicate roster cards', () => {
  const page = loadPage(fixture(), 1, true)
  const mine = render(page, 'mine')
  assert.match(mine, /等待.*分区/)
  assert.equal(classText(mine, 'vteam-name').length, 0)
  const legend = render(page, 'legend')
  assert.match(legend, /传奇组对阵尚未生成/)
  assert.equal(classText(legend, 'group-member-name').length, 0)
  assert.ok(classText(render(page, 'info'), 'group-member-name').includes('传奇直升1'))
})

test('promotion retains complete historical groups in information and original match cards', () => {
  const detail = fixture()
  detail.teams.forEach(t => { t.stage = 'eliminated'; t.group_name = null })
  const page = loadPage(detail)
  const games = render(page, 'challenger')
  assert.equal(classText(games, 'vteam-name').length, 24)
  assert.ok(classText(games, 'vteam-name').includes(longName))
  assert.equal(page.data.challengerOverviewGroups.reduce((n, g) => n + g.team_count, 0), 12)
  assert.ok(classText(render(page, 'info'), 'group-member-name').includes(longName))
})

for (const previousZone of ['上区', '下区']) {
  test(`a knockout qualifier retains its ${previousZone} history while current stage is knockout`, () => {
    const detail = fixture()
    const own = detail.teams.find(t => t.team_id === 5)
    own.stage = 'playoff'; own.group_name = previousZone
    detail.rounds.push({ id: 200, round_number: 20, group_name: previousZone, team1_id: 5, team2_id: 6,
      team1_name: own.team_name, team2_name: 'B组队伍', status: 'finished', winner_id: 5, team1_score: 13, team2_score: 7 },
    { id: 201, round_number: 21, group_name: '淘汰赛', team1_id: 5, team2_id: 6,
      team1_name: own.team_name, team2_name: 'B组队伍', status: 'pending' })
    const page = loadPage(detail, 5, true)
    const html = render(page, 'mine')
    assert.equal(page.data.myMatchSummary.group_text, '淘汰赛 · ' + previousZone + '晋级')
    assert.ok(classText(html, 'schedule-stage-text').includes('淘汰赛 · 1/4决赛 · BO3'))
    assert.ok(page.data.legendOverviewGroups.some(g => g.group_name === previousZone && g.teams.some(t => t.team_id === 5)))
    assert.equal(page.detail.teams.find(t => t.team_id === 5).group_name, previousZone)
  })
}

test('actual two encounters retain local game numbers and no extra personal navigation column', () => {
  const detail = fixture()
  const first = detail.rounds.find(r => r.id === 101)
  detail.rounds = [first, { ...first, id: 501, round_number: 17, team1_id: 12, team2_id: 5,
    team1_name: first.team2_name, team2_name: first.team1_name }]
  const page = loadPage(detail, 5, true)
  const html = render(page, 'mine')
  assert.deepEqual(classText(html, 'round-format'), ['本组第 1 场 · 第1次交手', '本组第 2 场 · 第2次交手'])
  assert.equal(nodesByClass(html, 'round-menu-button').length, 2)
  assert.equal(nodesByClass(html, 'event-main-tabs')[0].children.filter(n => n.tag).length, 4)
})

test('the ranking reminder opens only-own fixtures and is absent from non-ranking sections', () => {
  const page = loadPage(fixture(), 5, true)
  const summary = render(page, 'teams', 'my-match-notice')
  assert.deepEqual(classText(summary, 'match-notice-title'), [page.data.myMatchSummary.notice_title])
  assert.doesNotMatch(summary, /vteam|versus|<button|round-menu-button/)
  assert.equal(render(page, 'mine', 'my-match-notice'), '')
  assert.equal(page.data.focusedRoundId, 101)
  assert.equal(page.data.matchupStage, 'all')
  assert.equal(page.data.onlyMyTeam, true)
  assert.equal(render(loadPage(fixture()), 'teams', 'my-match-notice'), '')
})

test('only administrator-confirmed times appear; user controls cannot schedule a game', () => {
  const detail = fixture()
  Object.assign(detail.rounds.find(r => r.id === 101), { schedule_status: 'confirmed', scheduled_time: '2026-10-10T13:00:00' })
  Object.assign(detail.rounds.find(r => r.id === 102), { schedule_status: 'pending', scheduled_time: '2026-10-09T19:00:00', team1_confirmed: true })
  const html = render(loadPage(detail, 5, true), 'mine')
  assert.deepEqual(classText(html, 'schedule-clock'), ['13:00', '—'])
  assert.deepEqual(classText(html, 'group-title'), ['10月10日', '时间待定'])
  assert.doesNotMatch(html, /10-09 19:00|约定时间|确认时间|撤回|重新约定/)
  const source = fs.readFileSync(templateFile, 'utf8')
  assert.doesNotMatch(source, /openSchedule|showSchedule|submitSchedule|confirmSchedule|rejectSchedule|can_operate|schedule_action|i_confirmed|mode="(?:date|time)"/)
  assert.ok(nodesByClass(source, 'round-menu-button').every(n => n.attrs.catchtap === 'openRoundActions' && !n.attrs.bindtap))
})

test('unplayed games show dashes; in-progress and finished scores stay with the corresponding team', () => {
  const detail = fixture()
  const page = loadPage(detail)
  assertDuelLayout(render(page, 'challenger'), 12)
  assert.deepEqual(classText(render(page), 'vteam-score'), Array(24).fill('—'))
  detail.rounds = [{ ...detail.rounds.find(r => r.id === 101), status: 'in_progress', team1_score: 9, team2_score: 11 }]
  const current = render(loadPage(detail), 'challenger')
  assert.deepEqual(classText(current, 'vteam-name'), ['A组本队', '队伍12'])
  assert.deepEqual(classText(current, 'vteam-score'), ['9', '11'])
  assertDuelLayout(current, 1)
})

test('three-group playoff uses the same horizontal cards and four-group events hide it', () => {
  const detail = fixture()
  detail.teams = detail.teams.filter(t => t.group_name !== 'D')
  detail.rounds = detail.rounds.filter(r => r.group_name !== 'D')
  detail.rounds.push({ id: 200, round_number: 20, group_name: '附加赛', team1_id: 5, team2_id: 6,
    team1_name: 'A组本队', team2_name: longName, status: 'pending', team1_score: 0, team2_score: 0 })
  const page = loadPage(detail)
  page.changeMatchupStage({ currentTarget: { dataset: { stage: 'playoff' } } })
  const html = render(page, 'matches')
  assertDuelLayout(html, 1)
  assert.deepEqual(classText(html, 'vteam-name'), ['A组本队', longName])
  assert.match(html, /附加赛 · 单循环 BO1/)
  assert.equal(classText(html, 'group-member-name').length, 0)
  const four = render(loadPage(fixture()), 'matches')
  assert.doesNotMatch(four, />附加赛<\/button>/)
})

test('legend group filters expose just the chosen district without fixed-height game lists', () => {
  const detail = fixture()
  for (const [id, zone, team1, team2] of [[200, '上区', 1, 2], [201, '下区', 3, 4]]) detail.rounds.push({ id, round_number: id,
    group_name: zone, team1_id: team1, team2_id: team2, team1_name: zone + '甲队', team2_name: zone + '乙队', status: 'pending' })
  const page = loadPage(detail)
  const upper = render(page, 'legend')
  assert.deepEqual(classText(upper, 'vteam-name'), ['上区甲队', '上区乙队'])
  page.changeMatchupGroup({ currentTarget: { dataset: { group: '下区' } } })
  assert.deepEqual(classText(render(page), 'vteam-name'), ['下区甲队', '下区乙队'])
  assert.doesNotMatch(upper, /mini-swiper|mini-scroll-y/)
})

test('malformed bracket uses real game rows and keeps BO3 map detail available to members', () => {
  const detail = fixture()
  detail.rounds = [{ id: 200, round_number: 20, group_name: '淘汰赛', team1_id: 1, team2_id: 2,
    team1_name: '传奇直升1', team2_name: '传奇直升2', team1_score: 0, team2_score: 0, status: 'pending' }]
  const page = loadPage(detail, 1)
  const html = render(page, 'knockout')
  assert.deepEqual(classText(html, 'vteam-score'), ['—', '—'])
  assert.match(html, /小局详情/)
  page.openMatchupDetail({ currentTarget: { dataset: { id: 200 } } })
  assert.equal(page.data.showBo3, true)
  assert.deepEqual(classText(render(page), 'bo3-big-score'), ['VS'])
})

test('six-team bracket has real quarterfinals and inactive future dependencies with no invented games', () => {
  const page = loadPage(knockoutFixture(), 1, true)
  const list = render(page, 'knockout')
  assert.equal(nodesByClass(list, 'round-row').length, 2, 'knockout fixtures open in the schedule list')
  assert.equal(nodesByClass(list, 'bracket-node').length, 0)
  assert.match(list, /查看晋级图/)
  page.toggleMatchupBracket()
  const html = render(page)
  assert.equal(nodesByClass(html, 'bracket-node').filter(n => !hasClass(n, 'bracket-placeholder')).length, 2)
  assert.equal(nodesByClass(html, 'bracket-placeholder').length, 3)
  assert.deepEqual(classText(html, 'bracket-team-name').slice(0, 4), ['传奇直升3', '传奇直升4', '传奇直升1', '传奇直升2'])
  assert.ok(nodesByClass(html, 'bracket-placeholder').every(n => !descendantsOf(n).some(c => c.tag === 'button')))
  assert.equal(nodesByClass(html, 'round-menu-button').length, 1)
  assert.equal(page.detail.rounds.length, 2)
  page.toggleMyTeam()
  const mine = render(page)
  assert.equal(nodesByClass(mine, 'bracket-node').length, 0)
  assertDuelLayout(mine, 1)
})

test('complete bracket retains real BO3 scores, missing-result dashes, and original data', () => {
  const detail = knockoutFixture(true)
  const page = loadPage(detail, 5, true)
  render(page, 'knockout')
  page.toggleMatchupBracket()
  const html = render(page)
  assert.equal(nodesByClass(html, 'bracket-placeholder').length, 0)
  assert.equal(nodesByClass(html, 'bracket-node').length, 5)
  assert.deepEqual(classText(html, 'bracket-team-score'), ['2', '1', '2', '1', '2', '1', '2', '1', '—', '—'])
  page.showBo3({ currentTarget: { dataset: { id: 200 } } })
  assert.deepEqual(classText(render(page), 'bo3-big-score'), ['2 : 1'])
  assert.equal(JSON.stringify(page.detail), JSON.stringify(detail))
})

test('calendar cards group confirmed times in Beijing order and never substitute creation dates', () => {
  const detail = fixture()
  const base = detail.rounds[0]
  detail.rounds = [
    { ...base, id: 600, round_number: 1, schedule_status: 'confirmed', scheduled_time: '2026-10-10T17:30:00Z' },
    { ...base, id: 601, round_number: 2, schedule_status: 'confirmed', scheduled_time: '2026-10-11T01:00:00+08:00' },
    { ...base, id: 602, round_number: 3, schedule_status: 'pending', scheduled_time: '2026-10-09T13:00:00', created_at: '2026-10-08T12:00:00' },
    { ...base, id: 603, round_number: 4, schedule_status: 'confirmed', scheduled_time: '2026-02-30T13:00:00' },
    { ...base, id: 604, round_number: 5, status: 'finished', winner_id: base.team1_id, team1_score: 13, team2_score: 5, created_at: '2026-10-07T12:00:00' }
  ]
  const original = clone(detail)
  const page = loadPage(detail)
  const html = render(page, 'matches')
  assert.deepEqual(classText(html, 'group-title'), ['10月11日', '时间待定', '已结束 · 时间未记录'])
  assert.deepEqual(classText(html, 'schedule-clock'), ['01:00', '01:30', '—', '—', '—'])
  assert.deepEqual(page.data.matchupTimelineSections[0].rounds.map(r => r.id), [601, 600])
  assert.deepEqual(page.data.matchupDates.map(d => d.value), ['', '2026-10-11', 'unscheduled'])
  assert.match(html, /2026 · 周日/)
  assert.doesNotMatch(html, /10月07日|10月08日|10月09日|02月30日|17:30/)
  assert.equal(JSON.stringify(page.detail), JSON.stringify(original))
  page.changeMatchupDate({ detail: { value: '1' } })
  assert.equal(page.data.matchupCount, 2)
})

test('a BO1 card opens full team names and the matching score without exposing captain operations', () => {
  const detail = fixture()
  const round = detail.rounds.find(r => r.team1_name === longName || r.team2_name === longName)
  Object.assign(round, { status: 'finished', winner_id: round.team1_id, team1_score: 13, team2_score: 7 })
  const page = loadPage(detail)
  render(page, 'matches')
  page.openMatchupDetail({ currentTarget: { dataset: { id: round.id } } })
  const html = render(page)
  assert.equal(page.data.bo3Detail.format_text, 'BO1')
  assert.deepEqual(classText(html, 'bo3-big-score'), ['13 : 7'])
  assert.match(textOf(nodesByClass(html, 'bo3-panel')[0]), new RegExp(longName))
  assert.match(html, /本场采用 BO1/)
  assert.equal(nodesByClass(html, 'round-menu-button').length, 0)
})

test('date and own-team filtering leave a bracket only after explicit expansion and return to the list', () => {
  const page = loadPage(knockoutFixture(), 1, true)
  render(page, 'knockout')
  assert.equal(page.data.canShowBracket, true)
  assert.equal(page.data.showBracket, false)
  page.toggleMatchupBracket()
  assert.equal(page.data.showBracket, true)
  page.changeMatchupDate({ detail: { value: '1' } })
  assert.equal(page.data.showBracket, false)
  assert.equal(page.data.canShowBracket, false)
  page.changeMatchupDate({ detail: { value: '0' } })
  assert.equal(page.data.canShowBracket, true)
  assert.equal(page.data.showBracket, false)
  page.toggleMatchupBracket()
  page.toggleMyTeam()
  assert.equal(page.data.onlyMyTeam, true)
  assert.equal(page.data.showBracket, false)
  assertDuelLayout(render(page), 1)
})

test('all eight own-team games remain reachable across challenger, legend and knockout stages', () => {
  const detail = fixture()
  const ownId = 5
  detail.teams.find(t => t.team_id === ownId).stage = 'eliminated'
  const groups = ['A', 'A', 'A', 'A', '上区', '上区', '上区', '淘汰赛']
  detail.rounds = groups.map((group_name, index) => ({
    id: 700 + index, round_number: index + 1, group_name,
    team1_id: ownId, team2_id: 12, team1_name: 'A组本队', team2_name: '队伍12',
    status: 'finished', winner_id: index === 7 ? 12 : ownId,
    team1_score: index === 7 ? 1 : 13, team2_score: index === 7 ? 2 : 8,
    schedule_status: 'unconfirmed', scheduled_time: null,
    bo3_scores: index === 7 ? [{ t1: 13, t2: 5 }, { t1: 9, t2: 13 }, { t1: 11, t2: 13 }] : null
  }))
  const page = loadPage(detail, ownId)
  const html = render(page, 'mine')
  assert.equal(nodesByClass(html, 'matchup-stage-select').length, 1)
  assert.deepEqual(page.data.matchupStages.map(stage => stage.label), ['全部阶段', '挑战者小组赛', '传奇组', '淘汰赛'])
  assert.equal(page.data.matchupStageIndex, 0)
  assert.match(textOf(nodesByClass(html, 'matchup-stage-select')[0]), /比赛阶段全部阶段/)
  assert.equal(page.data.matchupCount, 8)
  assert.deepEqual(page.data.matchupTimelineSections.flatMap(section => section.rounds.map(r => r.id)), groups.map((_, index) => 700 + index))
  assert.equal(nodesByClass(html, 'schedule-match-card').length, 8)
  assert.match(html, /传奇组 · 上区/)
  assert.match(html, /淘汰赛 · 1\/4决赛/)
  assert.equal(page.data.matchupDates.find(date => date.value === 'unscheduled').label, '未记录正式时间')
  for (const [stage, count] of [['legend', 3], ['knockout', 1], ['challenger', 4], ['all', 8]]) {
    page.changeMatchupStagePicker({ detail: { value: String(page.data.matchupStages.findIndex(option => option.value === stage)) } })
    assert.equal(page.data.onlyMyTeam, true)
    assert.equal(page.data.matchupStages[page.data.matchupStageIndex].value, stage)
    assert.equal(page.data.matchupCount, count)
    assert.equal(nodesByClass(render(page), 'schedule-match-card').length, count)
  }
})

test('stage and group native pickers follow links, clear conflicting filters and track changing stage choices', () => {
  const detail = fixture()
  detail.rounds.push(
    { id: 800, round_number: 80, group_name: '上区', team1_id: 5, team2_id: 1, team1_name: 'A组本队', team2_name: '传奇直升1', status: 'pending' },
    { id: 801, round_number: 81, group_name: '下区', team1_id: 6, team2_id: 2, team1_name: 'B组队伍', team2_name: '传奇直升2', status: 'pending' }
  )
  const page = loadPage(detail, 5)
  page.openGroupRounds({ currentTarget: { dataset: { tab: 'challenger', group: 'B' } } })
  assert.equal(page.data.matchupStages[page.data.matchupStageIndex].value, 'challenger')
  assert.equal(page.data.matchupGroupChoices[page.data.matchupGroupIndex].value, 'B')
  assert.match(textOf(nodesByClass(render(page), 'matchup-group-select')[0]), /B组/)
  page.setData({ matchupDate: 'unscheduled', focusedRoundId: 101 })
  page.changeMatchupGroupPicker({ detail: { value: String(page.data.matchupGroupChoices.findIndex(option => option.value === 'C')) } })
  assert.equal(page.data.matchupGroup, 'C')
  assert.equal(page.data.matchupDate, '')
  assert.equal(page.data.focusedRoundId, null)
  assert.equal(page.data.matchupCount, 3)
  page.openGroupRounds({ currentTarget: { dataset: { tab: 'legend', group: '下区' } } })
  assert.equal(page.data.matchupStage, 'legend')
  assert.equal(page.data.matchupStages[page.data.matchupStageIndex].value, 'legend')
  assert.equal(page.data.matchupGroupChoices[0].label, '全部分区')
  assert.equal(page.data.matchupGroupChoices[page.data.matchupGroupIndex].value, '下区')
  assert.match(textOf(nodesByClass(render(page), 'matchup-group-select')[0]), /传奇分区下区/)

  const revised = clone(detail)
  revised.teams = revised.teams.filter(team => team.group_name !== 'D')
  revised.rounds = revised.rounds.filter(round => round.group_name !== 'D' && round.group_name !== '下区')
  page.detail = revised
  page.buildData(revised, 5, false)
  assert.equal(page.data.hasPlayoffStage, true)
  assert.equal(page.data.matchupStage, 'legend')
  assert.equal(page.data.matchupStageIndex, 3, 'the inserted playoff option must not leave the legend picker on a different stage')
  assert.equal(page.data.matchupGroup, '')
  assert.equal(page.data.matchupGroupIndex, 0)
  assert.equal(page.data.matchupCount, 1)
  const original = clone(page.data)
  for (const value of ['bad', '-1', '1.5', '999']) {
    page.changeMatchupStagePicker({ detail: { value } })
    page.changeMatchupGroupPicker({ detail: { value } })
    assert.deepEqual(page.data, original)
  }
})

test('native filter fields and the match list use page flow without button grids or hidden stage strips', () => {
  const tree = parseWxml(fs.readFileSync(templateFile, 'utf8'))
  const inspect = (node, ancestors = []) => {
    if (hasClass(node, 'matchup-filters') || hasClass(node, 'schedule-day-section')) {
      assert.equal(node.tag, 'view')
      assert.ok(ancestors.every(parent => parent.tag !== 'scroll-view' && parent.tag !== 'swiper'))
    }
    for (const child of node.children || []) inspect(child, ancestors.concat(node))
  }
  inspect(tree)
  const source = fs.readFileSync(templateFile, 'utf8')
  const stage = nodesByClass(source, 'matchup-stage-select')[0]
  const group = nodesByClass(source, 'matchup-group-select')[0]
  assert.equal(stage.tag, 'picker')
  assert.equal(stage.attrs.mode, 'selector')
  assert.equal(stage.attrs.range, '{{matchupStages}}')
  assert.equal(stage.attrs['range-key'], 'label')
  assert.equal(stage.attrs.value, '{{matchupStageIndex}}')
  assert.equal(stage.attrs.bindchange, 'changeMatchupStagePicker')
  assert.equal(group.tag, 'picker')
  assert.equal(group.attrs.range, '{{matchupGroupChoices}}')
  assert.equal(group.attrs.value, '{{matchupGroupIndex}}')
  assert.equal(group.attrs.bindchange, 'changeMatchupGroupPicker')
  assert.equal(nodesByClass(source, 'matchup-mine-switch')[0].attrs.bindchange, 'toggleMyTeam')
  assert.doesNotMatch(source, /matchup-stage-tabs|matchup-stage-scroll|matchup-group-filters/)
})
