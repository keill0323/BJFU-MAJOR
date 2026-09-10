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

module.exports = { isWaitingForGroup, isRosterLocked }
