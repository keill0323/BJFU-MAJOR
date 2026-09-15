// Retired contact collection: creation, safe legacy responses and existing navigation.
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
      if (name.endsWith('/image-upload.js')) return { showImageError() { throw new Error('Unexpected image selection failure') } }
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

test('creation needs only a team name and ignores a stale contact draft', async () => {
  const waiting = deferred(), requests = []
  const { page } = load(mine, { createTeam: (...args) => { requests.push(args); return waiting.promise } })
  page.refreshAll = async () => {}
  await page.createTeam()
  assert.match(page.data.createTeamError, /队伍名称/)
  Object.assign(page.data, { teamName: ' 联系示例队 ', teamCaptainQq: '123456789012' })
  const pending = page.createTeam()
  await page.createTeam()
  assert.deepEqual(requests, [['联系示例队']])
  waiting.resolve(team({ captain_qq: '123456789012' }))
  await pending
  assert.equal(page.data.teamName, '')
  assert.equal(page.data.creatingTeam, false)
  assert.equal(page.data.myTeam.captain_qq, undefined)
})

test('failed creation preserves the name and permits a retry', async () => {
  let requests = 0
  const { page } = load(mine, { createTeam: async () => { requests++; throw { detail: '队伍名已存在' } } })
  page.data.teamName = '联系示例队'
  await page.createTeam()
  assert.equal(page.data.teamName, '联系示例队')
  assert.equal(page.data.createTeamError, '队伍名已存在')
  assert.equal(page.data.creatingTeam, false)
  await page.createTeam()
  assert.equal(requests, 2)
})

test('account changes and unload prevent late creation from restoring a team', async () => {
  for (const action of ['account', 'unload']) {
    const waiting = deferred()
    const { page, setToken, toasts } = load(mine, { createTeam: () => waiting.promise })
    page.data.teamName = '联系示例队'
    const pending = page.createTeam()
    if (action === 'account') setToken('another-login')
    else page.onUnload()
    waiting.resolve(team())
    await pending
    assert.equal(page.data.myTeam, null)
    assert.equal(toasts.length, 0)
  }
})

test('my team removes legacy contact while keeping team details and captain state', async () => {
  const { page } = load(mine, { getMyTeam: async () => team({ captain_qq: '123456789012' }),
    getMe: async () => ({ id: 2 }), getApplications: async () => [] })
  await page.loadMyTeam()
  assert.equal(page.data.myTeam.id, 8)
  assert.equal(page.data.isCaptain, true)
  assert.equal(page.data.myTeam.captain_qq, undefined)
})

for (const file of [directory, component]) {
  test(file + ': neither visitors nor signed-in viewers see legacy QQ', async () => {
    for (const token of ['', 'fixture-token']) {
      const { page, copied } = load(file, { getTeam: async () => team({ captain_qq: '123456789012' }) }, token)
      if (file === component) await page.loadTeam()
      else await page.viewTeam({ currentTarget: { dataset: { id: 8 } } })
      const result = file === component ? page.data.team : page.data.detailTeam
      assert.equal(result.id, 8)
      assert.equal(result.captain_qq, undefined)
      assert.doesNotMatch(render(page, file), /123456789012|联系 QQ|登录后可查看/)
      assert.equal(page.copyCaptainQq, undefined)
      assert.equal(copied.length, 0)
    }
  })
  test(file + ': late response after logout cannot restore private team data', async () => {
    const waiting = deferred()
    const { page, setToken } = load(file, { getTeam: () => waiting.promise })
    const pending = file === component ? page.loadTeam() : page.viewTeam({ currentTarget: { dataset: { id: 8 } } })
    setToken('')
    waiting.resolve(team({ captain_qq: '123456789012' }))
    await pending
    assert.equal(file === component ? page.data.team : page.data.detailTeam, null)
  })
}

test('team form and detail contain no contact collection, copy or reminder controls', () => {
  const { page, navigations } = captain()
  assert.doesNotMatch(render(page, mine), /QQ|补充联系|保存联系/)
  assert.match(render(page, mine), /消息中心/)
  page.goMessages()
  assert.deepEqual(navigations, ['/pages/messages/messages'])
  Object.assign(page.data, { myTeam: null })
  const html = render(page, mine)
  assert.match(html, /队伍名称/)
  assert.doesNotMatch(html, /QQ|常用联系/)
  assert.equal(page.openContactEditor, undefined)
  assert.equal(page.saveTeamContact, undefined)
})

test('create API sends only name and the retired contact wrapper is removed', async () => {
  const requests = [], module = { exports: {} }
  vm.runInNewContext(fs.readFileSync(path.join(root, 'utils/api.js'), 'utf8'), {
    module, wx: { getStorageSync: () => 'fixture-token', request: value => {
      requests.push(value)
      value.success({ statusCode: 201, data: team() })
    } }
  })
  await module.exports.createTeam('联系示例队', 'legacy ignored argument')
  assert.deepEqual(plain(requests[0].data), { name: '联系示例队' })
  assert.equal(module.exports.updateTeamContact, undefined)
})
