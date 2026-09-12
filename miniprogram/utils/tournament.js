// 报名通过与进入比赛分组是两个不同状态，供用户端和管理端共用。
function isWaitingForGroup(team) {
  return !!team && team.registration_status === 'approved' && team.stage === 'challenger' &&
    !(Number(team.seed) > 0) && !team.group_name
}

// 优先使用服务端的名单锁标记，并兼容已有种子/分组/赛程的旧版详情。
function isRosterLocked(match, teams, rounds) {
  return !!(match && match.roster_locked === true) || !!(rounds && rounds.length) ||
    (teams || []).some(team => Number(team.seed) > 0 || !!team.group_name ||
      ['legend', 'playoff', 'eliminated'].indexOf(team.stage) >= 0)
}

const sameId = (a, b) => a != null && b != null && String(a) === String(b)
const participates = (round, id) => sameId(round.team1_id, id) || sameId(round.team2_id, id)
const hasResult = round => round.status === 'finished' && round.team1_id != null &&
  round.team2_id != null && participates(round, round.winner_id)
const orderedRounds = rounds => (rounds || []).slice().sort((a, b) =>
  (Number(a.round_number) || 0) - (Number(b.round_number) || 0) || (Number(a.id) || 0) - (Number(b.id) || 0))

function groupLabel(name) {
  if (name === '淘汰赛' || name === '附加赛') return name
  if (name === '上区' || name === '下区') return '传奇组 · ' + name
  return name && name.trim() ? '挑战者组 · ' + name + (/组$/.test(name) ? '' : '组') : '未分组'
}

// 重建历史小组名次：晋级后队伍的 group_name 已改变，参赛名单必须同时取自历史对阵。
function groupStandings(teams, rounds) {
  const tables = new Map()
  const add = name => {
    if (!name || !name.trim() || name === '淘汰赛') return null
    if (!tables.has(name)) tables.set(name, { name, ids: new Set(), rounds: [], positions: new Map(), complete: false })
    return tables.get(name)
  }
  teams.forEach(team => { const table = add(team.group_name); if (table) table.ids.add(String(team.team_id)) })
  rounds.forEach(round => {
    const table = add(round.group_name)
    if (!table) return
    table.rounds.push(round)
    if (round.team1_id != null) table.ids.add(String(round.team1_id))
    if (round.team2_id != null) table.ids.add(String(round.team2_id))
  })
  const teamMap = new Map(teams.map(team => [String(team.team_id), team]))
  tables.forEach(table => {
    const stats = new Map(Array.from(table.ids).map(id => [id, { id, wins: 0, diff: 0, rating: teamMap.has(id) ? Number(teamMap.get(id).rating || 0) : null }]))
    const pairs = new Map()
    table.complete = table.ids.size > 1 && table.rounds.length > 0
    table.rounds.forEach(round => {
      const a = String(round.team1_id), b = String(round.team2_id)
      if (!hasResult(round) || a === b || !Number.isFinite(round.team1_score) || !Number.isFinite(round.team2_score) ||
        round.team1_score === round.team2_score || !sameId(round.winner_id, round.team1_score > round.team2_score ? round.team1_id : round.team2_id)) {
        table.complete = false
        return
      }
      const pair = [a, b].sort().join(':')
      pairs.set(pair, (pairs.get(pair) || 0) + 1)
      stats.get(a).diff += round.team1_score - round.team2_score
      stats.get(b).diff += round.team2_score - round.team1_score
      stats.get(String(round.winner_id)).wins++
    })
    const size = table.ids.size
    // 支持完整单/双循环；缺场、未结束、额外不完整轮次不能用于奖金档判定。
    if (pairs.size !== size * (size - 1) / 2 || new Set(pairs.values()).size !== 1) table.complete = false
    if (!table.complete) return
    const ordered = Array.from(stats.values()).sort((a, b) => b.wins - a.wins || b.diff - a.diff || (b.rating || 0) - (a.rating || 0))
    ordered.forEach((row, index) => {
      const ambiguous = ordered.some(other => other.id !== row.id && other.wins === row.wins && other.diff === row.diff &&
        (other.rating === row.rating || other.rating == null || row.rating == null))
      // 三项完全同分时需要裁判确认，不能以种子号代替奖金名次裁决。
      if (!ambiguous) table.positions.set(row.id, index + 1)
    })
  })
  return tables
}

