// Real verification page and upload adapter, synthetic responses only.
// These tests exercise state transitions; they do not invoke WeChat or a model.
const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const { parseWxml, createRenderer } = require('../../tools/preview-miniprogram.js')
const root = path.resolve(__dirname, '../..')
const flush = () => new Promise(resolve => setImmediate(resolve))
function deferred() { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b }); return { promise, resolve, reject } }

function status(overrides = {}) {
  return { status: 'ok', data: { student: { status: 'verified' }, rank: Object.assign({
    status: 'verified', label: '当前段位已生效', current_rank: 'A+', pending_rank: null,
    application_status: 'approved', can_upload: true, reason: '当前有效段位以账号中的已生效值为准。'
  }, overrides) } }
}
function pending(rank = 'S10') {
  return status({ status: 'pending_review', label: '段位申请待管理员审核',
    pending_rank: rank, application_status: 'pending', can_upload: false,
    reason: '新的识别候选等待管理员审核，尚未生效。' })
}

function fixture(overrides = {}) {
  const calls = { selections: [], uploads: [], modals: [], toasts: [], status: 0, me: 0, nativeUploads: [], progress: [] }
  const storage = { token: 'synthetic-account-one' }
  const api = Object.assign({ BASE: 'https://example.invalid',
    getMe: async () => { calls.me++; return { id: storage.token === 'synthetic-account-one' ? 1 : 2,
      rank: 'A+', rank_image: '/uploads/synthetic-old.png', is_verified: true } },
    getAssistantVerificationStatus: async () => { calls.status++; return status() },
    uploadRank: async (file, onProgress) => { calls.uploads.push(['rank', file, onProgress]); return { need_review: true, rank: 'S10' } },
    uploadVerify: async (file, onProgress) => { calls.uploads.push(['student', file, onProgress]); return {} }
  }, overrides)
  const wx = { getStorageSync: name => storage[name], removeStorageSync: name => { delete storage[name] },
    chooseMedia: options => calls.selections.push(options),
    compressImage: options => options.success({ tempFilePath: '/synthetic-compressed.png' }),
    showModal: options => calls.modals.push(options), showToast: options => calls.toasts.push(options),
    nextTick: fn => fn(),
    uploadFile: options => { calls.nativeUploads.push(options); return { onProgressUpdate: callback => calls.progress.push(callback) } }
  }
  const imageModule = { exports: {} }
  vm.runInNewContext(fs.readFileSync(path.join(root, 'miniprogram/utils/image-upload.js'), 'utf8'), { module: imageModule, wx })
  let definition
  vm.runInNewContext(fs.readFileSync(path.join(root, 'miniprogram/pages/verify/verify.js'), 'utf8'), {
    Page: value => { definition = value }, wx, console,
    require: name => name.endsWith('/api.js') ? api : name.endsWith('/image-upload.js') ? imageModule.exports
      : require(path.join(root, 'miniprogram/utils/rank.js'))
  })
  const page = Object.assign({}, definition)
  page.data = JSON.parse(JSON.stringify(definition.data))
  page.setData = values => Object.assign(page.data, values)
  page.onLoad()
  function realUploads() {
    const apiModule = { exports: {} }
    vm.runInNewContext(fs.readFileSync(path.join(root, 'miniprogram/utils/api.js'), 'utf8'), {
      module: apiModule, wx, console,
      require: name => { assert.equal(name, './chat-client.js'); return { resetAccount() {} } }
    })
    api.uploadRank = apiModule.exports.uploadRank
    api.uploadVerify = apiModule.exports.uploadVerify
    return apiModule.exports
  }
  function render() {
    const tree = parseWxml(fs.readFileSync(path.join(root, 'miniprogram/pages/verify/verify.wxml'), 'utf8'))
    tree.scope = page.data
    return createRenderer({})(tree)
  }
  return { page, api, wx, storage, calls, realUploads, render }
}

