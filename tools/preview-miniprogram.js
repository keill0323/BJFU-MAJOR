/**
 * Offline layout preview of the real mini-program WXML/WXSS and page data logic.
 * Run: node tools/preview-miniprogram.js [--page match] [--out <temp directory>]
 * HTML and fictional fixtures never enter the deployed mini-program. This is
 * a browser layout approximation, not a replacement for WeChat/device testing.
 */
const fs = require('node:fs')
const path = require('node:path')
const os = require('node:os')
const vm = require('node:vm')

const root = path.resolve(__dirname, '..')
const appRoot = path.join(root, 'miniprogram')
const clone = value => JSON.parse(JSON.stringify(value))
const escapeHtml = value => String(value == null ? '' : value).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]))

function makeFixtures() {
  const user = { id: 1, nickname: '林间有回声', game_id: 'FOREST_01', student_id: '示例学号', identity: 'new_student', rank: 'S32', individual_rating: 88, avatar: '', is_verified: true, role: 'user' }
  const members = ['林间有回声', '今天练枪了吗', '白桦', '长名字的队友也能完整显示', '回防中'].map((nickname, i) => ({ user_id: i + 1, nickname, game_id: 'FOREST_0' + (i + 1), rank: ['S32', 'A', 'B', 'B', 'C'][i], rating: [88, 75, 62, 60, 45][i], identity: i < 3 ? 'new_student' : 'senior', student_id: '示例 ' + (i + 1), avatar: '', role: i === 0 ? 'captain' : 'member' }))
  const team = { id: 10, name: '北林森林回响', description: '认真打好每一回合，一起走向下一场。', captain_id: 1, captain_name: user.nickname, status: 'approved', logo: '', members, member_count: members.length, rating: 330 }
  const matches = [
    { id: 1, name: '2026 秋季 CS2 Major 新生赛', description: '新学期，新的主场。和队友一起，让热爱上场。', status: 'registering', match_type: 'freshman', max_teams: 16, team_size: 5, registered_count: 12, register_end: '2026-09-20T20:00:00' },
    { id: 2, name: '北林 CS2 校园公开赛', description: '集结校园战队，用每一个回合写下战绩。', status: 'in_progress', match_type: 'major', max_teams: 16, team_size: 5, registered_count: 16, register_end: '2026-08-31T20:00:00' }
  ]
  const teams = ['北林森林回响', '白桦竞技', '林间突击队', '最后一颗闪光'].map((team_name, i) => ({ team_id: 10 + i, team_name, stage: 'challenger', group_name: 'A', seed: i + 5, wins: i === 0 ? 2 : 1, losses: i === 0 ? 0 : 1, diff: 8 - i * 3, rating: 330 - i * 15, registration_status: 'approved' }))
  const rounds = [
    { id: 1, round_number: 1, group_name: 'A', team1_id: 10, team2_id: 11, team1_name: teams[0].team_name, team2_name: teams[1].team_name, team1_score: 13, team2_score: 9, winner_id: 10, status: 'finished', schedule_status: 'confirmed', scheduled_time: '2026-09-06T20:00:00' },
    { id: 2, round_number: 2, group_name: 'A', team1_id: 10, team2_id: 12, team1_name: teams[0].team_name, team2_name: teams[2].team_name, team1_score: null, team2_score: null, winner_id: null, status: 'pending', schedule_status: 'pending', scheduled_time: '2026-09-12T19:00:00', team1_confirmed: true, team2_confirmed: false },
    { id: 3, round_number: 3, group_name: 'A', team1_id: 11, team2_id: 13, team1_name: teams[1].team_name, team2_name: teams[3].team_name, team1_score: null, team2_score: null, winner_id: null, status: 'pending', schedule_status: 'unconfirmed' },
    { id: 4, round_number: 1, group_name: '淘汰赛', team1_id: 10, team2_id: 11, team1_name: teams[0].team_name, team2_name: teams[1].team_name, team1_score: 2, team2_score: 1, winner_id: 10, status: 'finished', schedule_status: 'confirmed', scheduled_time: '2026-09-06T20:00:00', bo3_scores: [{ t1: 13, t2: 9 }, { t1: 8, t2: 13 }, { t1: 13, t2: 10 }] }
  ]
  return { user, team, matches, detail: { match: matches[1], teams, rounds } }
}

