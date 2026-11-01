// Exercise the actual admin page and API mapping with isolated wx requests.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const test = require('node:test')

const root = path.resolve(__dirname, '../..')

function setup(respond = options => options.success({ statusCode: 200, data: { message: '对阵生成完成' } })) {
  const calls = { requests: [], toasts: [], modals: [], loads: 0 }
  const wx = {
    getStorageSync: () => 'isolated-test-token',
    request(options) {
      calls.requests.push(options)
      respond(options, calls.requests.length)
    },
    showToast: options => calls.toasts.push(options),
    showModal: options => calls.modals.push(options)
  }
  const module = { exports: {} }
  const apiFile = path.join(root, 'miniprogram/utils/api.js')
  vm.runInNewContext(fs.readFileSync(apiFile, 'utf8'), { module, wx }, { filename: apiFile })
  let page
  const pageFile = path.join(root, 'miniprogram/pages/admin/mdetail/mdetail.js')
  vm.runInNewContext(fs.readFileSync(pageFile, 'utf8'), {
    wx,
    Page: value => { page = value },
    require(name) {
      if (name === '../../../utils/api.js') return module.exports
      if (name === '../../../utils/tournament.js') return require(path.join(root, 'miniprogram/utils/tournament.js'))
      throw new Error('Unexpected module: ' + name)
    }
  }, { filename: pageFile })
  page.data = {
    ...page.data,
    matchId: 8,
    challengerTeams: ['A', 'B', 'C', 'D'].flatMap(group_name => Array.from({ length: 3 }, () => ({ group_name })))
  }
  page.setData = values => Object.assign(page.data, values)
  page.load = async () => { calls.loads++ }
  return { page, calls, api: module.exports }
}

test('the one-click entry requests double round BO1 once for each distinct group', async () => {
  const { page, calls, api } = setup()
  await page.generateAllMatches()
  assert.deepEqual(calls.requests.map(request => [request.method, request.url]), ['A', 'B', 'C', 'D'].map(group => [
    'POST', api.BASE + '/api/matches/8/auto-matches/double?group_name=' + group
  ]))
  assert.equal(calls.loads, 1)
  assert.equal(page.data.generatingMatches, false)
  assert.match(calls.toasts[0].title, /双循环 BO1/)
  const template = fs.readFileSync(path.join(root, 'miniprogram/pages/admin/mdetail/mdetail.wxml'), 'utf8')
  assert.match(template, /bindtap="generateAllMatches">\{\{generatingMatches \? '正在生成…' : '③生成双循环 BO1'\}\}/)
})

test('groups with existing rounds are untouched, including previously generated single rounds', async () => {
  const { page, calls } = setup()
  const oldRounds = [{ id: 91, group_name: 'A', team1_score: 13, team2_score: 7 }, { id: 92, group_name: 'C' }]
  page.data.challengerRounds = oldRounds
  await page.generateAllMatches()
  assert.deepEqual(calls.requests.map(request => request.url.split('group_name=')[1]), ['B', 'D'])
  assert.equal(page.data.challengerRounds, oldRounds)
  assert.deepEqual(oldRounds, [{ id: 91, group_name: 'A', team1_score: 13, team2_score: 7 }, { id: 92, group_name: 'C' }])
})

test('an existing schedule never sends a generator or reset request', async () => {
  const { page, calls } = setup()
  page.data.challengerRounds = ['A', 'B', 'C', 'D'].map(group_name => ({ group_name }))
  await page.generateAllMatches()
  assert.equal(calls.requests.length, 0)
  assert.equal(calls.toasts[0].title, '各组对阵已存在')
})

test('failed generation reports the server reason, refreshes completed groups and releases the busy guard', async () => {
  const { page, calls } = setup((options, number) => options.success(number === 1
    ? { statusCode: 200, data: {} }
    : { statusCode: 400, data: { detail: '「B」组已经有对阵，请勿重复生成' } }))
  await page.generateAllMatches()
  assert.equal(calls.requests.length, 2)
  assert.equal(calls.modals[0].content, '「B」组已经有对阵，请勿重复生成')
  assert.equal(calls.loads, 1)
  assert.equal(page.data.generatingMatches, false)
  assert.equal(calls.toasts.length, 0)
})

test('rapid repeated clicks cannot start another generation loop', async () => {
  let firstRequest
  const { page, calls } = setup((options, number) => {
    if (number === 1) firstRequest = options
    else options.success({ statusCode: 200, data: {} })
  })
  const work = page.generateAllMatches()
  assert.equal(page.data.generatingMatches, true)
  await page.generateAllMatches()
  assert.equal(calls.requests.length, 1)
  firstRequest.success({ statusCode: 200, data: {} })
  await work
  assert.equal(calls.requests.length, 4)
  assert.equal(page.data.generatingMatches, false)
})

test('generation stays guarded until the schedule refresh finishes', async () => {
  let completeRefresh
  let notifyRefreshStarted
  const refreshStarted = new Promise(resolve => { notifyRefreshStarted = resolve })
  const { page, calls } = setup()
  page.load = () => new Promise(resolve => {
    completeRefresh = resolve
    notifyRefreshStarted()
  })
  const work = page.generateAllMatches()
  await refreshStarted
  await page.generateAllMatches()
  assert.equal(calls.requests.length, 4)
  assert.equal(page.data.generatingMatches, true)
  completeRefresh()
  await work
  assert.equal(page.data.generatingMatches, false)
})

test('missing grouping does not send requests or enter the busy state', async () => {
  const { page, calls } = setup()
  page.data.challengerTeams = [{ group_name: null }]
  await page.generateAllMatches()
  assert.equal(calls.requests.length, 0)
  assert.equal(calls.loads, 0)
  assert.equal(page.data.generatingMatches, false)
  assert.equal(calls.toasts[0].title, '请先分组再生成对阵')
})
