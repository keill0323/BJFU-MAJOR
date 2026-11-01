// Exercise the real public match page with fixtures only, never the live API.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const test = require('node:test')
const { parseWxml, createRenderer } = require('../../tools/preview-miniprogram.js')

function loadPage() {
  let page
  const file = path.resolve(__dirname, '../../miniprogram/pages/match/match.js')
  vm.runInNewContext(fs.readFileSync(file, 'utf8'), {
    Page: value => { page = value },
    require(name) {
      if (name.endsWith('/tournament.js')) return require(path.resolve(path.dirname(file), name))
      assert.equal(name, '../../utils/api.js')
      return new Proxy({}, { get() { throw new Error('Unexpected API call in data formatting') } })
    },
    wx: { getStorageSync: () => '' },
    console
  }, { filename: file })
  page.data = JSON.parse(JSON.stringify(page.data))
  // Mimic WeChat's JSON serialization at the rendering boundary.
  page.setData = data => Object.assign(page.data, JSON.parse(JSON.stringify(data)))
  return page
}

function makeRound(id, groupName) {
  return { id, group_name: groupName, round_number: id, team1_id: id * 2, team2_id: id * 2 + 1,
    team1_name: `${groupName}场次${id}甲队`, team2_name: `${groupName}场次${id}乙队`, status: 'pending' }
}

function scorecardNames(html) {
  return [...html.matchAll(/<span class="vteam-name(?: [^"]*)?">([^<]*)<\/span>/g)].map(match => match[1])
}

function renderChallengerStages(data) {
  const template = fs.readFileSync(path.resolve(__dirname, '../../miniprogram/pages/match/match.wxml'), 'utf8')
  function renderStage(challengerStage) {
    const page = loadPage()
    page.setData({ ...data, match: { id: 8, name: '分组布局回归', status: 'in_progress', statusText: '进行中', max_teams: 20, team_size: 6 } })
    page.selectChallengerStage(challengerStage)
    page.switchTab({ currentTarget: { dataset: { tab: 'challenger' } } })
    const tree = parseWxml(template)
    tree.scope = page.data
    return { html: createRenderer({})(tree), stages: page.data.matchupStages }
  }
  const rendered = renderStage(data.challengerStage)
  return {
    html: rendered.html,
    labels: rendered.stages.map(stage => stage.label),
    // Test visible scorecards, independently of whether the panel uses a swiper or plain views.
    panels: data.challengerSwiper.map((_, index) => renderStage(index).html)
  }
}

for (const groupNames of [['A', 'B', 'C'], ['A', 'B', 'C', 'D']]) {
  test(`${groupNames.length} actual challenger groups render without unused fixed groups`, () => {
    const page = loadPage()
    const rounds = groupNames.map((name, i) => makeRound(i + 1, name))
    if (groupNames.length === 3) rounds.push(makeRound(10, '附加赛'))
    page.buildData({ teams: [], rounds }, null, false)
    assert.deepEqual(Object.keys(page.data.challengerSwiper[0].groups), groupNames)
    assert.equal(page.data.challengerSwiper[0].count, groupNames.length)
    assert.equal(page.data.groupCount, groupNames.length)
    assert.equal(page.data.hasPlayoffStage, groupNames.length === 3)
    const rendered = renderChallengerStages(page.data)
    assert.deepEqual(rendered.labels, groupNames.length === 3 ? ['全部阶段', '挑战者小组赛', '附加赛', '传奇组', '淘汰赛'] : ['全部阶段', '挑战者小组赛', '传奇组', '淘汰赛'])
    assert.match(rendered.html, /挑战者小组赛/)
    for (const [i, name] of groupNames.entries()) {
      assert.ok(scorecardNames(rendered.panels[0]).includes(`${name}场次${i + 1}甲队`))
      if (rendered.panels[1]) assert.ok(!scorecardNames(rendered.panels[1]).includes(`${name}场次${i + 1}甲队`))
    }
    assert.ok(!scorecardNames(rendered.panels[0]).includes('附加赛场次10甲队'))
    if (groupNames.length === 3) assert.ok(scorecardNames(rendered.panels[1]).includes('附加赛场次10甲队'))
  })
}