test('pending review keeps the effective rank separate and prevents another picker', async () => {
  let current = 'A+', latest = pending()
  const f = fixture({
    getMe: async () => ({ id: 1, rank: current, rank_image: '/uploads/synthetic-rank.png', is_verified: true }),
    getAssistantVerificationStatus: async () => latest
  })
  await f.page.onShow()
  assert.equal(f.page.data.user.rank, 'A+')
  assert.equal(f.page.data.rankReview.pending, true)
  assert.equal(f.page.data.rankReview.currentRank, 'A+')
  assert.match(f.page.data.rankReview.pendingRank, /S.*10/)
  assert.equal(f.page.data.rankCanSubmit, false)
  f.page.chooseRankImage()
  assert.equal(f.calls.selections.length, 0)
  assert.equal(f.calls.uploads.length, 0)
  const html = f.render()
  assert.match(html, /A\+/)
  assert.match(html, /S.*10/)
  assert.match(html, /待.*审|审核中/)
  current = 'S10'
  latest = status({ current_rank: current })
  await f.page.refreshVerificationStatus()
  assert.equal(f.page.data.user.rank, 'S10')
  assert.equal(f.page.data.rankReview.pending, false)
  assert.equal(f.page.data.operationFeedback.state, 'success')
  assert.equal(f.page.data.rankCanSubmit, true)
})

test('a rejected update leaves the effective rank intact and allows explicit reselection', async () => {
  let latest = pending()
  const f = fixture({ getAssistantVerificationStatus: async () => latest })
  await f.page.onShow()
  assert.equal(f.page.data.rankReview.pending, true)
  latest = status({
    status: 'update_rejected', application_status: 'rejected', label: '最近申请未通过，当前段位仍有效',
    reason: '历史段位文字被遮挡，请更换完整截图。', can_upload: true
  })
  await f.page.refreshVerificationStatus()
  assert.equal(f.page.data.user.rank, 'A+')
  assert.equal(f.page.data.rankReview.pending, false)
  assert.match(f.page.data.rankReview.reason, /遮挡/)
  assert.equal(f.page.data.rankCanSubmit, true)
  assert.equal(f.page.data.operationFeedback.state, 'error')
  f.page.chooseRankImage()
  assert.equal(f.calls.selections.length, 1)
  assert.equal(f.calls.uploads.length, 0)
})

test('a blocked unconfirmed rank explains the reason and offers refresh without claiming approval or review', async () => {
  for (const oldRank of [null, 'S']) {
    let current = oldRank
    let latest = status({ status: 'check_required', application_status: null, current_rank: null,
      pending_rank: null, label: '段位结果待核查', can_upload: false,
      reason: '现有段位资料无法确认，请刷新状态或联系管理员核查。' })
    const f = fixture({
      getMe: async () => ({ id: 1, rank: current, rank_image: '/uploads/synthetic-unconfirmed.png', is_verified: true }),
      getAssistantVerificationStatus: async () => latest
    })
    await f.page.onShow()
    assert.equal(f.page.data.rankReview.status, 'unknown')
    assert.equal(f.page.data.rankReview.pending, false)
    assert.equal(f.page.data.rankCanSubmit, false)
    f.page.chooseRankImage()
    assert.equal(f.calls.selections.length, 0)
    const html = f.render()
    assert.match(html, /现有段位资料无法确认/)
    assert.match(html, /重新核查状态/)
    assert.match(html, /先核查段位状态/)
    assert.match(html, /1 \/ 2 项完成/)
    const rankSection = html.slice(html.indexOf('id="verify-rank"'))
    assert.doesNotMatch(rankSection, /新申请待审核|已提交，等待审核|审核通过后生效|更新未通过|已认证段位截图/)
    assert.doesNotMatch(html, /认证已完成，可按需更新段位/)
    current = 'A+'
    latest = status()
    await f.page.refreshVerificationStatus()
    assert.equal(f.page.data.rankReview.status, 'verified')
    assert.equal(f.page.data.rankCanSubmit, true)
    assert.equal(f.page.data.user.rank, 'A+')
    f.page.chooseRankImage()
    assert.equal(f.calls.selections.length, 1, 'a successful live recheck restores the normal upload entry')
  }
})

