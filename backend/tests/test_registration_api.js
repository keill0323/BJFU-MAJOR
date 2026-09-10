// Isolated API and page tests: never contact the configured production server.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const test = require('node:test')

const root = path.resolve(__dirname, '../..')

function loadApi(response) {
  const calls = { requests: [], removedTokens: [], modals: [], modalWork: [], toasts: [], todoInvalidations: 0 }
  const wx = {
    getStorageSync: () => 'isolated-test-token',
    removeStorageSync: key => calls.removedTokens.push(key),
    request(options) {
      calls.requests.push(options)
      options.success(response)
    },
    showToast: value => calls.toasts.push(value),
    showModal(value) {
      calls.modals.push(value)
      if (value.success) calls.modalWork.push(Promise.resolve(value.success({ confirm: true })))
    }
  }
  const module = { exports: {} }
  const file = path.join(root, 'miniprogram/utils/api.js')
  vm.runInNewContext(fs.readFileSync(file, 'utf8'), { module, wx }, { filename: file })
  return { api: module.exports, calls, wx }
}

function loadAdminPage(context) {
  let page
  const file = path.join(root, 'miniprogram/pages/admin/mdetail/mdetail.js')
  vm.runInNewContext(fs.readFileSync(file, 'utf8'), {
    Page: definition => { page = definition },
    require(name) {
      if (name.endsWith('/admin-todos.js')) return { invalidate: () => { context.calls.todoInvalidations++ } }
      if (name.endsWith('/tournament.js')) return require(path.join(root, 'miniprogram/utils/tournament.js'))
      assert.equal(name, '../../../utils/api.js')
      return context.api
    },
    wx: context.wx,
    console
  }, { filename: file })
  page.data = { ...page.data, matchId: 8, teams: [{
    team_id: 12, registration_status: 'pending', stage: 'challenger', seed: 0, group_name: null
  }] }
  page.setData = data => Object.assign(page.data, data)
  return page
}

test('team registration approval sends the match and team IDs to the batch endpoint', async () => {
  const { api, calls } = loadApi({ statusCode: 200, data: { message: '审核通过', data: { approved: 5 } } })
  const result = await api.approveTeamRegistration(8, 12)
  assert.equal(result.data.approved, 5)
  assert.equal(calls.requests.length, 1)
  assert.equal(calls.requests[0].method, 'POST')
  assert.equal(calls.requests[0].url, api.BASE + '/api/matches/8/registrations/approve-team?team_id=12')
})

test('missing batch endpoint gives a deployment message without retrying legacy approval', async () => {
  const { api, calls } = loadApi({ statusCode: 404, data: { detail: 'Not Found' } })
  await assert.rejects(api.approveTeamRegistration(8, 12), error => {
    assert.equal(error.code, 'BACKEND_UPGRADE_REQUIRED')
    assert.equal(error.statusCode, 404)
    assert.equal(error.detail, '当前服务器版本不支持整队报名审核，请更新后端后重试')
    return true
  })
  assert.equal(calls.requests.length, 1)
})

test('a real missing registration stays a business error', async () => {
  const { api, calls } = loadApi({ statusCode: 404, data: { detail: '该队伍未报名此赛事' } })
  await assert.rejects(api.approveTeamRegistration(8, 12), error => {
    assert.equal(error.detail, '该队伍未报名此赛事')
    assert.equal(error.statusCode, 404)
    assert.equal(error.code, undefined)
    return true
  })
  assert.equal(calls.requests.length, 1)
})

test('missing registration-time endpoint explains the required backend update and preserves input payload', async () => {
  const { api, calls } = loadApi({ statusCode: 404, data: { detail: 'Not Found' } })
  const values = { register_start: null, register_end: '2026-10-08T20:00:00' }
  await assert.rejects(api.updateRegistrationWindow(8, values), error => {
    assert.equal(error.code, 'BACKEND_UPGRADE_REQUIRED')
    assert.equal(error.detail, '当前服务器版本不支持报名时间设置，请更新后端后重试')
    return true
  })
  assert.equal(calls.requests.length, 1)
  assert.equal(calls.requests[0].method, 'PUT')
  assert.equal(calls.requests[0].url, api.BASE + '/api/matches/8/registration-window')
  assert.deepEqual(calls.requests[0].data, values)
})