function applyPlacementBands(ranking, tables) {
  const challenger = Array.from(tables.values()).filter(table => !['上区', '下区', '附加赛'].includes(table.name))
  const count = challenger.length
  const formatKnown = count === 3 || count === 4
  const range = (start, end) => start === end ? String(start) : start + '–' + end
  return ranking.map(team => {
    if (team.rank_weight < 5 || team.rank_weight >= 8) return team
    const result = Object.assign({}, team, { rank_badge: team.rank_weight === 5 ? '传奇' : team.rank_weight === 6 ? '附加' : '小组', placement_order: 0 })
    const table = tables.get(team.group_name)
    const position = table && table.positions.get(String(team.team_id))
    if (position) result.group_text += ' · ' + (team.rank_weight === 7 ? '小组' : team.rank_weight === 5 ? '分区' : '附加赛') + '第 ' + position + ' 名'
    if (team.stage !== 'eliminated') return result
    let start = null, end = null
    if (position === 4 && team.rank_weight === 5 && ['上区', '下区'].includes(team.group_name)) {
      start = 7; end = 8
    } else if (formatKnown && count === 3 && team.rank_weight === 6 && position >= 2 && table.ids.size === 3) {
      start = 9; end = 10
    } else if (formatKnown && team.rank_weight === 7 && position >= (count === 3 ? 3 : 2)) {
      const firstEliminated = count === 3 ? 3 : 2
      start = count === 3 ? 11 : 9
      for (let place = firstEliminated; place < position; place++) start += challenger.filter(group => group.ids.size >= place).length
      end = start + challenger.filter(group => group.ids.size >= position).length - 1
    }
    result.rank_badge = start == null ? '待定' : range(start, end)
    result.placement_order = start == null ? 999 : start
    if (start == null) result.progress_text = '已淘汰 · 名次待核实'
    return result
  }).sort((a, b) => a.rank_weight - b.rank_weight || (a.placement_order || 0) - (b.placement_order || 0) ||
    ((Number(a.seed) > 0 ? Number(a.seed) : Infinity) - (Number(b.seed) > 0 ? Number(b.seed) : Infinity) || 0) ||
    (Number(a.team_id) || 0) - (Number(b.team_id) || 0))
}

// 名次只依据实际晋级结果；同阶段同名次并列，种子仅用于稳定排列。
function buildTeamRanking(teams, rounds) {
  const all = orderedRounds(rounds)
  const ko = all.filter(round => round.group_name === '淘汰赛')
  const quarter = ko.slice(0, 2), semi = ko.slice(2, 4), final = ko[4]
  const qualifiers = (teams || []).filter(team => team.stage === 'playoff')
  const quarterIds = new Set(quarter.reduce((ids, round) => ids.concat(round.team1_id, round.team2_id), []).filter(id => id != null).map(String))
  const hasByes = quarter.length === 2 && quarterIds.size === 4 && qualifiers.length === 6 &&
    Array.from(quarterIds).every(id => qualifiers.some(team => sameId(team.team_id, id)))
  const ranking = (teams || []).map(team => {
    const own = all.filter(round => participates(round, team.team_id))
    const groups = []
    own.forEach(round => {
      if (round.group_name && round.group_name !== '淘汰赛' && groups.indexOf(round.group_name) < 0) groups.push(round.group_name)
    })
    if (team.group_name && team.group_name !== '淘汰赛' && groups.indexOf(team.group_name) < 0) groups.push(team.group_name)
    let weight = 7, stage = '挑战者组', rank = '', eliminated = team.stage === 'eliminated'
    if (team.stage === 'legend' || ['上区', '下区'].indexOf(team.group_name) >= 0 || own.some(r => ['上区', '下区'].indexOf(r.group_name) >= 0)) {
      weight = 5; stage = '传奇组'
    } else if (team.group_name === '附加赛' || own.some(r => r.group_name === '附加赛')) {
      weight = 6; stage = '附加赛'
    }
    if (team.stage === 'playoff' || team.group_name === '淘汰赛' || ko.some(r => participates(r, team.team_id))) {
      weight = 4; stage = '六强'; rank = '六强'
    }
    if (semi.some(r => participates(r, team.team_id)) || quarter.some(r => hasResult(r) && sameId(r.winner_id, team.team_id)) ||
      (hasByes && team.stage === 'playoff' && !quarterIds.has(String(team.team_id)))) {
      weight = 3; stage = '四强'; rank = '四强'
    }
    if ((final && participates(final, team.team_id)) || semi.some(r => hasResult(r) && sameId(r.winner_id, team.team_id))) {
      weight = 2; stage = '决赛'; rank = '决赛'
    }
    const lastKo = ko.filter(r => participates(r, team.team_id)).pop()
    if (lastKo && hasResult(lastKo)) eliminated = !sameId(lastKo.winner_id, team.team_id)
    if (final && participates(final, team.team_id) && hasResult(final)) {
      weight = sameId(final.winner_id, team.team_id) ? 0 : 1
      stage = weight === 0 ? '冠军' : '亚军'; rank = stage
    }
    if (isWaitingForGroup(team) || (!team.stage && !own.length)) {
      weight = 8; stage = '待分组'; rank = ''
    }
    if (team.registration_status === 'pending' || team.registration_status === 'rejected') {
      weight = team.registration_status === 'pending' ? 9 : 10
      stage = weight === 9 ? '报名待审核' : '报名未通过'; rank = ''
    }
    return Object.assign({}, team, {
      ko_rank: rank, stage_text: stage, rank_weight: weight,
      rank_badge: weight === 0 ? '冠' : weight === 1 ? '亚' : weight === 2 ? '决赛' : weight === 3 ? '四强' : weight === 4 ? '六强' : '—',
      group_text: groups.length ? groups.map(groupLabel).join(' / ') : team.stage === 'legend' ? '直升传奇组 · 待分区' : groupLabel(team.group_name),
      progress_text: weight < 2 ? '赛事荣誉' : weight >= 8 ? '' : eliminated ? '已淘汰' : weight === 2 ? '争冠中' : '进行中'
    })
  })
  return applyPlacementBands(ranking, groupStandings(teams || [], all))
}