test('real upload progress advances to processing without claiming approval at 100 percent', async () => {
  let latest = status()
  const f = fixture({ getAssistantVerificationStatus: async () => latest })
  f.realUploads()
  await f.page.onShow()
  const work = f.page.uploadRankWithPath('/synthetic-rank.png')
  assert.equal(f.calls.nativeUploads.length, 1)
  assert.equal(f.calls.nativeUploads[0].name, 'file')
  assert.equal(f.calls.nativeUploads[0].timeout, 240000)
  assert.equal(f.page.data.operationFeedback.state, 'uploading')
  assert.equal(f.calls.progress.length, 1)
  f.calls.progress[0]({ progress: 37, totalBytesSent: 37, totalBytesExpectedToSend: 100 })
  assert.equal(f.page.data.operationFeedback.progress, 37)
  assert.equal(f.page.data.operationFeedback.state, 'uploading')
  f.calls.progress[0]({ progress: 100, totalBytesSent: 100, totalBytesExpectedToSend: 100 })
  assert.equal(f.page.data.operationFeedback.state, 'processing')
  assert.equal(f.page.data.user.rank, 'A+')
  latest = pending()
  f.calls.nativeUploads[0].success({ statusCode: 200, data: JSON.stringify({ need_review: true, auto_applied: false, rank: 'S10', reason: '等待管理员审核' }) })
  await work
  assert.equal(f.page.data.operationFeedback.state, 'pending')
  assert.equal(f.page.data.rankReview.pending, true)
  assert.equal(f.page.data.rankCanSubmit, false)
  assert.equal(f.page.data.user.rank, 'A+')
  f.calls.progress[0]({ progress: 60 })
  assert.equal(f.page.data.operationFeedback.state, 'pending', 'late transport progress cannot revert the business result')
})

test('an automatically applied result refreshes the effective profile and records success', async () => {
  let current = 'A+'
  const f = fixture({
    getMe: async () => ({ id: 1, rank: current, rank_image: '/uploads/synthetic-old.png', is_verified: true }),
    getAssistantVerificationStatus: async () => status({ current_rank: current }),
    uploadRank: async () => { current = 'S4'; return { auto_applied: true, need_review: false, rank: 'S4' } }
  })
  await f.page.onShow()
  await f.page.uploadRankWithPath('/synthetic-applied.png')
  assert.equal(f.page.data.user.rank, 'S4')
  assert.equal(f.page.data.operationFeedback.state, 'success')
  assert.equal(f.page.data.rankReview.pending, false)
})

test('student upload progress reports submission without converting pending identity into verification', async () => {
  for (const rankPending of [false, true]) {
    let sent = false, outcome = 'pending'
    const f = fixture({
      getMe: async () => ({ id: 1, rank: 'A+', is_verified: sent && outcome === 'verified',
        verify_image: sent ? '/uploads/synthetic-student.png' : null,
        verify_reject_reason: sent && outcome === 'rejected' ? '认证材料不清楚，请补交。' : null,
        ai_review_status: sent ? outcome === 'verified' ? 'auto_pass' : 'pending' : null }),
      getAssistantVerificationStatus: async () => {
        const value = rankPending ? pending() : status()
        value.data.student = { status: sent ? 'pending_review' : 'not_submitted',
          label: sent ? '材料已提交，初审或人工复核中' : '尚未提交认证', can_upload: true }
        return value
      }
    })
    f.realUploads()
    await f.page.onShow()
    const work = f.page.uploadWithPath('/synthetic-student.png')
    assert.equal(f.page.data.operationFeedback.kind, 'student')
    assert.equal(f.page.data.operationFeedback.state, 'uploading')
    assert.equal(f.calls.nativeUploads[0].timeout, 60000)
    f.calls.progress[0]({ progress: 100 })
    assert.equal(f.page.data.operationFeedback.state, 'processing')
    sent = true
    f.calls.nativeUploads[0].success({ statusCode: 200, data: '{}' })
    await work
    assert.equal(f.page.data.user.is_verified, false)
    assert.equal(f.page.data.operationFeedback.state, 'pending')
    assert.equal(f.page.data.operationFeedback.kind, 'student', 'an unrelated rank application cannot overwrite the submitted identity material feedback')
    outcome = rankPending ? 'rejected' : 'verified'
    await f.page.refreshVerificationStatus()
    assert.equal(f.page.data.operationFeedback.kind, 'student')
    assert.equal(f.page.data.operationFeedback.state, outcome === 'verified' ? 'success' : 'error')
    assert.equal(f.page.data.user.is_verified, outcome === 'verified')
    if (outcome === 'rejected') assert.match(f.page.data.operationFeedback.message, /不清楚/)
  }
})

