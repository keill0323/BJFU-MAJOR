// Exercise the live score-entry page with isolated API responses; no network calls.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const test = require('node:test')

const root = path.resolve(__dirname, '../..')
const plain = value => JSON.parse(JSON.stringify(value))
const upgradeMessage = '冠军档案数据库尚未升级，比分未保存。请管理员完成冠军表升级后重试。'

function loadPage(updateRoundResult = async () => ({})) {
  let page
  const calls = { requests: [], modals: [], toasts: [], reloads: 0 }
  const file = path.join(root, 'miniprogram/pages/admin/mdetail/mdetail.js')
  vm.runInNewContext(fs.readFileSync(file, 'utf8'), {
    Page: definition => { page = definition },
    require(name) {
      if (name.endsWith('/tournament.js')) return require(path.join(root, 'miniprogram/utils/tournament.js'))
      assert.equal(name, '../../../utils/api.js')
      return {
        updateRoundResult: (id, data) => {
          calls.requests.push([id, plain(data)])
          return updateRoundResult(id, data)
        }
      }
    },
    wx: { showToast: value => calls.toasts.push(value), showModal: value => calls.modals.push(value) },
    console
  }, { filename: file })
  page.data = plain(page.data)
  page.data.knockoutRounds = [{ id: 42, group_name: '淘汰赛' }]
  page.setData = values => Object.assign(page.data, values)
  page.load = () => { calls.reloads++ }
  page.openScore({ currentTarget: { dataset: { id: 42 } } })
  assert.equal(page.data.isBo3, true)
  return { page, calls }
}

function enterGames(page, games) {
  games.forEach((value, index) => page.onBo3Input({
    currentTarget: { dataset: { index } }, detail: { value }
  }))
}

for (const [name, inputs, games, score] of [
  ['2-0', ['13-9', '13-8', ''], [{ t1: 13, t2: 9 }, { t1: 13, t2: 8 }], [2, 0]],
  ['2-1', ['13-9', '8-13', '13-7'], [{ t1: 13, t2: 9 }, { t1: 8, t2: 13 }, { t1: 13, t2: 7 }], [2, 1]]
]) {
  test(`BO3 ${name} submits map scores and derived series score through the active admin page`, async () => {
    const { page, calls } = loadPage()
    enterGames(page, inputs)
    await page.submitScore()
    assert.deepEqual(calls.requests, [[42, { team1_score: score[0], team2_score: score[1], bo3_scores: games }]])
    assert.equal(calls.modals.length, 0)
    assert.deepEqual(plain(calls.toasts), [{ title: '比分已更新', icon: 'success' }])
    assert.equal(calls.reloads, 1)
    assert.equal(page.data.showScoreInput, false)
    assert.equal(page.data.scoreRoundId, null)
    assert.deepEqual(plain(page.data.bo3Games), ['', '', ''])
  })
}

test('champion-schema 503 displays the upgrade detail and retains scores until a successful retry', async () => {
  let attempts = 0
  let resolveRetry
  const { page, calls } = loadPage(() => {
    attempts++
    if (attempts === 1) return Promise.reject({ statusCode: 503, detail: upgradeMessage })
    return new Promise(resolve => { resolveRetry = resolve })
  })
  const inputs = ['13-9', '8-13', '13-7']
  enterGames(page, inputs)
  await page.submitScore()
  assert.equal(calls.requests.length, 1)
  assert.equal(calls.modals.length, 1)
  assert.equal(calls.modals[0].content, upgradeMessage)
  assert.equal(calls.modals[0].showCancel, false)
  assert.equal(calls.toasts.length, 0)
  assert.equal(calls.reloads, 0)
  assert.equal(page.data.showScoreInput, true)
  assert.equal(page.data.scoreRoundId, 42)
  assert.deepEqual(plain(page.data.bo3Games), inputs)

  const retry = page.submitScore()
  assert.equal(calls.requests.length, 2)
  assert.deepEqual(calls.requests[1], calls.requests[0])
  assert.equal(page.data.showScoreInput, true)
  assert.deepEqual(plain(page.data.bo3Games), inputs)
  assert.equal(calls.toasts.length, 0)
  assert.equal(calls.reloads, 0)
  resolveRetry({})
  await retry
  assert.equal(calls.modals.length, 1)
  assert.deepEqual(plain(calls.toasts), [{ title: '比分已更新', icon: 'success' }])
  assert.equal(calls.reloads, 1)
  assert.equal(page.data.showScoreInput, false)
  assert.equal(page.data.scoreRoundId, null)
  assert.deepEqual(plain(page.data.bo3Games), ['', '', ''])
})
