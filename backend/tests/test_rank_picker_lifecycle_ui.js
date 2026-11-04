// Real page callbacks with synthetic wx lifecycle events; no native picker/network.
const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const root = path.resolve(__dirname, '../..')
const flush = () => new Promise(resolve => setImmediate(resolve))
const selected = file => ({ tempFiles: [{ tempFilePath: file }] })

function fixture(overrides = {}) {
  const calls = { selections: [], uploads: [], compressions: [], toasts: [], modals: [] }
  const storage = { token: 'synthetic-account-one' }
  const api = Object.assign({
    BASE: 'https://example.invalid',
    getMe: async () => ({ id: storage.token === 'synthetic-account-one' ? 1 : 2,
      rank: 'S3', rank_image: '/uploads/synthetic-old.png', is_verified: true }),
    getAssistantVerificationStatus: async () => { throw { statusCode: 503 } },
    uploadRank: async file => { calls.uploads.push(['rank', file]); return { auto_applied: true, rank: 'S4' } },
    uploadVerify: async file => { calls.uploads.push(['student', file]); return {} }
  }, overrides)
  const wx = {
    getStorageSync: key => storage[key], chooseMedia: options => calls.selections.push(options),
    compressImage: options => calls.compressions.push(options),
    showToast: options => calls.toasts.push(options), showModal: options => calls.modals.push(options),
    nextTick: callback => callback()
  }
  const images = { exports: {} }
  vm.runInNewContext(fs.readFileSync(path.join(root, 'miniprogram/utils/image-upload.js'), 'utf8'),
    { module: images, wx })
  let definition
  vm.runInNewContext(fs.readFileSync(path.join(root, 'miniprogram/pages/verify/verify.js'), 'utf8'), {
    Page: value => { definition = value }, wx, console,
    require: name => name.endsWith('/api.js') ? api : name.endsWith('/image-upload.js') ? images.exports
      : require(path.join(root, 'miniprogram/utils/rank.js'))
  })
  function newPage() {
    const page = Object.assign({}, definition)
    page.data = JSON.parse(JSON.stringify(definition.data))
    page.setData = values => Object.assign(page.data, values)
    page.onLoad()
    return page
  }
  return { page: newPage(), newPage, storage, calls, api }
}

test('rank picker keeps the original image and uploads once when success follows native return', async () => {
  const { page, calls } = fixture()
  await page.onShow(); page.chooseRankImage()
  assert.deepEqual(Array.from(calls.selections[0].sizeType), ['original'])
  page.onHide(); await page.onShow()
  assert.equal(calls.uploads.length, 0)
  calls.selections[0].success(selected('/synthetic-returned.png'))
  calls.selections[0].success(selected('/synthetic-duplicate.png'))
  await flush()
  assert.deepEqual(calls.uploads, [['rank', '/synthetic-returned.png']])
  assert.equal(calls.compressions.length, 0)
})

test('a selected rank image waits while hidden and is consumed once on the same page return', async () => {
  const { page, calls } = fixture()
  await page.onShow(); page.chooseRankImage(); page.onHide()
  calls.selections[0].success(selected('/synthetic-hidden.png'))
  assert.equal(calls.uploads.length, 0, 'native success cannot start a background upload')
  await page.onShow(); await page.onShow()
  calls.selections[0].success(selected('/synthetic-late-duplicate.png'))
  await flush()
  assert.deepEqual(calls.uploads, [['rank', '/synthetic-hidden.png']])
})

test('unloading discards both an outstanding picker and an already selected hidden result', async () => {
  for (const hasResult of [false, true]) {
    const { page, newPage, calls } = fixture()
    await page.onShow(); page.chooseRankImage(); page.onHide()
    if (hasResult) calls.selections[0].success(selected('/synthetic-selected-before-unload.png'))
    page.onUnload()
    const fresh = newPage(); await fresh.onShow()
    calls.selections[0].success(selected('/synthetic-old-page.png'))
    await page.onShow()
    assert.equal(calls.uploads.length, 0)
    fresh.chooseRankImage()
    calls.selections[1].success(selected('/synthetic-fresh-page.png'))
    await flush()
    assert.deepEqual(calls.uploads, [['rank', '/synthetic-fresh-page.png']])
  }
})

test('an account change on return discards the old picker even when the original account returns later', async () => {
  const { page, storage, calls } = fixture()
  await page.onShow(); page.chooseRankImage(); page.onHide()
  storage.token = 'synthetic-account-two'; await page.onShow()
  storage.token = 'synthetic-account-one'; await page.onShow()
  calls.selections[0].success(selected('/synthetic-old-account.png'))
  await flush()
  assert.equal(calls.uploads.length, 0)
  page.chooseRankImage()
  calls.selections[1].success(selected('/synthetic-current-account.png'))
  await flush()
  assert.deepEqual(calls.uploads, [['rank', '/synthetic-current-account.png']])
})