test('a known HTTP validation failure leaves a visible retryable error and no unresolved request', async () => {
  const f = fixture()
  f.realUploads()
  await f.page.onShow()
  const work = f.page.uploadRankWithPath('/synthetic-invalid.png')
  f.calls.nativeUploads[0].success({ statusCode: 400, data: JSON.stringify({ detail: '图片格式不合法' }) })
  await work
  assert.equal(f.page.data.operationFeedback.state, 'error')
  assert.match(f.page.data.operationFeedback.message, /格式不合法/)
  assert.equal(f.page._uncertainUpload, null)
  f.page.chooseRankImage()
  assert.equal(f.calls.selections.length, 1)
})

test('an unknown upload outcome remains distinguishable from rejection and never reuploads itself', async () => {
  for (const error of [{ statusCode: 500, detail: '服务暂不可用' }, { detail: '网络错误：timeout' }]) {
    let sends = 0, reads = 0
    const f = fixture({ uploadRank: async () => { sends++; throw error },
      getAssistantVerificationStatus: async () => { reads++; return status() } })
    await f.page.onShow()
    await f.page.uploadRankWithPath('/synthetic-unknown.png')
    assert.equal(f.page.data.operationFeedback.state, 'unknown')
    assert.ok(f.page._uncertainUpload)
    assert.equal(sends, 1)
    assert.ok(reads >= 2, 'unknown outcome must query state')
    await f.page.refreshVerificationStatus()
    assert.equal(sends, 1)
    assert.equal(f.page.data.operationFeedback.state, 'unknown')
    f.page.chooseRankImage()
    assert.equal(f.calls.selections.length, 0, 'a second image needs an explicit informed retry')
  }
})

test('a timeout resolved to a real pending application becomes pending without a second upload', async () => {
  let latest = status(), sends = 0
  const f = fixture({ getAssistantVerificationStatus: async () => latest,
    uploadRank: async () => { sends++; latest = pending(); throw { detail: '网络错误：timeout' } } })
  await f.page.onShow()
  await f.page.uploadRankWithPath('/synthetic-timeout.png')
  assert.equal(sends, 1)
  assert.equal(f.page._uncertainUpload, null)
  assert.equal(f.page.data.operationFeedback.state, 'pending')
  assert.equal(f.page.data.rankCanSubmit, false)
  assert.equal(f.page.data.user.rank, 'A+')
})

test('an unavailable status endpoint is visible without pretending no application exists', async () => {
  const f = fixture({ getAssistantVerificationStatus: async () => { throw { statusCode: 503, detail: '状态暂不可用' } } })
  await f.page.onShow()
  assert.ok(f.page.data.rankStatusError)
  assert.equal(f.page.data.user.rank, 'A+')
  assert.notEqual(f.page.data.rankReview.status, 'none')
  assert.equal(f.calls.uploads.length, 0)
  assert.equal(f.calls.modals.length, 0, 'page entry should not interrupt with a status error popup')
})

test('account replacement prevents old progress and upload responses from altering the new feedback', async () => {
  const work = deferred()
  let progress
  const f = fixture({ uploadRank: (file, callback) => { progress = callback; return work.promise } })
  await f.page.onShow()
  const pendingUpload = f.page.uploadRankWithPath('/synthetic-old-account.png')
  f.storage.token = 'synthetic-account-two'
  await f.page.onShow()
  const before = JSON.stringify({ user: f.page.data.user, feedback: f.page.data.operationFeedback, rank: f.page.data.rankReview })
  progress({ progress: 100 })
  work.resolve({ auto_applied: true, rank: 'S50' })
  await pendingUpload
  assert.equal(JSON.stringify({ user: f.page.data.user, feedback: f.page.data.operationFeedback, rank: f.page.data.rankReview }), before)
  assert.equal(f.page.data.user.id, 2)
})

