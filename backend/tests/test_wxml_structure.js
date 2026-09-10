const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const test = require('node:test')
const { parseWxml, createRenderer } = require('../../tools/preview-miniprogram.js')
const appRoot = path.resolve(__dirname, '../../miniprogram')

function wxmlFiles(directory) {
  return fs.readdirSync(directory, { withFileTypes: true }).flatMap(entry => {
    if (entry.name === 'node_modules') return []
    const file = path.join(directory, entry.name)
    return entry.isDirectory() ? wxmlFiles(file) : entry.name.endsWith('.wxml') ? [file] : []
  })
}

test('all project templates have valid conditional branch scopes, including hidden dialogs', () => {
  for (const file of wxmlFiles(appRoot)) {
    assert.doesNotThrow(() => parseWxml(fs.readFileSync(file, 'utf8')), path.relative(appRoot, file))
  }
})

test('preview rejects the reported else/loop compile error and accepts a wrapper block', () => {
  assert.throws(() => parseWxml('<view wx:if="{{loading}}"/><view wx:else wx:for="{{players}}"/>'), /cannot share a node with wx:for/)
  assert.throws(() => parseWxml('<view wx:if="{{loading}}"/><view wx:elif="{{ready}}" wx:for="{{players}}"/>'), /cannot share a node with wx:for/)
  assert.doesNotThrow(() => parseWxml('<view wx:if="{{loading}}"/><block wx:else><view wx:for="{{players}}"/></block>'))
  assert.throws(() => parseWxml('<view wx:if="{{loading}}"/><view/><view wx:else/>'), /wx:if not found/)
  assert.throws(() => parseWxml('<view wx:if="{{loading}}"/><block><view wx:else/></block>'), /wx:if not found/)
  assert.throws(() => parseWxml('<view wx:if="{{loading}}"/>非空文本<view wx:else/>'), /wx:if not found/)
  assert.throws(() => parseWxml('<view wx:if="{{item.ready}}" wx:for="{{players}}"/><view wx:else/>'), /wx:if not found/)
  assert.doesNotThrow(() => parseWxml('<view wx:if="{{item.ready}}" wx:for="{{players}}"/>'))
})

test('the actual talent-market template keeps loading, failure, empty and player states exclusive', () => {
  const tree = parseWxml(fs.readFileSync(path.join(appRoot, 'pages/team/team.wxml'), 'utf8'))
  function find(node) {
    if (node.attrs && node.attrs.class === 'market-scroll') return node
    for (const child of node.children || []) { const match = find(child); if (match) return match }
  }
  const market = find(tree)
  assert.ok(market)
  const render = state => createRenderer({})({ children: [market], scope: state })
  const base = {
    marketLoading: false, marketError: false, marketMatches: [{ id: 1 }],
    freePlayers: [{ id: 2, nickname: '测试选手甲', initial: '测', display_rank: 'A', score_text: 70 },
      { id: 3, nickname: '测试选手乙', initial: '测', display_rank: 'B', score_text: 60 }]
  }
  const loading = render({ ...base, marketLoading: true })
  assert.match(loading, /正在寻找/)
  assert.doesNotMatch(loading, /player-row/)
  const error = render({ ...base, marketError: true })
  assert.match(error, /暂时无法加载/)
  assert.doesNotMatch(error, /player-row/)
  const empty = render({ ...base, freePlayers: [] })
  assert.match(empty, /这场赛事暂时没有自由人/)
  assert.doesNotMatch(empty, /player-row/)
  assert.match(render({ ...base, freePlayers: [], marketMatches: [] }), /暂时没有可选择的赛事/)
  const players = render(base)
  assert.equal((players.match(/class="player-row"/g) || []).length, 2)
  assert.match(players, /测试选手甲/)
  assert.match(players, /测试选手乙/)
  assert.doesNotMatch(players, /sheet-empty/)
})