function setData(values) {
  for (const [key, value] of Object.entries(values)) {
    const parts = key.replace(/\[(\d+)\]/g, '.$1').split('.')
    let parent = this.data
    for (const part of parts.slice(0, -1)) parent = parent[part] || (parent[part] = {})
    parent[parts[parts.length - 1]] = value
  }
}

function createEnvironment(fixtures, route, guest = false) {
  const reads = {
    getMe: () => fixtures.user, getMyTeam: () => fixtures.team,
    getMatches: () => fixtures.matches, searchMatches: () => fixtures.matches,
    getMatch: () => fixtures.detail.match, getTeam: () => fixtures.team,
    getAdminTeams: () => [{ ...fixtures.team, status: 'pending' }, { ...fixtures.team, id: 11, name: '白桦竞技', status: 'approved' }],
    adminListUsers: () => [fixtures.user], getVerifyList: () => [{ ...fixtures.user, is_verified: false, verify_image: '/images/brand/forest-crest.svg', ai_review_status: 'pending', ai_review_reason: '示例：请核对在校信息。' }],
    getRankApplications: () => [{ id: 8, user_id: fixtures.user.id, nickname: fixtures.user.nickname, current_rank: 'A+', ai_rank: 'S10', ai_confidence: .82, rank_image: '/images/ranks/s-gold.svg', ai_reason: '示例：段位变更待人工确认。' }],
    getAdminTodos: () => fixtures.todos,
    getAdminChampion: () => fixtures.adminChampion,
    getMatchDetail: () => fixtures.detail, adminMatchDetail: () => fixtures.detail,
    getStageWindows: () => fixtures.windows || [],
    getHallChampions: () => fixtures.hallChampions,
    getHallPlayers: () => fixtures.hallPlayers,
    getMyRegistration: () => ({ registered: true, registration: { team_id: fixtures.team.id, status: 'approved' } }),
    getApplications: () => [], getInvitations: () => [], getMyRankApplications: () => [],
    getTeams: () => [fixtures.team, ...['白桦竞技', '林间突击队', '最后一颗闪光'].map((name, i) => ({ ...fixtures.team, id: 11 + i, name, rating: 295 - i * 35, captain_name: ['白桦', '林间风', '回防中'][i], member_count: 4 + i % 2 }))], getTalentMarket: () => []
  }
  const api = new Proxy({ BASE: '' }, {
    get(target, key) {
      if (key in target) return target[key]
      if (reads[key]) return async () => clone(reads[key]())
      throw new Error('Preview refuses unsupported API access: ' + String(key))
    }
  })
  const wx = {
    getStorageSync: key => key === 'token' ? (guest ? '' : 'offline-preview-token') : key.startsWith('no_rule_prompt_'),
    getWindowInfo: () => ({ statusBarHeight: 20, windowWidth: 390 }),
    getSystemInfoSync: () => ({ statusBarHeight: 20, windowWidth: 390 }),
    getMenuButtonBoundingClientRect: () => ({ top: 26, height: 32, left: 292, width: 86 }),
    showToast: () => {}, stopPullDownRefresh: () => {}, showModal: () => {},
    setStorageSync: () => {}, navigateTo: () => {}, reLaunch: () => {}
  }
  const context = {
    console, wx, getCurrentPages: () => [{ route: 'pages/' + route + '/' + route }],
    getApp: () => ({ globalData: {} }),
    require(modulePath) {
      if (modulePath.endsWith('/api.js')) return api
      if (modulePath.endsWith('/rank.js')) return require(path.join(appRoot, 'utils/rank.js'))
      if (modulePath.endsWith('/tournament.js')) return require(path.join(appRoot, 'utils/tournament.js'))
      if (modulePath.endsWith('/navigation.js')) return require(path.join(appRoot, 'utils/navigation.js'))
      if (modulePath.endsWith('/admin-todos.js')) return {
        refresh: async () => {
          if (fixtures.todoError) throw { detail: '示例：网络暂时不可用' }
          return clone(fixtures.todos)
        },
        invalidate() {},
        subscribe(listener) { listener({ data: fixtures.todos, loading: false, error: fixtures.todoError ? '待办读取失败' : '', updatedAt: 1 }); return () => {} }
      }
      throw new Error('Preview refuses unsupported module: ' + modulePath)
    }
  }
  return context
}

