const api = require('../../utils/api.js')
const { rankBadge } = require('../../utils/rank.js')

function text(value) {
  return typeof value === 'string' ? value.trim() : ''
}

function positiveId(value) {
  const number = Number(value)
  return Number.isSafeInteger(number) && number > 0 ? number : 0
}

function mediaUrl(value) {
  const path = text(value)
  if (/^https?:\/\//i.test(path)) return path
  if (!path || path.indexOf('//') === 0 || /^[a-z]+:/i.test(path)) return ''
  return api.BASE + '/' + path.replace(/^\/+/, '')
}

function score(value) {
  if (value === null || value === undefined || value === '') return null
  const number = Number(value)
  return Number.isFinite(number) && number >= 0 ? number : null
}

// Only display fields enter component data; private server fields are never copied.
function profileData(raw) {
  const captainId = positiveId(raw.captain_id)
  const members = (Array.isArray(raw.members) ? raw.members : []).filter(Boolean).map(member => {
    const userId = positiveId(member.user_id)
    const nickname = text(member.nickname)
    const gameId = text(member.game_id)
    const displayName = nickname || gameId || (userId ? '玩家' + userId : '未命名玩家')
    const isCaptain = userId && captainId ? userId === captainId : text(member.role).toLowerCase() === 'captain'
    const verified = member.is_verified === true
    const identity = verified && ['new_student', 'senior'].indexOf(member.identity) >= 0 ? member.identity : 'unknown'
    const rank = text(member.rank)
    const badge = rankBadge(rank)
    const rating = score(member.rating)
    return {
      user_id: userId,
      nickname,
      game_id: gameId,
      display_name: displayName,
      initial: Array.from(displayName)[0] || '?',
      avatar_full: mediaUrl(member.avatar),
      is_captain: !!isCaptain,
      role: isCaptain ? 'captain' : 'member',
      rank_icon: badge.icon,
      rank_label: badge.label,
      rank_set: badge.key !== 'unranked',
      rating,
      score_text: rating === null ? '—' : String(rating),
      is_verified: verified,
      verification_text: verified ? '学籍已认证' : '学籍未认证',
      identity,
      identity_text: identity === 'new_student' ? '新生' : identity === 'senior' ? '老生' : '身份待确认'
    }
  })
  // Captain first; the API's roster order is retained for everyone else.
  const sortedMembers = members.filter(member => member.is_captain).concat(members.filter(member => !member.is_captain))
  const captain = sortedMembers.find(member => member.is_captain)
  const status = text(raw.status).toLowerCase()
  const statusClass = ['approved', 'pending', 'rejected'].indexOf(status) >= 0 ? status : 'unknown'
  const name = text(raw.name) || '未命名队伍'
  const created = /^(\d{4})-(\d{2})-(\d{2})/.exec(text(raw.created_at))
  return {
    team: {
      id: positiveId(raw.id),
      name,
      captain_id: captainId,
      captain_name: captain ? captain.display_name : (captainId ? '玩家' + captainId : '暂未设置'),
      description: text(raw.description) || '队长还没有填写队伍简介',
      logo_full: mediaUrl(raw.logo),
      logo_initial: Array.from(name)[0] || '?',
      status_class: statusClass,
      status_text: { approved: '队伍已通过审核', pending: '队伍待审核', rejected: '队伍审核未通过', unknown: '审核状态待确认' }[statusClass],
      created_date: created ? created[1] + '.' + created[2] + '.' + created[3] : '暂无记录',
      display_rating: members.map(member => member.rating || 0).sort((a, b) => b - a).slice(0, 5).reduce((sum, value) => sum + value, 0),
      member_count: members.length,
      ranked_count: members.filter(member => member.rank_set).length,
      verified_count: members.filter(member => member.is_verified).length,
      new_student_count: members.filter(member => member.identity === 'new_student').length,
      senior_count: members.filter(member => member.identity === 'senior').length,
      unknown_count: members.filter(member => member.identity === 'unknown').length
    },
    members: sortedMembers
  }
}

Component({
  properties: {
    teamId: {
      type: Number,
      value: 0,
      observer() { this.loadTeam() }
    }
  },
  data: { loading: true, error: '', team: null, members: [] },
  lifetimes: {
    created() { this._generation = 0; this._detached = false },
    attached() { if (this._requestedTeamId === undefined) this.loadTeam() },
    detached() { this._detached = true; this._generation = (this._generation || 0) + 1 }
  },
  methods: {
    async loadTeam() {
      if (this._detached) return
      const teamId = positiveId(this.data.teamId)
      const generation = this._generation = (this._generation || 0) + 1
      this._requestedTeamId = teamId
      this.setData({ loading: !!teamId, error: teamId ? '' : '未选择队伍，请关闭后重新打开', team: null, members: [] })
      if (!teamId) return
      const isCurrent = () => !this._detached && generation === this._generation && teamId === positiveId(this.data.teamId)
      try {
        const result = await api.getTeam(teamId)
        if (!isCurrent()) return
        if (!result || positiveId(result.id) !== teamId) throw new Error('Invalid team response')
        const profile = profileData(result)
        this.setData({ loading: false, error: '', team: profile.team, members: profile.members })
      } catch (err) {
        if (!isCurrent()) return
        const status = err && err.statusCode
        const error = status === 404 ? '队伍不存在或已被删除' : status === 401 || status === 403 ? '暂时无法查看队伍，请确认登录状态后重试' : '队伍信息加载失败，请检查网络后重试'
        this.setData({ loading: false, error, team: null, members: [] })
      }
    }
  }
})