test('real HTTP 401 exposes login for its expired owner but leaves a replacement account untouched', async () => {
  for (const replaceAccount of [false, true]) {
    const f = fixture()
    f.realUploads()
    await f.page.onShow()
    const work = f.page.uploadRankWithPath('/synthetic-expiring-login.png')
    let replacement
    if (replaceAccount) {
      f.storage.token = 'synthetic-account-two'
      await f.page.onShow()
      replacement = JSON.stringify({ user: f.page.data.user, feedback: f.page.data.operationFeedback, rank: f.page.data.rankReview })
    }
    f.calls.nativeUploads[0].success({ statusCode: 401, data: '{}' })
    await work
    if (replaceAccount) {
      assert.equal(f.storage.token, 'synthetic-account-two')
      assert.equal(JSON.stringify({ user: f.page.data.user, feedback: f.page.data.operationFeedback, rank: f.page.data.rankReview }), replacement)
      assert.equal(f.page.data.user.id, 2)
    } else {
      assert.equal(f.storage.token, undefined)
      assert.equal(f.page.data.user, null)
      assert.equal(f.page.data.uploading, false)
      assert.equal(f.page.data.operationFeedback.state, 'error')
      assert.equal(f.page.data.operationFeedback.action, 'login')
      assert.match(f.render(), /登录/)
    }
  }
})

test('an old account status response cannot overwrite the replacement account review state', async () => {
  const stale = deferred()
  let count = 0
  const f = fixture({ getAssistantVerificationStatus: () => ++count === 1 ? stale.promise : Promise.resolve(status({ current_rank: 'A+' })) })
  const first = f.page.onShow()
  await flush()
  f.storage.token = 'synthetic-account-two'
  await f.page.onShow()
  stale.resolve(pending('S50'))
  await first
  assert.equal(f.page.data.user.id, 2)
  assert.equal(f.page.data.rankReview.pending, false)
  assert.doesNotMatch(String(f.page.data.rankReview.pendingRank), /50/)
})

test('a status read started before upload cannot erase the new pending application', async () => {
  const stale = deferred()
  let count = 0, latest = status()
  const f = fixture({
    getAssistantVerificationStatus: () => ++count === 2 ? stale.promise : Promise.resolve(latest),
    uploadRank: async () => { latest = pending(); return { need_review: true, rank: 'S10' } }
  })
  await f.page.onShow()
  const oldRead = f.page.refreshVerificationStatus()
  await flush()
  await f.page.uploadRankWithPath('/synthetic-new-pending.png')
  assert.equal(f.page.data.rankReview.pending, true)
  stale.resolve(status())
  await oldRead
  assert.equal(f.page.data.rankReview.pending, true)
  assert.equal(f.page.data.operationFeedback.state, 'pending')
  assert.equal(f.page.data.rankCanSubmit, false)
  assert.equal(f.page.data.rankStatusLoading, false)
})

test('a hidden page ignores a late pending status and queries fresh state when shown again', async () => {
  const stale = deferred()
  let count = 0
  const f = fixture({ getAssistantVerificationStatus: () => ++count === 2 ? stale.promise : Promise.resolve(status()) })
  await f.page.onShow()
  const oldRead = f.page.refreshVerificationStatus()
  await flush()
  f.page.onHide()
  stale.resolve(pending())
  await oldRead
  assert.equal(f.page.data.rankReview.pending, false)
  await f.page.onShow()
  assert.equal(f.page.data.rankReview.pending, false)
  assert.equal(f.page.data.rankStatusLoading, false)
  assert.equal(count, 3)
})

test('selection cancellation clears the busy selection feedback and permits another tap', async () => {
  const f = fixture()
  await f.page.onShow()
  f.page.chooseRankImage()
  assert.equal(f.page.data.operationFeedback.state, 'selecting')
  f.calls.selections[0].fail({ errMsg: 'chooseMedia:fail cancel' })
  assert.notEqual(f.page.data.operationFeedback && f.page.data.operationFeedback.state, 'selecting')
  assert.equal(f.page.data.uploading, false)
  f.page.chooseRankImage()
  assert.equal(f.calls.selections.length, 2)
})
