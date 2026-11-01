// Score entry must stay reachable on long schedules and with the native keyboard open.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const test = require('node:test')
const { parseWxml, createRenderer } = require('../../tools/preview-miniprogram.js')

const root = path.resolve(__dirname, '../..')
const base = path.join(root, 'miniprogram/pages/admin/mdetail/mdetail')
const template = fs.readFileSync(base + '.wxml', 'utf8')
const tree = parseWxml(template)
const plain = value => JSON.parse(JSON.stringify(value))
const tap = dataset => ({ currentTarget: { dataset } })

function loadPage({ detail, save = async () => ({}), windowHeight = 568 } = {}) {
  let page
  const calls = { saves: [], toasts: [], modals: [], keyboardHides: 0 }
  vm.runInNewContext(fs.readFileSync(base + '.js', 'utf8'), {
    Page: value => { page = value },
    require(name) {
      if (name.endsWith('/tournament.js')) return require(path.join(root, 'miniprogram/utils/tournament.js'))
      assert.equal(name, '../../../utils/api.js')
      return {
        adminMatchDetail: async () => detail,
        updateRoundResult: (id, scores) => {
          calls.saves.push([id, plain(scores)])
          return save()
        }
      }
    },
    wx: {
      getWindowInfo: () => ({ windowHeight }),
      hideKeyboard: () => { calls.keyboardHides++ },
      showToast: value => calls.toasts.push(value),
      showModal: value => calls.modals.push(value)
    }
  }, { filename: base + '.js' })
  page.data = plain(page.data)
  page.setData = (values, callback) => {
    Object.assign(page.data, values)
    if (callback) callback()
  }
  return { page, calls }
}

function nodes(node, predicate) {
  return (predicate(node) ? [node] : []).concat(...(node.children || []).map(child => nodes(child, predicate)))
}

const hasClass = name => node => (node.attrs?.class || '').split(' ').includes(name)

test('all 24 group games render in page flow, including the final D-group scoring button', async () => {
  const rounds = ['A', 'B', 'C', 'D'].flatMap((group_name, group) => Array.from({ length: 6 }, (_, game) => ({
    id: group * 6 + game + 1, group_name, round_number: game + 1,
    team1_id: group * 3 + 1, team2_id: group * 3 + 2,
    team1_name: group_name + '队甲', team2_name: group_name + '队乙', status: 'pending'
  })))
  const { page } = loadPage({ detail: { match: { id: 8, name: '周末赛', status: 'ongoing' }, teams: [], rounds } })
  await page.load()
  page.switchTab(tap({ tab: 'challenger' }))
  const html = createRenderer({})({ children: tree.children, scope: page.data })
  assert.equal((html.match(/>录比分<\/div>/g) || []).length, 24)
  assert.match(html, /D队乙/)
  page.openScore(tap({ id: 24 }))
  assert.equal(page.data.scoreRoundId, 24)
  assert.equal(page.data.scoreTeam2Name, 'D队乙')
  assert.equal(nodes(tree, node => node.tag === 'swiper' || node.tag === 'swiper-item').length, 0)
  for (const list of nodes(tree, hasClass('admin-round-list'))) {
    assert.equal(list.tag, 'view')
    assert.equal(list.attrs['scroll-y'], undefined)
  }
})

test('legend tabs show only the selected zone while every row is in normal page flow', async () => {
  const rounds = ['上区', '下区'].flatMap((group_name, group) => Array.from({ length: 6 }, (_, game) => ({
    id: group * 6 + game + 100, group_name, round_number: game + 1,
    team1_id: 1, team2_id: 2, team1_name: group_name + '选手甲', team2_name: group_name + '选手乙'
  })))
  const { page } = loadPage({ detail: { match: { id: 8 }, teams: [], rounds } })
  await page.load()
  page.switchTab(tap({ tab: 'legend' }))
  page.switchLegendStage(tap({ idx: 1 }))
  const html = createRenderer({})({ children: tree.children, scope: page.data })
  assert.equal((html.match(/>录比分<\/div>/g) || []).length, 6)
  assert.match(html, /下区选手甲/)
  assert.doesNotMatch(html, /上区选手甲/)
})

