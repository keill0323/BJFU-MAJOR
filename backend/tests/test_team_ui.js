const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const test = require('node:test')

const root = path.resolve(__dirname, '../..')

function loadPage(name, api, storage = () => 'test-token') {
  let page
  const file = path.join(root, 'miniprogram/pages', name, name + '.js')
  vm.runInNewContext(fs.readFileSync(file, 'utf8'), {
    Page: definition => { page = definition },
    require(modulePath) {
      if (modulePath.endsWith('/api.js')) return api
      if (modulePath.endsWith('/image-upload.js')) return { showImageError() { throw new Error('Unexpected image selection failure') } }
      if (modulePath.endsWith('/rank.js')) return require(path.join(root, 'miniprogram/utils/rank.js'))
      throw new Error('Unexpected module: ' + modulePath)
    },
    wx: { getStorageSync: storage, showToast() {}, stopPullDownRefresh() {} }
  }, { filename: file })
  page.data = JSON.parse(JSON.stringify(page.data))
  page.setData = values => Object.assign(page.data, values)
  return page
}

function teamFixture() {
  return {
    id: 4,
    name: '真实成员评分测试队',
    captain_id: 20,
    status: 'approved',
    // The detail API intentionally omits the team's total rating and captain name.
    members: [20, 80, 30, 60, 40, 50].map((rating, index) => ({
      user_id: 20 + index,
      nickname: index === 0 ? '队长名字' : '队员' + index,
      rating,
      rank: index === 0 ? null : 'A',
      role: index === 0 ? 'captain' : 'member',
      avatar: index === 0 ? 'https://example.invalid/avatar.png' : null
    }))
  }
}

test('my team sums the top five actual scores and derives captain-only state from the current user', async () => {
  let currentUserId = 20
  let applicationReads = 0
  const page = loadPage('team', {
    BASE: 'https://example.invalid',
    getMyTeam: async () => teamFixture(),
    getMe: async () => ({ id: currentUserId }),
    getApplications: async () => { applicationReads++; return [] }
  })
  await page.refreshAll()
  assert.equal(page.data.myTeam.display_rating, 260)
  assert.equal(page.data.myTeam.rating_tier, 'B')
  assert.equal(page.data.members.length, 6)
  assert.equal(page.data.rankedMemberCount, 5)
  assert.equal(page.data.members[0].avatar_full, 'https://example.invalid/avatar.png')
  assert.equal(page.data.isCaptain, true)
  assert.equal(page.data.loading, false)

  currentUserId = 21
  await page.refreshAll()
  assert.equal(page.data.isCaptain, false)
  assert.equal(applicationReads, 1)
})

test('logged-out team refresh clears stale roster and captain state without requesting personal data', async () => {
  let requests = 0
  const page = loadPage('team', {
    getMyTeam: async () => { requests++; throw new Error('No anonymous personal request expected') },
    getMyInvitations: async () => { requests++; return [] },
    getMyRankApplications: async () => { requests++; return [] }
  }, () => '')
  Object.assign(page.data, { myTeam: teamFixture(), members: [{}], isCaptain: true, applications: [{}] })
  await page.onPullDownRefresh()
  assert.equal(requests, 0)
  assert.equal(page.data.loggedIn, false)
  assert.equal(page.data.myTeam, null)
  assert.equal(page.data.members.length, 0)
  assert.equal(page.data.applications.length, 0)
  assert.equal(page.data.isCaptain, false)
  assert.equal(page.data.loading, false)
})

test('failed my-team reads show the error state and discard stale actionable roster', async () => {
  const page = loadPage('team', {
    getMyTeam: async () => { throw new Error('offline') },
    getTeams: async () => []
  })
  Object.assign(page.data, { myTeam: teamFixture(), members: [{}], isCaptain: true })
  await page.refreshAll()
  assert.equal(page.data.loadFailed, true)
  assert.equal(page.data.loading, false)
  assert.equal(page.data.myTeam, null)
  assert.equal(page.data.members.length, 0)
  assert.equal(page.data.isCaptain, false)
})

test('directory detail derives its real rating and captain name when those summary fields are absent', async () => {
  const page = loadPage('teams', {
    BASE: 'https://example.invalid',
    getTeam: async () => teamFixture()
  })
  await page.viewTeam({ currentTarget: { dataset: { id: 4 } } })
  assert.equal(page.data.showTeamDetail, true)
  assert.equal(page.data.detailTeam.rating, 260)
  assert.equal(page.data.detailTeam.tierLabel, 'B')
  assert.equal(page.data.detailTeam.captainLabel, '队长名字')
  assert.equal(page.data.detailMembers.length, 6)
})

test('directory request failure leaves a distinct error state instead of showing stale search results', async () => {
  const page = loadPage('teams', { getTeams: async () => { throw new Error('offline') } })
  Object.assign(page.data, { teams: [{ id: 4 }], filtered: [{ id: 4 }], total: 1 })
  await page.loadTeams()
  assert.equal(page.data.loadFailed, true)
  assert.equal(page.data.loading, false)
  assert.equal(page.data.teams.length, 0)
  assert.equal(page.data.filtered.length, 0)
})
