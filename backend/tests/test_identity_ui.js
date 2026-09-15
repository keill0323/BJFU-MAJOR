// Run: node --test backend/tests/test_identity_ui.js
// The real page scripts run with isolated wx/API mocks and cannot contact a server.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const test = require('node:test')

const root = path.resolve(__dirname, '../..')

function loadPage(name, methods, wxMethods = {}) {
  let page
  const calls = { toasts: [], navigations: [], storage: [] }
  const api = new Proxy(methods, {
    get(target, key) {
      if (key in target) return target[key]
      throw new Error('Unexpected API access: ' + String(key))
    }
  })
  const wx = Object.assign({
    getStorageSync: () => 'fixture-token',
    showToast: value => calls.toasts.push(value),
    showModal: () => {},
    navigateTo: value => calls.navigations.push(value),
    reLaunch: value => calls.navigations.push(value),
    setStorageSync: (key, value) => calls.storage.push([key, value])
  }, wxMethods)
  const file = path.join(root, 'miniprogram/pages', name, name + '.js')
  vm.runInNewContext(fs.readFileSync(file, 'utf8'), {
    Page: definition => { page = definition },
    require(modulePath) {
      if (modulePath.endsWith('/api.js')) return api
      if (modulePath.endsWith('/image-upload.js')) return { showImageError() { throw new Error('Unexpected image selection failure') } }
      if (modulePath.endsWith('/rank.js')) return require(path.join(root, 'miniprogram/utils/rank.js'))
      throw new Error('Unexpected module: ' + modulePath)
    },
    wx,
    console
  }, { filename: file })
  page.data = JSON.parse(JSON.stringify(page.data))
  page.setData = values => Object.assign(page.data, values)
  return { page, calls }
}

test('verification derives the real rank, score and completion state, including zero S stars', async () => {
  let user
  const { page } = loadPage('verify', { getMe: async () => Object.assign({}, user) })
  const cases = [
    { rank: 'S0', verified: true, rating: 34, stars: 0, progress: 0, tier: 'S', count: 2, display: 'S0星' },
    { rank: 'S32', verified: false, rating: 70, stars: 32, progress: 64, tier: 'S', count: 1, display: '钻S32星' },
    { rank: 'A++', verified: true, rating: 31, stars: null, progress: 0, tier: 'A', count: 2, display: 'A++' },
    { rank: null, verified: false, rating: 0, stars: null, progress: 0, tier: '', count: 0, display: '' }
  ]
  for (const expected of cases) {
    user = { rank: expected.rank, is_verified: expected.verified, individual_rating: expected.rating }
    await page.loadUser()
    assert.equal(page.data.rankStars, expected.stars)
    assert.equal(page.data.rankProgress, expected.progress)
    assert.equal(page.data.rankTier, expected.tier)
    assert.equal(page.data.verifiedCount, expected.count)
    assert.equal(page.data.user.display_rank, expected.display)
    assert.equal(page.data.user.individual_rating, expected.rating)
  }
})

test('verification renders both absolute and server-relative screenshot URLs', async () => {
  let user = { verify_image: 'https://assets.example.test/school.png', rank_image: '/uploads/rank.png' }
  const { page } = loadPage('verify', {
    BASE: 'https://api.example.test',
    getMe: async () => Object.assign({}, user)
  })
  await page.loadUser()
  assert.equal(page.data.user.verify_image_full, 'https://assets.example.test/school.png')
  assert.equal(page.data.user.rank_image_full, 'https://api.example.test/uploads/rank.png')
  user = { verify_image: '/uploads/school.png', rank_image: 'https://assets.example.test/rank.png' }
  await page.loadUser()
  assert.equal(page.data.user.verify_image_full, 'https://api.example.test/uploads/school.png')
  assert.equal(page.data.user.rank_image_full, 'https://assets.example.test/rank.png')
})

test('updating the avatar retains the displayed rank and an absolute avatar URL', async () => {
  const uploads = []
  const { page } = loadPage('profile', {
    uploadAvatar: async filePath => {
      uploads.push(filePath)
      return { rank: 'S25', avatar: 'https://assets.example.test/avatar.png' }
    }
  })
  await page.uploadAvatar('local-avatar.png')
  assert.deepEqual(uploads, ['local-avatar.png'])
  assert.equal(page.data.user.display_rank, '钻S25星')
  assert.equal(page.data.user.avatar_full, 'https://assets.example.test/avatar.png')
  assert.equal(page.data.avatarUploading, false)
})

test('login requires consent and submits only once while the first request is pending', async () => {
  let loginCount = 0
  let wxLoginCount = 0
  let completeLogin
  const { page, calls } = loadPage('login', {
    login: async code => {
      assert.equal(code, 'fixture-code')
      loginCount++
      return new Promise(resolve => { completeLogin = () => resolve({ access_token: 'fixture-access-token' }) })
    }
  }, { login: async () => { wxLoginCount++; return { code: 'fixture-code' } } })

  await page.handleLogin()
  assert.equal(loginCount, 0)
  assert.equal(wxLoginCount, 0)
  assert.match(calls.toasts[0].title, /请先阅读并同意/)

  page.data.agreed = true
  const firstRequest = page.handleLogin()
  await Promise.resolve()
  assert.equal(page.data.loggingIn, true)
  await page.handleLogin()
  assert.equal(loginCount, 1)
  assert.equal(wxLoginCount, 1)
  completeLogin()
  await firstRequest
  assert.equal(page.data.loggingIn, false)
  assert.deepEqual(calls.storage, [['token', 'fixture-access-token']])
  assert.equal(calls.navigations[0].url, '/pages/index/index')
})