test('knockout labels identify first-round matches and exact semifinal dependencies without claiming same-division games', async () => {
  const rounds = Array.from({ length: 4 }, (_, i) => ({
    id: i + 1, group_name: '淘汰赛', round_number: i + 1,
    team1_id: i + 1, team2_id: i + 5, team1_name: '队甲', team2_name: '队乙', status: 'pending'
  }))
  const { page } = loadPage({ detail: { match: { id: 8 }, teams: [], rounds: rounds.slice().reverse() } })
  await page.load()
  assert.deepEqual(plain(page.data.knockoutGroups.map(group => group.rounds.map(round => [round.id, round.ko_label]))), [
    [[1, '第一场'], [2, '第二场']],
    [[3, '上区第1 vs 第二场胜者'], [4, '下区第1 vs 第一场胜者']]
  ])
})

test('score form scrolls natively without a touchmove-catching ancestor; confirm stays outside it', () => {
  const panel = nodes(tree, node => (node.attrs?.class || '').startsWith('score-panel '))[0]
  const mask = nodes(tree, node => node.attrs?.class === 'mask score-mask')[0]
  assert.ok(panel)
  assert.equal(mask.attrs.catchtouchmove, undefined)
  assert.equal(panel.attrs.catchtouchmove, undefined)
  const scroll = nodes(panel, hasClass('score-form-scroll'))[0]
  assert.equal(scroll.tag, 'scroll-view')
  assert.ok('scroll-y' in scroll.attrs)
  assert.equal(nodes(scroll, node => node.attrs?.bindtap === 'submitScore').length, 0)
  const footer = panel.children.find(hasClass('score-footer'))
  const confirm = nodes(footer, node => node.attrs?.bindtap === 'submitScore')[0]
  assert.equal(confirm.tag, 'button')
  assert.equal(confirm.attrs.disabled, '{{scoreSaving}}')
  for (const input of nodes(scroll, node => node.tag === 'input')) {
    assert.equal(input.attrs['adjust-position'], '{{false}}')
    assert.equal(input.attrs.bindkeyboardheightchange, 'onScoreKeyboardHeightChange')
    assert.equal(input.attrs.bindfocus, 'onScoreFocus')
  }
})

test('small-screen BO3 form avoids keyboard and scrolls to the focused third game', () => {
  const { page } = loadPage()
  page.data.knockoutRounds = [{ id: 42, group_name: '淘汰赛', team1_name: '队一', team2_name: '队二' }]
  page.openScore(tap({ id: 42 }))
  page.onScoreFocus(tap({ focusId: 'score-game-2' }))
  page.onScoreKeyboardHeightChange({ detail: { height: 300 } })
  assert.equal(page.data.scoreKeyboardHeight, 300)
  assert.ok(page.data.scorePanelHeight + 300 < 568)
  assert.equal(page.data.scoreFocusId, 'score-game-2')
  assert.equal(page.data.scoreTeam1Name, '队一')
  page.onScoreKeyboardHeightChange({ detail: { height: 0 } })
  assert.equal(page.data.scorePanelHeight, Math.floor(568 * 0.82))
  page.closeScoreInput()
  page.onScoreKeyboardHeightChange({ detail: { height: 300 } })
  assert.equal(page.data.scoreKeyboardHeight, 0)
  assert.equal(page.data.scoreFocusId, '')
})

test('very short keyboard viewport keeps the form scrollable and gives the footer priority', () => {
  const { page } = loadPage({ windowHeight: 440 })
  page.data.knockoutRounds = [{ id: 42, group_name: '淘汰赛' }]
  page.openScore(tap({ id: 42 }))
  page.onScoreKeyboardHeightChange({ detail: { height: 300 } })
  assert.equal(page.data.scorePanelHeight, 128)
  const panel = nodes(tree, node => (node.attrs?.class || '').startsWith('score-panel '))[0]
  const html = createRenderer({})({ children: [panel], scope: page.data })
  assert.match(html, /score-panel-compact/)
  assert.match(html, /score-form-scroll mini-scroll-y/)
  assert.match(html, /确认比分/)
})