test('registration-time business errors are not mislabeled as outdated backend versions', async () => {
  const { api } = loadApi({ statusCode: 404, data: { detail: '赛事不存在' } })
  await assert.rejects(api.updateRegistrationWindow(8, { register_start: null, register_end: null }), error => {
    assert.equal(error.detail, '赛事不存在')
    assert.equal(error.code, undefined)
    return true
  })
})

test('seed and group uses one atomic request and encodes custom group names', async () => {
  const { api, calls } = loadApi({ statusCode: 200, data: { data: { group_count: 3 } } })
  await api.seedAndGroup(8, ['A', '林区', 'B&C'])
  assert.equal(calls.requests.length, 1)
  assert.equal(calls.requests[0].method, 'POST')
  assert.equal(calls.requests[0].url, api.BASE + '/api/matches/8/seed-and-group?group_names=A&group_names=' + encodeURIComponent('林区') + '&group_names=B%26C')
})

test('missing atomic grouping endpoint never falls back to separately committed seed assignment', async () => {
  const { api, calls } = loadApi({ statusCode: 404, data: { detail: 'Not Found' } })
  await assert.rejects(api.seedAndGroup(8, ['A', 'B', 'C']), error => {
    assert.equal(error.code, 'BACKEND_UPGRADE_REQUIRED')
    assert.match(error.detail, /种子分配与分组/)
    return true
  })
  assert.equal(calls.requests.length, 1)
})

test('approval still surfaces permissions failures', async () => {
  const { api, calls } = loadApi({ statusCode: 403, data: { detail: '权限不足' } })
  await assert.rejects(api.approveTeamRegistration(8, 12), error => {
    assert.equal(error.detail, '权限不足')
    assert.equal(error.statusCode, 403)
    return true
  })
  assert.equal(calls.requests.length, 1)
})

test('expired authentication clears the token and never retries approval', async () => {
  const { api, calls } = loadApi({ statusCode: 401, data: { detail: '令牌无效' } })
  await assert.rejects(api.approveTeamRegistration(8, 12), error => {
    assert.equal(error.detail, '请先登录')
    assert.equal(error.unauthorized, true)
    assert.equal(error.statusCode, 401)
    return true
  })
  assert.deepEqual(calls.removedTokens, ['token'])
  assert.equal(calls.requests.length, 1)
})

test('admin approval button displays the deployment failure and never claims approval succeeded', async () => {
  const context = loadApi({ statusCode: 404, data: { detail: 'Not Found' } })
  const page = loadAdminPage(context)
  let reloads = 0
  page.load = () => { reloads += 1 }
  await page.approveTeamReg({ currentTarget: { dataset: { id: 12, name: '测试队伍' } } })
  await Promise.all(context.calls.modalWork)
  assert.equal(context.calls.modals[1].content, '当前服务器版本不支持整队报名审核，请更新后端后重试')
  assert.equal(context.calls.toasts.length, 0)
  assert.equal(reloads, 0)
  assert.equal(context.calls.requests.length, 1)
  assert.equal(context.calls.todoInvalidations, 0)
})

test('admin approval button refreshes the list only after successful approval', async () => {
  const context = loadApi({ statusCode: 200, data: { message: '已通过 5 条报名记录' } })
  const page = loadAdminPage(context)
  let reloads = 0
  page.load = () => { reloads += 1 }
  await page.approveTeamReg({ currentTarget: { dataset: { id: 12, name: '测试队伍' } } })
  await Promise.all(context.calls.modalWork)
  assert.equal(context.calls.toasts[0].title, '已通过')
  assert.equal(reloads, 1)
  assert.equal(context.calls.todoInvalidations, 1)
})