// 从赛事全部对阵分阶段统计，绝不复用接口中随当前阶段变化的 wins/losses。
function buildTeamHistory(teamId, rounds) {
  const groups = new Map()
  orderedRounds(rounds).filter(round => participates(round, teamId)).forEach(round => {
    const name = round.group_name || ''
    if (!groups.has(name)) groups.set(name, { key: name || 'ungrouped', title: name ? groupLabel(name) : '其他对阵',
      order: name === '淘汰赛' ? 3 : ['上区', '下区'].indexOf(name) >= 0 ? 2 : name === '附加赛' ? 1 : name ? 0 : 4,
      wins: 0, losses: 0, pending: 0, unresolved: 0, byes: 0, rounds: [] })
    const section = groups.get(name)
    const first = sameId(round.team1_id, teamId)
    const bye = round.team1_id == null || round.team2_id == null
    const valid = hasResult(round)
    const win = valid && sameId(round.winner_id, teamId)
    const outcome = bye ? '轮空' : valid ? (win ? '胜' : '负') : round.status === 'finished' ? '结果待确认' : round.status === 'in_progress' ? '进行中' : '待比赛'
    if (bye) section.byes++
    else if (valid) section[win ? 'wins' : 'losses']++
    else if (round.status === 'finished') section.unresolved++
    else section.pending++
    const score = (a, b) => first ? a + ' : ' + b : b + ' : ' + a
    const games = Array.isArray(round.bo3_scores) ? round.bo3_scores : []
    section.rounds.push(Object.assign({}, round, {
      opponent: (first ? round.team2_name : round.team1_name) || (bye ? '轮空' : '对手名称未提供'),
      outcome, result_class: valid ? (win ? 'win' : 'loss') : 'pending',
      score_text: !bye && (valid || round.status === 'in_progress') ? score(round.team1_score, round.team2_score) : '—',
      format_text: name === '淘汰赛' ? 'BO3 · 地图比分' : 'BO1 · 回合比分',
      maps_text: games.map((game, i) => '图' + (i + 1) + ' ' + score(game.t1, game.t2)).join(' / ')
    }))
  })
  return Array.from(groups.values()).sort((a, b) => a.order - b.order).map(section => Object.assign(section, {
    record_text: section.wins + ' 胜 · ' + section.losses + ' 负',
    extra_text: [section.pending ? section.pending + ' 场未结束' : '', section.byes ? section.byes + ' 场轮空' : '', section.unresolved ? section.unresolved + ' 场结果待确认' : ''].filter(Boolean).join(' · ')
  }))
}
module.exports = { isWaitingForGroup, isRosterLocked, buildTeamRanking, buildTeamHistory }