test('custom Chinese groups and groups beyond F retain all rounds and eliminated teams', () => {
  const page = loadPage()
  const groupNames = ['松林', '竹林', 'G']
  const teams = groupNames.map((name, i) => ({ team_id: i + 1, team_name: name + '队', group_name: name, stage: 'eliminated', seed: null }))
  const rounds = groupNames.map((name, i) => makeRound(i + 1, name)).concat(makeRound(4, '松林'))
  page.buildData({ teams, rounds }, null, false)
  assert.deepEqual(Object.keys(page.data.challengerSwiper[0].groups), groupNames)
  assert.equal(page.data.challengerSwiper[0].groups['松林'].length, 2)
  assert.equal(page.data.challengerSwiper[0].count, 4)
  assert.deepEqual(page.data.rankingTeams.filter(t => t.rank_weight === 7).map(t => t.team_id), [1, 2, 3])
  assert.equal(page.data.rankingTeams.every(t => t.progress_text.startsWith('已淘汰')), true)
  assert.equal(page.data.rankingTeams.every(t => t.rank_badge === '待定'), true)
})

test('reserved stage names stay in playoffs, legend sections and knockout sections', () => {
  const page = loadPage()
  const groupNames = ['松林', '附加赛', '上区', '下区', '淘汰赛']
  const teams = groupNames.map((name, i) => ({ team_id: i + 1, group_name: name, stage: 'eliminated', seed: null }))
  page.buildData({ teams, rounds: groupNames.map((name, i) => makeRound(i + 1, name)) }, null, false)
  assert.deepEqual(Object.keys(page.data.challengerSwiper[0].groups), ['松林'])
  assert.equal(page.data.challengerSwiper.some(item => item.type === 'playoff'), false)
  assert.deepEqual(page.data.legendSwiper.map(g => g.rounds.map(r => r.group_name)), [['上区'], ['下区']])
  assert.deepEqual(page.data.knockoutGroups[0].rounds.map(r => r.group_name), ['淘汰赛'])
  assert.deepEqual(page.data.rankingTeams.filter(t => t.rank_weight >= 6).map(t => t.team_id).sort(), [1, 2])
  assert.deepEqual(page.data.rankingTeams.filter(t => t.rank_weight <= 5).map(t => t.team_id).sort(), [3, 4, 5])
  assert.deepEqual(page.data.rankingTeams.filter(t => t.rank_weight === 4).map(t => t.team_id), [5])
})

test('ungrouped records do not become named groups and prototype-like names remain safe', () => {
  const page = loadPage()
  const names = [null, '', '   ', '__proto__', 'constructor']
  page.buildData({ teams: [], rounds: names.map((name, i) => makeRound(i + 1, name)) }, null, false)
  assert.deepEqual(Object.keys(page.data.challengerSwiper[0].groups), ['__proto__', 'constructor'])
  assert.equal(page.data.challengerSwiper[0].groups.__proto__[0].id, 4)
  assert.equal(page.data.challengerSwiper[0].groups.constructor[0].id, 5)
  assert.equal(page.data.challengerSwiper[0].count, 2)
})

test('team assignments and historical rounds are combined without counting a group twice', () => {
  const page = loadPage()
  page.buildData({
    teams: ['松林', '竹林', '竹林', '银杏'].map((name, i) => ({ team_id: i + 1, group_name: name, stage: 'challenger' })),
    rounds: [makeRound(1, '松林')]
  }, null, false)
  assert.equal(page.data.groupCount, 3)
  assert.deepEqual(renderChallengerStages(page.data).labels, ['全部阶段', '挑战者小组赛', '附加赛', '传奇组', '淘汰赛'])
  page.buildData({
    teams: [{ team_id: 1, group_name: '上区', stage: 'legend' }],
    rounds: ['松林', '竹林', '银杏', '上区'].map((name, i) => makeRound(i + 1, name))
  }, null, false)
  assert.equal(page.data.groupCount, 3)
  assert.equal(page.data.hasPlayoffStage, true)
})

test('four groups assigned before any rounds have no playoff tab', () => {
  const page = loadPage()
  page.buildData({ teams: ['A', 'B', 'C', 'D'].map((name, i) => ({ team_id: i + 1, group_name: name, stage: 'challenger' })), rounds: [] }, null, false)
  assert.equal(page.data.groupCount, 4)
  const rendered = renderChallengerStages(page.data)
  assert.deepEqual(rendered.labels, ['全部阶段', '挑战者小组赛', '传奇组', '淘汰赛'])
  assert.match(rendered.html, /挑战者小组赛/)
})