test('BO1 saves once despite repeated confirm/close taps and preserves the selected round during submission', async () => {
  let finishSave
  const { page, calls } = loadPage({ save: () => new Promise(resolve => { finishSave = resolve }) })
  page.load = async () => {}
  page.data.challengerRounds = [{ id: 6, group_name: 'A', round_number: 6, team1_name: '甲队', team2_name: '乙队' }]
  page.openScore(tap({ id: 6 }))
  assert.equal(page.data.isBo3, false)
  assert.equal(page.data.scoreMatchLabel, 'A 组 · 第 6 场')
  page.onScoreInput({ detail: { value: '13-0' } })
  const pending = page.submitScore()
  await page.submitScore()
  page.closeScoreInput()
  assert.equal(calls.saves.length, 1)
  assert.equal(page.data.showScoreInput, true)
  assert.equal(page.data.scoreRoundId, 6)
  assert.equal(page.data.scoreSaving, true)
  finishSave()
  await pending
  assert.deepEqual(calls.saves, [[6, { team1_score: 13, team2_score: 0 }]])
  assert.equal(page.data.showScoreInput, false)
  assert.equal(page.data.scoreSaving, false)
  assert.equal(calls.keyboardHides, 1)
})

test('validation and server errors release score controls while retaining the entered score', async () => {
  const { page, calls } = loadPage({ save: async () => { throw { detail: '该对阵暂不可录分' } } })
  page.data.legendRounds = [{ id: 9, group_name: '上区' }]
  page.openScore(tap({ id: 9 }))
  await page.submitScore()
  assert.equal(calls.saves.length, 0)
  assert.equal(page.data.scoreSaving, false)
  page.onScoreInput({ detail: { value: '13-7' } })
  await page.submitScore()
  assert.equal(page.data.scoreSaving, false)
  assert.equal(page.data.showScoreInput, true)
  assert.equal(page.data.scoreInput, '13-7')
  assert.equal(calls.modals[0].content, '该对阵暂不可录分')
})

for (const success of [true, false]) {
  test(`leaving the page prevents a late ${success ? 'successful' : 'failed'} save from affecting the next page`, async () => {
    let complete
    const { page, calls } = loadPage({ save: () => new Promise((resolve, reject) => {
      complete = () => success ? resolve() : reject({ detail: '稍后返回的错误' })
    }) })
    page.data.challengerRounds = [{ id: 9, group_name: 'A' }]
    page.openScore(tap({ id: 9 }))
    page.onScoreInput({ detail: { value: '13-7' } })
    const saving = page.submitScore()
    page.onUnload()
    page.setData = () => assert.fail('unloaded page must not receive render updates')
    page.load = () => assert.fail('unloaded page must not issue another detail request')
    page.onScoreKeyboardHeightChange({ detail: { height: 0 } })
    complete()
    await saving
    assert.equal(calls.saves.length, 1)
    assert.equal(calls.modals.length, 0)
    assert.equal(calls.toasts.length, 0)
    assert.equal(calls.keyboardHides, 0)
  })
}

test('a queued keyboard layout callback cannot update an unloaded page', () => {
  const { page } = loadPage()
  page.data.knockoutRounds = [{ id: 42, group_name: '淘汰赛' }]
  page.openScore(tap({ id: 42 }))
  let finishLayout
  page.setData = (values, callback) => { Object.assign(page.data, values); finishLayout = callback }
  page.onScoreKeyboardHeightChange({ detail: { height: 300 } })
  page.onUnload()
  page.setData = () => assert.fail('late keyboard callback must be ignored')
  finishLayout()
})