test('a callback observing another token permanently discards its picker before onShow', async () => {
  const { page, storage, calls } = fixture()
  await page.onShow(); page.chooseRankImage(); page.onHide()
  storage.token = 'synthetic-account-two'
  calls.selections[0].success(selected('/synthetic-other-account.png'))
  storage.token = 'synthetic-account-one'; await page.onShow()
  calls.selections[0].success(selected('/synthetic-resurrected.png'))
  await flush()
  assert.equal(calls.uploads.length, 0)
  page.chooseRankImage()
  assert.equal(calls.selections.length, 2)
})

test('hidden native cancellation releases the picker so returning users can choose again', async () => {
  const { page, calls } = fixture()
  await page.onShow(); page.chooseRankImage(); page.onHide()
  calls.selections[0].fail({ errMsg: 'chooseMedia:fail cancel' })
  assert.equal(calls.toasts.length, 0)
  await page.onShow(); page.chooseRankImage()
  assert.equal(calls.selections.length, 2)
  assert.equal(calls.toasts.length, 0, 'user cancellation keeps its original silent treatment')
  calls.selections[1].success(selected('/synthetic-after-cancel.png'))
  await flush()
  assert.deepEqual(calls.uploads, [['rank', '/synthetic-after-cancel.png']])
})

test('hidden native permission failure is reported once after return and permits a new selection', async () => {
  const { page, calls } = fixture()
  await page.onShow(); page.chooseRankImage(); page.onHide()
  calls.selections[0].fail({ errMsg: 'chooseMedia:fail auth deny' })
  assert.equal(calls.modals.length, 0)
  await page.onShow(); await page.onShow()
  assert.equal(calls.modals.length, 1)
  assert.equal(calls.modals[0].title, '无法使用相册或相机')
  page.chooseRankImage()
  assert.equal(calls.selections.length, 2)
})

test('late callbacks from a cancelled selection cannot replace a newer selected image', async () => {
  const { page, calls } = fixture()
  await page.onShow(); page.chooseRankImage()
  calls.selections[0].fail({ errMsg: 'chooseMedia:fail cancel' })
  page.chooseRankImage()
  calls.selections[0].success(selected('/synthetic-superseded.png'))
  assert.equal(calls.uploads.length, 0)
  calls.selections[1].success(selected('/synthetic-new-selection.png'))
  await flush()
  assert.deepEqual(calls.uploads, [['rank', '/synthetic-new-selection.png']])
})

test('a missing native file result uses the existing failure handler and allows another tap', async () => {
  for (const hidden of [false, true]) {
    const { page, calls } = fixture()
    await page.onShow(); page.chooseRankImage()
    if (hidden) page.onHide()
    calls.selections[0].success({ tempFiles: [] })
    assert.equal(calls.uploads.length, 0)
    if (hidden) {
      assert.equal(calls.toasts.length, 0)
      await page.onShow()
    }
    assert.equal(calls.toasts[0].title, '选图失败，请稍后重试')
    page.chooseRankImage()
    assert.equal(calls.selections.length, 2)
  }
})

test('once upload has begun, hide/show retains uncertain-result handling and never reuploads', async () => {
  let resolveUpload
  const work = new Promise(resolve => { resolveUpload = resolve })
  const { page, calls } = fixture({ uploadRank: file => { calls.uploads.push(['rank', file]); return work } })
  await page.onShow(); page.chooseRankImage()
  calls.selections[0].success(selected('/synthetic-already-uploading.png'))
  assert.equal(calls.uploads.length, 1)
  page.onHide(); await page.onShow()
  page.chooseRankImage()
  assert.equal(calls.selections.length, 1)
  assert.equal(calls.modals[0].title, '先核查上次上传结果')
  resolveUpload({ auto_applied: true, rank: 'S4' }); await flush()
  assert.equal(calls.uploads.length, 1)
  assert.equal(calls.toasts.length, 0, 'stale upload callbacks do not regain foreground ownership')
})

test('student picker callbacks retain their original page-generation boundary', async () => {
  const { page, calls } = fixture()
  await page.onShow(); page.chooseVerifyImage(); page.onHide(); await page.onShow()
  calls.selections[0].success(selected('/synthetic-student.png'))
  await flush()
  assert.equal(calls.compressions.length, 0)
  assert.equal(calls.uploads.length, 0)
})