function loadDefinition(relative, context, registration) {
  let definition
  vm.runInNewContext(fs.readFileSync(path.join(appRoot, relative), 'utf8'), { ...context, [registration]: value => { definition = value } }, { filename: relative, timeout: 1500 })
  if (!definition) throw new Error('Missing ' + registration + ' definition: ' + relative)
  return { ...definition, ...(definition.methods || {}), data: clone(definition.data || {}), setData }
}

async function pageData(name) {
  const fixtures = makeFixtures()
  const route = name.split('-')[0]
  const guest = name.endsWith('-guest') || route === 'login'
  fixtures.todos = { verification_count: 3, rank_application_count: 2, team_count: 1, registration_count: 2, total: 8,
    registration_matches: [{ match_id: 1, match_name: fixtures.matches[0].name, count: 2, roster_locked: false }] }
  if (['workbench', 'champion', 'admin'].includes(route) || name === 'index-manager') fixtures.user.role = 'admin'
  if (name === 'workbench-empty') fixtures.todos = { verification_count: 0, rank_application_count: 0, team_count: 0, registration_count: 0, total: 0, registration_matches: [] }
  if (name === 'workbench-error') fixtures.todoError = true
  if (route === 'workbench') fixtures.matches.push({ id: 3, name: '2025 秋季校赛', status: 'finished', max_teams: 16, team_size: 5, match_type: 'freshman' })
  if (route === 'champion') {
    fixtures.detail.match = { ...fixtures.matches[0], status: 'finished' }
    fixtures.adminChampion = { champion: name === 'champion' ? null : {
      match_id: 1, match_name: fixtures.detail.match.name, match_type: 'major', champion_team_id: null,
      champion_name: '林间回响', champion_logo: '', event_date: '2025-10-19T00:00:00',
      runner_up_name: null, champion_score: null, runner_up_score: null,
      roster: [{ nickname: '林间风', user_id: null }, { nickname: '白桦', user_id: null }],
      snapshot_source: name === 'champion-readonly' ? 'final_result' : 'manual'
    }, note: '依据往届赛事记录补录（示例）', version: 1, updated_at: null }
  }
  if (name === 'verify-empty') Object.assign(fixtures.user, { rank: null, individual_rating: 0, is_verified: false, student_id: '' })
  if (route === 'admin' || name === 'match-three' || name === 'match-four') {
    if (route === 'admin') {
      fixtures.user.role = 'admin'
      fixtures.detail.match = { ...fixtures.matches[0], register_start: '2026-09-08T09:00:00' }
    }
    const groups = name.endsWith('-four') ? ['A', 'B', 'C', 'D'] : ['A', 'B', 'C']
    fixtures.detail.teams = groups.flatMap((group_name, i) => [0, 1].map(j => ({
      ...fixtures.detail.teams[i], team_id: 10 + i * 2 + j, group_name,
      team_name: fixtures.detail.teams[i].team_name + (j ? '二队' : '')
    })))
    fixtures.detail.rounds = [] // Assigned groups must appear before rounds exist.
    fixtures.windows = [{ group_name: 'B', window_start: '2026-09-21T18:00:00', window_end: '2026-09-25T22:00:00' }]
  }
  if (name === 'admin-awaiting' || name === 'match-awaiting') {
    fixtures.detail.match = { ...fixtures.matches[0], roster_locked: false }
    fixtures.detail.teams = fixtures.detail.teams.slice(0, 3).map(team => ({
      ...team, stage: 'challenger', group_name: null, seed: 0, wins: 0, losses: 0, diff: 0
    }))
    fixtures.detail.rounds = []
  }
  if (name === 'admin-awaiting' || name === 'admin-roster-locked') {
    fixtures.detail.teams.push({ team_id: 99, team_name: '等待审核的新队伍', registration_status: 'pending', stage: 'challenger', group_name: null, seed: 0, wins: 0, losses: 0, diff: 0, rating: 220 })
  }
  if (route === 'hall') {
    const champions = name === 'hall-empty' ? [] : [{
      match_id: 2, match_name: '2026 北林 CS2 校园公开赛', match_type: 'major', event_date: '2026-06-20T19:00:00',
      awarded_at: '2026-06-28T21:30:00', champion_team_id: 10, champion_name: '北林森林回响', champion_logo: '',
      runner_up_name: '白桦竞技', champion_score: 2, runner_up_score: 1, snapshot_source: 'final_result',
      roster: fixtures.team.members.map(member => ({ user_id: member.user_id, nickname: member.nickname, avatar: member.avatar, rank: member.rank }))
    }, {
      match_id: 3, match_name: '2025 秋季 CS2 Major 新生赛', match_type: 'freshman', event_date: '2025-10-01T18:00:00',
      awarded_at: null, champion_team_id: 11, champion_name: '白桦竞技', champion_logo: '', runner_up_name: '最后一颗闪光',
      champion_score: 2, runner_up_score: 0, snapshot_source: 'backfill', roster: []
    }]
    const players = name === 'hall-empty' ? [] : ['S50', 'S50', 'S32', 'S25', 'S10', 'A++', 'A+', 'C+'].map((rank, i) => ({
      user_id: i + 1, nickname: ['林间有回声', '白桦', '今天练枪了吗', '长名字的选手也能完整显示', '回防中', '最后一颗闪光', '林间风', '向下一场'][i],
      avatar: '', rank, individual_rating: 100 - i * 8, position: i < 2 ? 1 : i + 1
    }))
    fixtures.hallChampions = { items: champions, total: champions.length, offset: 0, limit: 20, has_more: false }
    fixtures.hallPlayers = { items: players, total: players.length, offset: 0, limit: 50, has_more: false }
  }
  const context = createEnvironment(fixtures, route, guest)
  const nav = loadDefinition('components/nav-drawer/nav-drawer.js', context, 'Component')
  nav.triggerEvent = () => {}
  nav.lifetimes.attached.call(nav)
  if (nav.loadUser) await nav.loadUser()
  const pagePath = route === 'admin' ? 'pages/admin/mdetail/mdetail' : route === 'workbench' ? 'pages/admin/admin'
    : route === 'champion' ? 'pages/admin/champion/champion' : 'pages/' + route + '/' + route
  const page = loadDefinition(pagePath + '.js', context, 'Page')
  page.selectComponent = () => nav
  if (route === 'index') {
    await page.loadMatches()
    if (page.onUserChange) page.onUserChange({ detail: { user: nav.data.user } })
  } else if (route === 'team') {
    await page.refreshAll()
  } else if (route === 'teams') {
    await page.loadTeams()
  } else if (route === 'verify' || route === 'profile') {
    page.setData({ token: 'offline-preview-token' })
    await page.loadUser()
  } else if (route === 'match') {
    await page.onLoad({ id: fixtures.detail.match.id })
    page.setData({ tab: name === 'match-knockout' ? 'knockout' : name === 'match-awaiting' ? 'teams' : 'challenger' })
  } else if (route === 'login') {
    page.onLoad()
  } else if (route === 'hall') {
    await page.onLoad({ tab: name === 'hall-players' ? 'players' : 'champions' })
    if (name === 'hall') page.toggleRoster({ currentTarget: { dataset: { id: 2 } } })
  } else if (route === 'workbench') {
    await page.onLoad({ tab: ['verify', 'rankapps', 'teams', 'matches'].includes(name.split('-')[1]) ? name.split('-')[1] : 'overview' })
  } else if (route === 'champion') {
    await page.onLoad({ matchId: 1 })
  } else if (route === 'admin') {
    page.setData({ matchId: fixtures.detail.match.id })
    await page.load()
    if (name.startsWith('admin-challenger')) page.setData({ tab: 'challenger' })
    if (name === 'admin-registration') page.openRegistrationPanel()
    if (name.startsWith('admin-windows')) await page.openWindowPanel()
  }
  if (page.data.loadFailed || page.data.loadError) throw new Error('Page fixture loading failed: ' + name)
  return { route, pagePath, data: page.data, nav: nav.data }
}

