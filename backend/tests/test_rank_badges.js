const assert = require('node:assert/strict')
const test = require('node:test')
const fs = require('node:fs')
const path = require('node:path')
const { rankBadge } = require('../../miniprogram/utils/rank.js')
const { pageData, parseWxml, createRenderer } = require('../../tools/preview-miniprogram.js')
const appRoot = path.resolve(__dirname, '../../miniprogram')

test('every real base/plus/double-plus rank has its own existing local badge', () => {
  const cases = { D: 'd', C: 'c', 'C+': 'c-plus', 'C++': 'c-double-plus', B: 'b', 'B+': 'b-plus', 'B++': 'b-double-plus', A: 'a', 'A+': 'a-plus', 'A++': 'a-double-plus' }
  const paths = new Set()
  for (const [rank, key] of Object.entries(cases)) {
    const badge = rankBadge(rank)
    assert.equal(badge.key, key)
    assert.equal(badge.label, rank)
    assert.equal(badge.stars, null)
    assert.ok(fs.existsSync(path.join(appRoot, badge.icon)))
    paths.add(badge.icon)
  }
  assert.equal(paths.size, Object.keys(cases).length)
})

test('S badge categories switch at the real 10, 25 and 50 star thresholds', () => {
  for (const [stars, key] of [[0, 's'], [9, 's'], [10, 's-gold'], [24, 's-gold'], [25, 's-diamond'], [49, 's-diamond'], [50, 's-demon']]) {
    const badge = rankBadge('S' + stars)
    assert.equal(badge.key, key)
    assert.equal(badge.stars, stars)
    assert.equal(badge.progress, stars * 2)
    assert.ok(badge.label.endsWith(stars + '星'))
    assert.ok(fs.existsSync(path.join(appRoot, badge.icon)))
  }
  assert.equal(rankBadge('S').key, 's')
  for (const rank of [null, '', 'D+', 'S51', 'S-1', 'A+++', '../anything']) {
    assert.equal(rankBadge(rank).key, 'unranked')
    assert.equal(rankBadge(rank).label, '待认证')
  }
  assert.ok(fs.existsSync(path.join(appRoot, rankBadge(null).icon)))
})

test('home, verification and profile render the same actual player badge alongside the rank text', async () => {
  const badgeClasses = { index: 'hero-rank-icon', verify: 'rank-emblem', profile: 'profile-rank-icon' }
  for (const [page, className] of Object.entries(badgeClasses)) {
    const fixture = await pageData(page)
    assert.equal(fixture.data.user.rank_badge.key, 's-diamond')
    const tree = parseWxml(fs.readFileSync(path.join(appRoot, 'pages', page, page + '.wxml'), 'utf8'))
    tree.scope = fixture.data
    const html = createRenderer(fixture.nav)(tree)
    assert.match(html, new RegExp('<img class="' + className + '"[^>]+src="data:image/svg\\+xml;base64,'))
    assert.match(html, /钻S32星/)
  }
})
