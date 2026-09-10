const api = require('../../../utils/api.js')

const text = value => value === null || value === undefined ? '' : String(value)
const allowedImage = value => !value || (!/[\s\\]/.test(value) && (/^\/(?!\/)/.test(value) || /^https?:\/\/[^/?#@]+(?:[/?#]|$)/i.test(value)))
const validRank = value => /^(D|[CBA]\+{0,2}|S(?:[0-9]|[1-4][0-9]|50)?)$/.test(value || '')
const emptyForm = () => ({ champion_name: '', champion_logo: '', event_date: '', runner_up_name: '', champion_score: '', runner_up_score: '', rosterText: '', note: '' })

function rosterText(roster) { return roster.map(member => text(member.nickname).trim()).filter(Boolean).join('\n') }
function knownMember(member) {
  const result = { nickname: text(member.nickname).trim() }
  if (Number.isInteger(member.user_id) && member.user_id > 0) result.user_id = member.user_id
  if (validRank(member.rank)) result.rank = member.rank
  if (member.avatar && member.avatar.length <= 256 && allowedImage(member.avatar)) result.avatar = member.avatar
  return result
}

Page({
  data: {
    matchId: null, match: null, loading: true, loadError: '', denied: false, canEdit: false,
    champion: null, version: 0, form: emptyForm(), selectedTeamId: null,
    teams: [], teamsLoading: false, teamsError: '', teamIndex: -1, teamLoading: false,
    rosterCount: 0, fromCurrentTeam: false, hasAccountLinks: false, historicalOnly: false,
    saving: false, published: false, formError: ''
  },
  onLoad(options = {}) {
    const matchId = Number(options.matchId)
    if (!Number.isInteger(matchId) || matchId <= 0) {
      this.setData({ loading: false, loadError: '赛事参数无效，请从管理后台重新进入。' })
      return Promise.resolve()
    }
    this.setData({ matchId })
    return this.load()
  },
  onUnload() { this._unloaded = true; this._teamRequest = (this._teamRequest || 0) + 1 },
  async load() {
    if (this._loading || !this.data.matchId) return
    this._loading = true
    this.setData({ loading: true, loadError: '', denied: false, canEdit: false })
    try {
      const user = await api.getMe()
      if (this._unloaded) return
      if (!user || user.role !== 'admin') {
        this.setData({ denied: true })
        return
      }
      const [match, record] = await Promise.all([api.getMatch(this.data.matchId), api.getAdminChampion(this.data.matchId)])
      if (this._unloaded) return
      const champion = record.champion || null
      this.setData({ match, canEdit: match.status === 'finished' && (!champion || champion.snapshot_source === 'manual') })
      this.applyRecord(record)
    } catch (err) {
      if (!this._unloaded) this.setData({ loadError: err && err.detail || '冠军档案加载失败，请稍后重试。' })
    } finally {
      this._loading = false
      if (!this._unloaded) this.setData({ loading: false })
    }
  },
  applyRecord(record) {
    const champion = record.champion || null
    const roster = (champion && champion.roster || []).map(knownMember).filter(member => member.nickname)
    this._knownRoster = roster
    this._rosterOriginalText = rosterText(roster)
    this._editRevision = (this._editRevision || 0) + 1
    this.setData({
      champion, version: record.version || 0, selectedTeamId: champion && champion.champion_team_id || null,
      rosterCount: roster.length, fromCurrentTeam: false, historicalOnly: false,
      hasAccountLinks: !!(champion && champion.champion_team_id) || roster.some(member => !!member.user_id),
      form: Object.assign(emptyForm(), champion ? {
        champion_name: text(champion.champion_name), champion_logo: text(champion.champion_logo),
        event_date: text(champion.event_date).slice(0, 10), runner_up_name: text(champion.runner_up_name),
        champion_score: text(champion.champion_score), runner_up_score: text(champion.runner_up_score),
        rosterText: this._rosterOriginalText, note: text(record.note)
      } : {})
    })
  },
  onField(e) {
    if (!this.data.canEdit || this.data.saving) return
    const field = e.currentTarget.dataset.field
    if (!Object.prototype.hasOwnProperty.call(emptyForm(), field)) return
    const value = text(e.detail.value)
    this._editRevision = (this._editRevision || 0) + 1
    const update = { form: Object.assign({}, this.data.form, { [field]: value }), formError: '', published: false }
    if (field === 'champion_name' && value !== this.data.form.champion_name) Object.assign(update, { selectedTeamId: null, teamIndex: -1 })
    if (field === 'rosterText') update.rosterCount = value.split(/\r?\n/).filter(line => line.trim()).length
    const selectedTeamId = Object.prototype.hasOwnProperty.call(update, 'selectedTeamId') ? update.selectedTeamId : this.data.selectedTeamId
    update.hasAccountLinks = !!selectedTeamId || (update.form.rosterText === this._rosterOriginalText && (this._knownRoster || []).some(member => !!member.user_id))
    this.setData(update)
  },
  keepHistoricalOnly() {
    if (!this.data.canEdit || this.data.saving || !this.data.hasAccountLinks) return
    this._teamRequest = (this._teamRequest || 0) + 1
    this._editRevision = (this._editRevision || 0) + 1
    const currentText = this.data.form.rosterText
    const roster = currentText === this._rosterOriginalText ? this._knownRoster || [] : currentText.split(/\r?\n/).map(nickname => ({ nickname: nickname.trim() })).filter(member => member.nickname)
    this._knownRoster = roster.map(member => {
      const historical = knownMember(member)
      delete historical.user_id
      return historical
    })
    this._rosterOriginalText = currentText
    this.setData({ selectedTeamId: null, hasAccountLinks: false, historicalOnly: true, teamIndex: -1, teamLoading: false, teamsError: '', formError: '', published: false })
  },
  async loadTeams() {
    if (!this.data.canEdit || this.data.teamsLoading || this.data.saving) return
    this.setData({ teamsLoading: true, teamsError: '' })
    try {
      const teams = await api.getAdminTeams()
      if (!this._unloaded) this.setData({ teams: teams || [], teamsError: teams && teams.length ? '' : '暂无现有战队，可直接填写历史队名。' })
    } catch (err) {
      if (!this._unloaded) this.setData({ teamsError: err && err.detail || '战队列表加载失败，可直接填写历史队名。' })
    } finally {
      if (!this._unloaded) this.setData({ teamsLoading: false })
    }
  },
  async onPickTeam(e) {
    if (!this.data.canEdit || this.data.saving) return
    const index = Number(e.detail.value)
    const team = this.data.teams[index]
    if (!team) return
    const requestId = (this._teamRequest || 0) + 1
    this._teamRequest = requestId
    const revision = this._editRevision || 0
    this.setData({ teamLoading: true, teamsError: '', teamIndex: index, published: false })
    try {
      const detail = await api.getTeam(team.id)
      if (this._unloaded || requestId !== this._teamRequest) return
      if (revision !== (this._editRevision || 0)) {
        this.setData({ teamsError: '你已修改表单，本次带入已取消。需要时可重新选择战队。', teamIndex: -1 })
        return
      }
      const roster = (detail.members || []).map(knownMember).filter(member => member.nickname)
      this._knownRoster = roster
      this._rosterOriginalText = rosterText(roster)
      this._editRevision = revision + 1
      this.setData({
        selectedTeamId: detail.id, fromCurrentTeam: true, rosterCount: roster.length, formError: '',
        hasAccountLinks: !!detail.id || roster.some(member => !!member.user_id), historicalOnly: false,
        form: Object.assign({}, this.data.form, { champion_name: text(detail.name), champion_logo: text(detail.logo), rosterText: this._rosterOriginalText })
      })
    } catch (err) {
      if (!this._unloaded && requestId === this._teamRequest) this.setData({ teamsError: err && err.detail || '带入失败，已保留原有内容。', teamIndex: -1 })
    } finally {
      if (!this._unloaded && requestId === this._teamRequest) this.setData({ teamLoading: false })
    }
  },
  makePayload() {
    const form = this.data.form
    const name = form.champion_name.trim(), note = form.note.trim(), runnerName = form.runner_up_name.trim()
    if (!name || name.length > 64) throw new Error('请填写冠军战队名称，最多 64 个字。')
    const date = form.event_date
    const parsed = /^\d{4}-\d{2}-\d{2}$/.test(date) && new Date(date + 'T00:00:00Z')
    if (!parsed || !Number.isFinite(parsed.getTime()) || parsed.toISOString().slice(0, 10) !== date) throw new Error('请选择真实的夺冠日期。')
    if (runnerName.length > 64) throw new Error('亚军战队名称最多 64 个字。')
    if (!note || note.length > 500) throw new Error('请填写资料来源或补录原因，最多 500 个字。')
    const winner = form.champion_score.trim(), runner = form.runner_up_score.trim()
    let championScore = null, runnerScore = null
    if (winner || runner) {
      if (!/^\d{1,3}$/.test(winner) || !/^\d{1,3}$/.test(runner)) throw new Error('比分请成对填写 0–999 的整数，不清楚时两项都留空。')
      championScore = Number(winner); runnerScore = Number(runner)
      if (championScore <= runnerScore) throw new Error('冠军比分必须高于亚军比分。')
    }
    const logo = form.champion_logo.trim()
    if (!allowedImage(logo) || logo.length > 255) throw new Error('队徽请填写 http(s) 图片地址或 / 开头的站内路径，最多 255 个字符。')
    const names = form.rosterText.split(/\r?\n/).map(line => line.trim()).filter(Boolean)
    if (names.length > 20 || names.some(name => name.length > 64)) throw new Error('夺冠阵容最多 20 人，每行一名，每个名字最多 64 个字。')
    const roster = form.rosterText === this._rosterOriginalText ? (this._knownRoster || []).map(knownMember) : names.map(nickname => ({ nickname }))
    return {
      expected_version: this.data.version, champion_team_id: this.data.selectedTeamId,
      champion_name: name, champion_logo: logo || null, event_date: date + 'T00:00:00',
      runner_up_name: runnerName || null, champion_score: championScore, runner_up_score: runnerScore,
      roster, note
    }
  },
  async submit() {
    if (!this.data.canEdit || this.data.saving || this.data.teamLoading) return
    let payload
    try { payload = this.makePayload() } catch (err) { this.setData({ formError: err.message }); return }
    this.setData({ saving: true, formError: '' })
    try {
      const confirmed = await new Promise(resolve => wx.showModal({
        title: this.data.champion ? '确认更新冠军档案' : '确认发布冠军档案',
        content: '赛事：' + this.data.match.name + '\n冠军：' + payload.champion_name + '\n日期：' + this.data.form.event_date + '\n阵容：' + (payload.roster.length ? payload.roster.length + ' 人' : '暂无记录') + '\n确认后将公开展示在名人堂，请核对历史资料。',
        confirmText: '确认发布', success: result => resolve(!!result.confirm), fail: () => resolve(false)
      }))
      if (!confirmed || this._unloaded) return
      const result = await api.saveAdminChampion(this.data.matchId, payload)
      if (this._unloaded) return
      this.applyRecord(result)
      this.setData({ published: true, formError: '' })
    } catch (err) {
      if (!this._unloaded) this.setData({ formError: (err && err.detail || '发布失败，表单已保留，请稍后重试。') + (err && err.statusCode === 409 ? ' 请先保留当前填写内容，再返回管理页面重新进入，核对最新档案。' : '') })
    } finally {
      if (!this._unloaded) this.setData({ saving: false })
    }
  },
  goHall() { wx.navigateTo({ url: '/pages/hall/hall' }) },
  goManagement() {
    const pages = getCurrentPages()
    const index = pages.findIndex(page => page.route === 'pages/admin/admin')
    if (index >= 0 && index < pages.length - 1) wx.navigateBack({ delta: pages.length - 1 - index, fail: () => wx.redirectTo({ url: '/pages/admin/admin?tab=matches' }) })
    else wx.redirectTo({ url: '/pages/admin/admin?tab=matches' })
  },
  goMatchAdmin() { wx.navigateTo({ url: '/pages/admin/mdetail/mdetail?id=' + this.data.matchId }) }
})