function parseWxml(source) {
  source = source.replace(/<!--[\s\S]*?-->/g, '')
  const rootNode = { tag: 'block', attrs: {}, children: [] }
  const stack = [rootNode]
  const tags = /<\/?([\w-]+)(?:"[^"]*"|'[^']*'|[^'">])*?>/g
  let offset = 0
  for (const found of source.matchAll(tags)) {
    if (found.index > offset) stack[stack.length - 1].children.push({ text: source.slice(offset, found.index) })
    offset = found.index + found[0].length
    if (found[0].startsWith('</')) {
      const previous = stack.pop()
      if (!previous || previous.tag !== found[1]) throw new Error('Mismatched WXML close tag: ' + found[1])
    } else {
      const attrs = {}
      const raw = found[0].slice(found[1].length + 1).replace(/\/?\s*>$/, '')
      for (const attr of raw.matchAll(/([\w:-]+)(?:\s*=\s*("([^"]*)"|'([^']*)'))?/g)) attrs[attr[1]] = attr[3] == null ? (attr[4] == null ? true : attr[4]) : attr[3]
      const node = { tag: found[1], attrs, children: [] }
      stack[stack.length - 1].children.push(node)
      if (!/\/\s*>$/.test(found[0])) stack.push(node)
    }
  }
  if (offset < source.length) rootNode.children.push({ text: source.slice(offset) })
  if (stack.length !== 1) throw new Error('Unclosed WXML tag: ' + stack[stack.length - 1].tag)
  validateConditionals(rootNode)
  return rootNode
}

// Catch conditional-scope errors even in hidden branches. Browser rendering
// alone does not reproduce the native WXML compiler's loop transformation.
function validateConditionals(parent) {
  let chainOpen = false
  for (const node of parent.children) {
    if ('text' in node) {
      if (node.text.trim()) chainOpen = false
      continue
    }
    const attrs = node.attrs
    const branches = ['wx:if', 'wx:elif', 'wx:else'].filter(key => key in attrs)
    if (branches.length > 1) throw new Error('Multiple conditional directives on <' + node.tag + '>')
    const continuation = 'wx:elif' in attrs || 'wx:else' in attrs
    if (continuation && 'wx:for' in attrs) {
      throw new Error('wx:else/wx:elif cannot share a node with wx:for; wrap the loop in a conditional <block>')
    }
    if (continuation && !chainOpen) throw new Error('wx:if not found before ' + branches[0] + ' on <' + node.tag + '>')
    if ('wx:if' in attrs) chainOpen = !('wx:for' in attrs)
    else if (!('wx:elif' in attrs)) chainOpen = false
    validateConditionals(node)
  }
}

function evaluate(value, scope) {
  if (value === true) return true
  const source = String(value || '').replace(/^\s*{{|}}\s*$/g, '')
  try {
    return vm.runInNewContext('(' + source + ')', scope, { timeout: 1000 })
  } catch (err) {
    // Parallel test workers can stall briefly on Windows. A timeout must fail
    // the preview explicitly, never silently change which WXML branch renders.
    if (err.code === 'ERR_SCRIPT_EXECUTION_TIMEOUT') throw err
    return undefined
  }
}
function interpolate(value, scope) {
  return String(value == null ? '' : value).replace(/{{([\s\S]*?)}}/g, (_, expression) => {
    const result = evaluate(expression, scope)
    return result == null ? '' : String(result)
  })
}

const resourceCache = new Map()
function resourceUrl(resource) {
  if (!resource || /^https?:\/\//i.test(resource)) return ''
  if (resource.startsWith('data:')) return resource
  if (resourceCache.has(resource)) return resourceCache.get(resource)
  const resolved = path.resolve(appRoot, resource.replace(/^\/+/, ''))
  if (!resolved.startsWith(appRoot + path.sep) || !fs.existsSync(resolved)) return ''
  const type = { '.svg': 'image/svg+xml', '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp' }[path.extname(resolved).toLowerCase()]
  if (!type) return ''
  const value = 'data:' + type + ';base64,' + fs.readFileSync(resolved).toString('base64')
  resourceCache.set(resource, value)
  return value
}

function cssSource(file, chain = []) {
  const resolved = path.resolve(file)
  if (!resolved.startsWith(appRoot + path.sep)) throw new Error('WXSS import outside mini-program')
  if (chain.includes(resolved)) throw new Error('Circular WXSS import: ' + resolved)
  let source = fs.readFileSync(resolved, 'utf8').replace(/^\uFEFF/, '')
  source = source.replace(/@import\s+['"]([^'"]+)['"]\s*;/g, (_, target) => cssSource(path.resolve(path.dirname(resolved), target), [...chain, resolved]))
  return source
}
function browserCss(source) {
  return source.replace(/(-?(?:\d*\.)?\d+)rpx\b/g, 'calc($1 * 100vw / 750)')
    .replace(/([^{}]+)\{/g, (_, selectors) => selectors.replace(/(^|[\s>+~,(])(page|view|text|image|scroll-view|swiper-item|swiper|picker|switch)(?=[\s.#:\[>+~),]|$)/g, (all, prefix, tag) => prefix + ({ page: 'body', view: 'div', text: 'span', image: 'img', 'scroll-view': 'div', 'swiper-item': 'div', swiper: 'div', picker: 'div', switch: 'input' })[tag]) + '{')
    .replace(/url\(['"]?(\/images\/[^)'"\s]+)['"]?\)/g, (_, resource) => 'url("' + resourceUrl(resource) + '")')
}

function createRenderer(navData) {
  const navTree = parseWxml(fs.readFileSync(path.join(appRoot, 'components/nav-drawer/nav-drawer.wxml'), 'utf8'))
  const returnTree = parseWxml(fs.readFileSync(path.join(appRoot, 'components/event-return/event-return.wxml'), 'utf8'))
  function children(nodes, scope) {
    let output = '', branchTaken = false, chainOpen = false
    for (const node of nodes) {
      if ('text' in node) { output += escapeHtml(interpolate(node.text, scope)); continue }
      const attrs = node.attrs
      if ('wx:if' in attrs) {
        chainOpen = true
        branchTaken = 'wx:for' in attrs || !!evaluate(attrs['wx:if'], scope)
        if (branchTaken) output += render(node, scope)
      } else if ('wx:elif' in attrs || 'wx:else' in attrs) {
        const show = chainOpen && !branchTaken && ('wx:else' in attrs || !!evaluate(attrs['wx:elif'], scope))
        if (show) { branchTaken = true; output += render(node, scope) }
      } else {
        chainOpen = false; branchTaken = false; output += render(node, scope)
      }
    }
    return output
  }
  function iterations(node, scope) {
    const list = evaluate(node.attrs['wx:for'], scope) || []
    return Object.keys(list).map(key => ({ ...scope, [node.attrs['wx:for-item'] || 'item']: list[key], [node.attrs['wx:for-index'] || 'index']: Array.isArray(list) ? Number(key) : key }))
  }
  function render(node, scope, skipFor = false) {
    if ('text' in node) return escapeHtml(interpolate(node.text, scope))
    const attrs = node.attrs
    if ('wx:for' in attrs && !skipFor) return iterations(node, scope).map(local => render(node, local, true)).join('')
    if ('wx:if' in attrs && !evaluate(attrs['wx:if'], scope)) return ''
    if (node.tag === 'block') return children(node.children, scope)
    if (node.tag === 'nav-drawer') return '<div class="preview-component">' + children(navTree.children, { ...navData, title: interpolate(attrs.title, scope), showAdminNotice: attrs['show-admin-notice'] ? !!evaluate(attrs['show-admin-notice'], scope) : !!navData.showAdminNotice }) + '</div>'
    if (node.tag === 'event-return') return '<div class="preview-component">' + children(returnTree.children, {}) + '</div>'
    let tag = ({ view: 'div', text: 'span', image: 'img', 'scroll-view': 'div', swiper: 'div', 'swiper-item': 'div', picker: 'div', switch: 'input', input: 'input', textarea: 'textarea', button: 'button' })[node.tag] || 'div'
    let htmlAttrs = ''
    const extraClass = node.tag === 'swiper' ? ' mini-swiper' : node.tag === 'swiper-item' ? ' mini-swiper-item' : node.tag === 'scroll-view' ? (attrs['scroll-y'] ? ' mini-scroll-y' : ' mini-scroll-x') : ''
    htmlAttrs += ' class="' + escapeHtml(interpolate(attrs.class || '', scope) + extraClass) + '"'
    if (attrs.style) htmlAttrs += ' style="' + escapeHtml(browserCss(interpolate(attrs.style, scope))) + '"'
    if (attrs.hidden && evaluate(attrs.hidden, scope)) htmlAttrs += ' hidden'
    if (tag === 'img') {
      htmlAttrs += ' src="' + resourceUrl(interpolate(attrs.src || '', scope)) + '" alt=""'
      if (attrs.mode === 'aspectFill') htmlAttrs += ' data-fit="cover"'
      else htmlAttrs += ' data-fit="contain"'
    }
    if (tag === 'input') {
      htmlAttrs += ' type="' + (node.tag === 'switch' ? 'checkbox' : attrs.password ? 'password' : 'text') + '"'
      if (attrs.value) htmlAttrs += ' value="' + escapeHtml(interpolate(attrs.value, scope)) + '"'
      if (attrs.placeholder) htmlAttrs += ' placeholder="' + escapeHtml(interpolate(attrs.placeholder, scope)) + '"'
    }
    if (tag === 'img' || tag === 'input') return '<' + tag + htmlAttrs + '>'
    let body
    if (node.tag === 'swiper') {
      const expanded = []
      for (const child of node.children.filter(n => n.tag)) {
        if ('wx:for' in child.attrs) for (const local of iterations(child, scope)) expanded.push([child, local])
        else expanded.push([child, scope])
      }
      const selected = expanded[Number(interpolate(attrs.current || '0', scope)) || 0]
      body = selected ? render(selected[0], selected[1], true) : ''
    } else body = children(node.children, scope)
    return '<' + tag + htmlAttrs + '>' + body + '</' + tag + '>'
  }
  return tree => children(tree.children, tree.scope)
}

async function writePreview(name, directory) {
  const { route, pagePath, data, nav } = await pageData(name)
  const base = path.join(appRoot, pagePath)
  const tree = parseWxml(fs.readFileSync(base + '.wxml', 'utf8'))
  tree.scope = data
  const css = browserCss(cssSource(path.join(appRoot, 'app.wxss')) + '\n' + cssSource(path.join(appRoot, 'components/nav-drawer/nav-drawer.wxss')) + '\n' + cssSource(path.join(appRoot, 'components/event-return/event-return.wxss')) + '\n' + cssSource(base + '.wxss'))
  const config = JSON.parse(fs.readFileSync(base + '.json', 'utf8'))
  const nativeNav = config.navigationStyle !== 'custom' ? '<div class="preview-native-nav"><span>‹</span><strong>' + escapeHtml(config.navigationBarTitleText || '北林CS2校赛') + '</strong><span>•••</span></div>' : ''
  const html = '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src \'none\'; img-src data:; style-src \'unsafe-inline\'"><title>' + name + ' · 离线布局预览</title><style>'
    + 'html,body{margin:0;padding:0;width:100%;min-height:100%;}div{box-sizing:border-box}button,input,textarea{font:inherit;box-sizing:border-box}button{cursor:pointer}img{display:inline-block;vertical-align:middle}img[data-fit="cover"]{object-fit:cover}img[data-fit="contain"]{object-fit:contain}.mini-swiper-item{width:100%;height:100%;overflow:hidden}.mini-scroll-y{overflow-y:auto}.mini-scroll-x{overflow-x:auto}.preview-native-nav{display:flex;align-items:center;justify-content:space-between;padding:20px 20px 0;height:64px;background:white;color:#102a43;font-size:15px}.preview-native-nav span{font-size:22px}.preview-disclaimer{padding:20px;text-align:center;font-size:11px;line-height:1.7;color:#6d8193;background:#f3f7fa}[hidden]{display:none!important}'
    + css + '</style></head><body>' + nativeNav + createRenderer(nav)(tree) + '<div class="preview-disclaimer">离线布局预览 · 全部为示例数据<br>真实 WXML / WXSS 与页面数据逻辑转换；不替代微信开发者工具及真机验收。</div></body></html>'
  const output = path.join(directory, name + '.html')
  fs.writeFileSync(output, html)
  return { page: name, html: output, bytes: Buffer.byteLength(html) }
}

async function main() {
  const args = process.argv.slice(2)
  const arg = name => args.includes(name) ? args[args.indexOf(name) + 1] : null
  const requested = arg('--page')
  const supported = ['index', 'index-guest', 'index-manager', 'team', 'teams', 'verify', 'verify-empty', 'profile', 'login', 'match', 'match-knockout', 'match-three', 'match-four', 'match-awaiting', 'admin', 'admin-registration', 'admin-windows', 'admin-windows-four', 'admin-challenger', 'admin-challenger-four', 'admin-awaiting', 'admin-roster-locked', 'hall', 'hall-players', 'hall-empty', 'workbench', 'workbench-verify', 'workbench-rankapps', 'workbench-teams', 'workbench-matches', 'workbench-empty', 'workbench-error', 'champion', 'champion-edit', 'champion-readonly']
  if (requested && !supported.includes(requested)) throw new Error('Unsupported page: ' + requested)
  const directory = arg('--out') ? path.resolve(arg('--out')) : fs.mkdtempSync(path.join(os.tmpdir(), 'bjfu-layout-preview-'))
  const tempRoot = path.resolve(os.tmpdir())
  if (directory !== tempRoot && !directory.startsWith(tempRoot + path.sep)) throw new Error('Preview output must stay inside the system temporary directory')
  fs.mkdirSync(directory, { recursive: true })
  const outputs = []
  for (const name of requested ? [requested] : supported) outputs.push(await writePreview(name, directory))
  console.log(JSON.stringify({ note: 'Offline fixtures; no real API calls; layout preview only.', directory, outputs }, null, 2))
}

if (require.main === module) main().catch(error => { console.error(error); process.exitCode = 1 })
module.exports = { parseWxml, browserCss, pageData, writePreview, createRenderer }
