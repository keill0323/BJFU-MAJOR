// Isolated UI regression checks: fictional contacts, no network or real clipboard.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const test = require('node:test')
const { parseWxml, createRenderer } = require('../../tools/preview-miniprogram.js')
const root = path.resolve(__dirname, '../../miniprogram')
const mine = 'pages/team/team'
const directory = 'pages/teams/teams'
const component = 'components/team-profile/team-profile'
const plain = value => JSON.parse(JSON.stringify(value))
const team = (overrides = {}) => ({ id: 8, captain_id: 2, name: '联系示例队', members: [], status: 'approved', captain_qq: null, ...overrides })
function deferred() {
  let resolve, reject
  const promise = new Promise((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}
function load(file = mine, apis = {}, initialToken = 'qq-test-token') {
  let definition, token = initialToken
  const copied = [], toasts = [], navigations = []
  const api = { BASE: 'https://example.test', ...apis }
  vm.runInNewContext(fs.readFileSync(path.join(root, file + '.js'), 'utf8'), {
    Page: value => { definition = value }, Component: value => { definition = value }, console,
    require(name) {
      if (name.endsWith('/api.js')) return api
      if (name.endsWith('/rank.js')) return require(path.join(root, 'utils/rank.js'))
      throw new Error('Unexpected module: ' + name)
    },
    wx: { getStorageSync: key => key === 'token' ? token : '', setClipboardData: value => copied.push(value.data),
      showToast: value => toasts.push(value.title), navigateTo: value => navigations.push(value.url) }
  })
  const page = { ...definition, ...definition.methods, data: plain(definition.data) }
  page.setData = values => {
    for (const [key, value] of Object.entries(values)) {
      const keys = key.split('.')
      let current = page.data
      for (const part of keys.slice(0, -1)) current = current[part]
      current[keys[keys.length - 1]] = value
    }
  }
  if (definition.lifetimes) { page.data.teamId = 8; definition.lifetimes.created.call(page) }
  return { page, copied, toasts, navigations, setToken: value => { token = value } }
}
function render(page, file) {
  const tree = parseWxml(fs.readFileSync(path.join(root, file + '.wxml'), 'utf8'))
  tree.scope = page.data
  return createRenderer({})(tree)
}
function captain(apis) {
  const result = load(mine, apis)
  Object.assign(result.page.data, { myTeam: team(), isCaptain: true, loggedIn: true, loading: false })
  return result
}

test('new team rejects missing and invalid QQ without making a request', async () => {
  let requests = 0
  const { page } = load(mine, { createTeam: async () => { requests++; return team() } })
  page.data.teamName = '联系示例队'
  for (const value of ['', ' ', '1234', '1234567890123', '012345', '１２３４５', '١٢٣٤٥', '12 345', 'abcde']) {
    page.data.teamCaptainQq = value
    await page.createTeam()
    assert.match(page.data.createTeamError, /QQ/)
    assert.equal(page.data.creatingTeam, false)
    assert.equal(page.data.teamCaptainQq, value)
  }
  assert.equal(requests, 0)
})

test('creation sends trimmed contact once, clears it only after success, and refreshes team', async () => {
  const waiting = deferred(), requests = []
  const { page } = load(mine, { createTeam: (...args) => { requests.push(args); return waiting.promise } })
  let refreshed = 0
  page.refreshAll = async () => { refreshed++ }
  Object.assign(page.data, { teamName: ' 联系示例队 ', teamCaptainQq: ' 123456789012 ' })
  const pending = page.createTeam()
  await page.createTeam()
  assert.equal(page.data.creatingTeam, true)
  assert.deepEqual(requests, [['联系示例队', '123456789012']])
  waiting.resolve(team({ captain_qq: '123456789012' }))
  await pending
  assert.equal(page.data.myTeam.captain_qq, '123456789012')
  assert.equal(page.data.teamCaptainQq, '')
  assert.equal(page.data.creatingTeam, false)
  assert.equal(refreshed, 1)
})

test('failed creation preserves name and QQ and permits a retry', async () => {
  let requests = 0
  const { page } = load(mine, { createTeam: async () => { requests++; throw { detail: '队伍名已存在' } } })
  Object.assign(page.data, { teamName: '联系示例队', teamCaptainQq: '12345' })
  await page.createTeam()
  assert.equal(page.data.teamName, '联系示例队')
  assert.equal(page.data.teamCaptainQq, '12345')
  assert.equal(page.data.createTeamError, '队伍名已存在')
  assert.equal(page.data.creatingTeam, false)
  await page.createTeam()
  assert.equal(requests, 2)
})

test('a creation response from a previous login cannot install its team', async () => {
  const waiting = deferred()
  const { page, setToken, toasts } = load(mine, { createTeam: () => waiting.promise })
  Object.assign(page.data, { teamName: '联系示例队', teamCaptainQq: '12345' })
  const pending = page.createTeam()
  setToken('another-login')
  waiting.resolve(team({ captain_qq: '12345' }))
  await pending
  assert.equal(page.data.myTeam, null)
  assert.equal(toasts.length, 0)
})

test('only a logged-in captain can open or save the contact form', async () => {
  for (const [loggedIn, isCaptain] of [[false, true], [true, false]]) {
    let requests = 0
    const { page, setToken } = captain({ updateTeamContact: async () => { requests++ } })
    if (!loggedIn) setToken('')
    page.data.isCaptain = isCaptain
    page.openContactEditor()
    assert.equal(page.data.showContactEditor, false)
    Object.assign(page.data, { showContactEditor: true, captainQqDraft: '12345' })
    await page.saveTeamContact()
    assert.equal(requests, 0)
  }
})

test('legacy captain can fill QQ without replacing roster, score, or team description', async () => {
  const waiting = deferred(), requests = []
  const { page } = captain({ updateTeamContact: (...args) => { requests.push(args); return waiting.promise } })
  Object.assign(page.data.myTeam, { display_rating: 330, description: '保留简介', members: [{ user_id: 2 }] })
  page.openContactEditor()
  assert.equal(page.data.captainQqDraft, '')
  page.onContactQqInput({ detail: { value: ' 987654321 ' } })
  const pending = page.saveTeamContact()
  await page.saveTeamContact()
  assert.deepEqual(requests, [[8, '987654321']])
  waiting.resolve(team({ captain_qq: '987654321', description: '不会覆盖的响应简介' }))
  await pending
  assert.equal(page.data.myTeam.captain_qq, '987654321')
  assert.equal(page.data.myTeam.display_rating, 330)
  assert.equal(page.data.myTeam.description, '保留简介')
  assert.deepEqual(plain(page.data.myTeam.members), [{ user_id: 2 }])
  assert.equal(page.data.showContactEditor, false)
})

test('invalid or failed contact updates retain existing contact and draft', async () => {
  let requests = 0
  const { page } = captain({ updateTeamContact: async () => { requests++; throw { detail: '网络错误' } } })
  page.data.myTeam.captain_qq = '12345'
  page.openContactEditor()
  page.data.captainQqDraft = ''
  await page.saveTeamContact()
  assert.equal(requests, 0)
  assert.match(page.data.contactError, /QQ/)
  page.data.captainQqDraft = '987654321'
  await page.saveTeamContact()
  assert.equal(page.data.myTeam.captain_qq, '12345')
  assert.equal(page.data.captainQqDraft, '987654321')
  assert.equal(page.data.contactError, '网络错误')
  assert.equal(page.data.contactSaving, false)
})

test('closing, switching team, logout, or unload rejects late contact success', async () => {
  for (const action of ['close', 'team', 'logout', 'unload']) {
    const waiting = deferred()
    const { page, setToken, toasts } = captain({ updateTeamContact: () => waiting.promise })
    page.openContactEditor()
    page.data.captainQqDraft = '12345'
    const pending = page.saveTeamContact()
    if (action === 'close') page.closeContactEditor()
    if (action === 'team') page.data.myTeam = team({ id: 9 })
    if (action === 'logout') setToken('')
    if (action === 'unload') page.onUnload()
    waiting.resolve(team({ captain_qq: '12345' }))
    await pending
    assert.equal(page.data.myTeam.captain_qq, null, action)
    assert.equal(toasts.length, 0, action)
  }
})

test('a refresh begun before save cannot restore the previous QQ', async () => {
  const waiting = deferred()
  const { page } = captain({ getMyTeam: () => waiting.promise, getMe: async () => ({ id: 2 }),
    getApplications: async () => [], updateTeamContact: async () => team({ captain_qq: '987654321' }) })
  page._teamDataToken = 'qq-test-token'
  page.data.myTeam.captain_qq = '12345'
  const refresh = page.loadMyTeam()
  page.openContactEditor()
  page.data.captainQqDraft = '987654321'
  await page.saveTeamContact()
  waiting.resolve(team({ captain_qq: '12345' }))
  await refresh
  assert.equal(page.data.myTeam.captain_qq, '987654321')
})

for (const file of [directory, component]) {
  const open = page => file === component ? page.loadTeam() : page.viewTeam({ currentTarget: { dataset: { id: 8 } } })
  const displayed = page => file === component ? page.data.team : page.data.detailTeam
  test(file + ': signed-in detail shows and copies only the returned QQ', async () => {
    const { page, copied } = load(file, { getTeam: async () => team({ captain_qq: '123456789012' }) })
    await open(page)
    assert.equal(displayed(page).captain_qq, '123456789012')
    assert.match(render(page, file), /123456789012/)
    page.copyCaptainQq()
    assert.deepEqual(copied, ['123456789012'])
  })
  test(file + ': guests cannot display or copy even an unexpected QQ in a response', async () => {
    const { page, copied } = load(file, { getTeam: async () => team({ captain_qq: '123456789012' }) }, '')
    await open(page)
    assert.equal(displayed(page).captain_qq, '')
    assert.doesNotMatch(render(page, file), /123456789012/)
    assert.match(render(page, file), /登录后可查看/)
    page.copyCaptainQq()
    assert.deepEqual(copied, [])
  })
  test(file + ': missing or invalid contact is never guessed from other user fields', async () => {
    for (const value of [null, '', 'bad-contact']) {
      const { page, copied } = load(file, { getTeam: async () => team({ captain_qq: value,
        members: [{ user_id: 2, student_id: '999999999', game_id: '888888888' }] }) })
      await open(page)
      assert.equal(displayed(page).captain_qq, '')
      assert.match(render(page, file), /队长暂未补充/)
      page.copyCaptainQq()
      assert.deepEqual(copied, [])
    }
  })
  test(file + ': logout during detail loading discards the previous login response', async () => {
    const waiting = deferred()
    const { page, setToken } = load(file, { getTeam: () => waiting.promise })
    const pending = open(page)
    setToken('')
    waiting.resolve(team({ captain_qq: '123456789012' }))
    await pending
    assert.equal(displayed(page), null)
  })
}

test('API sends captain_qq in create and contact requests and explains an old backend', async () => {
  const requests = [], exports = { exports: {} }
  let status = 200
  vm.runInNewContext(fs.readFileSync(path.join(root, 'utils/api.js'), 'utf8'), {
    module: exports, wx: { getStorageSync: () => 'qq-test-token', request: value => {
      requests.push(value)
      value.success({ statusCode: status, data: status === 200 ? team() : { detail: 'Not Found' } })
    } }
  })
  await exports.exports.createTeam('联系示例队', '12345')
  await exports.exports.updateTeamContact(8, '987654321')
  assert.deepEqual(plain(requests.map(value => [value.url.replace(/^https?:\/\/[^/]+/, ''), value.method, value.data])), [
    ['/api/teams', 'POST', { name: '联系示例队', captain_qq: '12345' }],
    ['/api/teams/8/contact', 'PUT', { captain_qq: '987654321' }]
  ])
  status = 404
  await assert.rejects(exports.exports.updateTeamContact(8, '987654321'), error => /更新后端/.test(error.detail))
})

test('creation and legacy captain forms render clear required and login-visibility labels', () => {
  const { page } = captain()
  assert.match(render(page, mine), /补充 QQ/)
  page.openContactEditor()
  assert.match(render(page, mine), /保存 QQ/)
  assert.match(render(page, mine), /登录/)
  Object.assign(page.data, { myTeam: null, showContactEditor: false })
  const html = render(page, mine)
  assert.match(html, /队长 QQ/)
  assert.match(html, /必填/)
  assert.match(html, /登录/)
})