test('an ungrouped event never displays a speculative playoff stage', () => {
  const page = loadPage()
  page.buildData({ teams: [{ team_id: 1, group_name: null, stage: 'challenger' }], rounds: [] }, null, false)
  assert.equal(page.data.groupCount, 0)
  assert.equal(page.data.hasPlayoffStage, false)
  const rendered = renderChallengerStages(page.data)
  assert.deepEqual(rendered.labels, ['全部阶段', '挑战者小组赛', '传奇组', '淘汰赛'])
  assert.match(rendered.html, /挑战者小组赛/)
})

test('four-group events hide old playoff tabs without deleting the historical rounds', () => {
  const page = loadPage()
  const detail = { teams: [], rounds: ['A', 'B', 'C', 'D', '附加赛'].map((name, i) => makeRound(i + 1, name)) }
  const originalRounds = JSON.stringify(detail.rounds)
  page.buildData(detail, null, false)
  assert.equal(page.data.groupCount, 4)
  assert.equal(page.data.hasPlayoffStage, false)
  const rendered = renderChallengerStages(page.data)
  assert.deepEqual(rendered.labels, ['全部阶段', '挑战者小组赛', '传奇组', '淘汰赛'])
  assert.match(rendered.html, /挑战者小组赛/)
  assert.ok(!scorecardNames(rendered.panels[0]).includes('附加赛场次5甲队'))
  assert.equal(page.data.challengerSwiper.some(item => item.type === 'playoff'), false)
  assert.equal(page.data.roundCount, 5)
  assert.equal(JSON.stringify(detail.rounds), originalRounds)
})

test('an old event with unknown original groups can still display its recorded playoff rounds', () => {
  const page = loadPage()
  page.buildData({ teams: [], rounds: [makeRound(5, '附加赛')] }, null, false)
  assert.equal(page.data.groupCount, 0)
  assert.equal(page.data.hasPlayoffStage, true)
  assert.deepEqual(renderChallengerStages(page.data).labels, ['全部阶段', '挑战者小组赛', '附加赛', '传奇组', '淘汰赛'])
  assert.deepEqual(page.data.challengerSwiper.find(item => item.type === 'playoff').rounds.map(r => r.id), [5])
})

test('refreshing or receiving stale swiper events cannot select a removed playoff tab', () => {
  const page = loadPage()
  const threeGroups = { teams: [], rounds: ['A', 'B', 'C'].map((name, i) => makeRound(i + 1, name)) }
  page.buildData(threeGroups, null, false)
  page.switchChallengerStage({ currentTarget: { dataset: { idx: '1' } } })
  assert.equal(page.data.challengerStage, 1)
  page.buildData(threeGroups, null, false)
  assert.equal(page.data.challengerStage, 1)
  page.buildData({ teams: [], rounds: ['A', 'B', 'C', 'D'].map((name, i) => makeRound(i + 1, name)) }, null, false)
  assert.equal(page.data.challengerStage, 0)
  for (const current of [1, -1, 0.5, 'bad']) {
    page.onChallengerSwiper({ detail: { current } })
    assert.equal(page.data.challengerStage, 0)
  }
})

test('the playoff stage uses single round-robin and preserves the previous group filter on return', () => {
  const page = loadPage()
  page.buildData({ teams: [], rounds: ['A', 'B', 'C'].map((name, i) => makeRound(i + 1, name)).concat(makeRound(10, '附加赛')) }, null, false)
  page.selectChallengerGroup({ currentTarget: { dataset: { group: 'A' } } })
  const selectedGroup = renderChallengerStages(page.data).html
  assert.match(selectedGroup, /挑战者小组赛 · 双循环 BO1/)
  assert.deepEqual(scorecardNames(selectedGroup), ['A场次1甲队', 'A场次1乙队'])
  assert.doesNotMatch(selectedGroup, /group-member-name/)

  page.switchChallengerStage({ currentTarget: { dataset: { idx: '1' } } })
  const playoff = renderChallengerStages(page.data).html
  assert.match(playoff, /附加赛 · 单循环 BO1/)
  assert.doesNotMatch(playoff, /双循环|每个对手交手两次|group-member-name|group-filters/)
  assert.deepEqual(scorecardNames(playoff), ['附加赛场次10甲队', '附加赛场次10乙队'])

  page.switchChallengerStage({ currentTarget: { dataset: { idx: '0' } } })
  const restored = renderChallengerStages(page.data).html
  assert.equal(page.data.selectedChallengerGroup, 'A')
  assert.deepEqual(scorecardNames(restored), ['A场次1甲队', 'A场次1乙队'])
  assert.match(restored, /class="matchup-field-value">A组<\/span>/)
  assert.match(restored, /挑战者小组赛 · 双循环 BO1/)
})
