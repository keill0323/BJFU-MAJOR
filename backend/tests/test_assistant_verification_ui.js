// Real page scripts, synthetic current-user status, no uploads or remote models.
const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const { parseWxml, createRenderer } = require('../../tools/preview-miniprogram.js')
const root = path.resolve(__dirname, '../..')
const event = dataset => ({ currentTarget: { dataset } })
const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b }); return { promise, resolve, reject } }

function verification({ student = '材料已提交，初审或人工复核中', rank = '当前A+，新申请待审核', pending = 'S10' } = {}) {
  return { status: 'ok', data: { student: { status: 'pending_review' }, rank: { current_rank: 'A+', pending_rank: pending, application_status: 'pending' } },
    cards: [{ type: 'verification_status', title: '我的认证', summary: '上传不等于通过', items: [
      { title: '在校认证', subtitle: student, detail: '需按原规则核查', badge: student, action: { kind: 'open_verify', scope: 'student' } },
      { title: '游戏段位认证', subtitle: rank, detail: rank + '；待审识别段位' + pending + '尚未生效', badge: '待审核', action: { kind: 'open_verify', scope: 'rank' } }
    ] }] }
}

function verificationPageStatus(hasPending = false) {
  const value = verification({ student: '尚未提交在校认证',
    rank: hasPending ? '段位申请待管理员审核' : '尚未提交段位认证', pending: hasPending ? 'S10' : '' })
  value.data.student = { status: 'not_submitted', label: '尚未提交在校认证', can_upload: true }
  value.data.rank = { status: hasPending ? 'pending_review' : 'not_submitted',
    label: hasPending ? '段位申请待管理员审核' : '尚未提交段位认证',
    current_rank: null, pending_rank: hasPending ? 'S10' : null,
    application_status: hasPending ? 'pending' : null, can_upload: !hasPending,
    reason: hasPending ? '申请已提交，待管理员审核；识别候选段位尚未生效。' : '请在认证中心主动提交截图。' }
  return value
}

function focusedVerification(scope, title) {
  const value = verification()
  value.data = Object.assign({ scope }, scope === 'both' ? value.data : { [scope]: value.data[scope] })
  value.cards[0].title = '认证办理'
  value.cards[0].items = value.cards[0].items.filter(item => scope === 'both' || item.action.scope === scope)
  if (title && value.cards[0].items.length === 1) value.cards[0].items[0].title = title
  return value
}

function fixture(pageName = 'assistant', overrides = {}, token = 'account-one') {
  const calls = { navigations: [], modals: [], toasts: [], selections: [], compressions: [], uploads: [], scrolls: [], me: 0, verification: 0 }
  const storage = { token }
  const pendingRankAccounts = new Set()
  const api = Object.assign({ BASE: 'https://api.example.test', getAssistantStatus: async () => ({ enabled: true, available: true }),
    getAssistantVerificationStatus: async () => { calls.verification++; return pageName === 'verify' ? verificationPageStatus(pendingRankAccounts.has(storage.token)) : verification() },
    createAssistantConversation: async () => ({ conversation_id: 'synthetic-conversation' }),
    getAssistantConversation: async () => ({ messages: [] }),
    sendAssistantMessage: async () => ({ reply: '请以当前本人状态为准', cards: verification().cards }),
    getMe: async () => { calls.me++; return { id: storage.token === 'account-one' ? 1 : 2, rank: null, is_verified: false } },
    uploadVerify: async file => { calls.uploads.push(['student', file]); return {} },
    uploadRank: async file => { calls.uploads.push(['rank', file]); pendingRankAccounts.add(storage.token); return { need_review: true, rank: 'S10' } }
  }, overrides)
  const wx = { getStorageSync: key => storage[key], getWindowInfo: () => ({ windowHeight: 700 }),
    navigateTo: options => calls.navigations.push(options.url), reLaunch: options => calls.navigations.push(options.url), removeStorageSync: key => { delete storage[key] },
    showModal: options => calls.modals.push(options), showToast: options => calls.toasts.push(options),
    chooseMedia: options => calls.selections.push(options), compressImage: options => calls.compressions.push(options),
    pageScrollTo: options => calls.scrolls.push(options), nextTick: fn => fn() }
  const file = path.join(root, 'miniprogram/pages', pageName, pageName + '.js')
  let definition
  vm.runInNewContext(fs.readFileSync(file, 'utf8'), { Page: value => { definition = value }, wx, console,
    require: name => {
      if (name.endsWith('/api.js')) return api
      if (name.endsWith('/wechat-subscriptions.js')) return { prefetch: async () => {}, prompt: async () => {} }
      if (name.endsWith('/rank.js')) return require(path.join(root, 'miniprogram/utils/rank.js'))
      if (name.endsWith('/image-upload.js')) return { showImageError: error => calls.toasts.push(error) }
      throw new Error('Unexpected module ' + name)
    }
  }, { filename: file })
  function newPage() {
    const page = Object.assign({}, definition)
    page.data = JSON.parse(JSON.stringify(definition.data))
    page.setData = (values, callback) => { Object.assign(page.data, values); if (callback) callback() }
    return page
  }
  return { page: newPage(), newPage, api, wx, storage, calls }
}

async function openAssistant(overrides, token) {
  const f = fixture('assistant', overrides, token)
  f.page.onLoad(); await f.page.onShow()
  return f
}

function realUploadApi(f) {
  const module = { exports: {} }
  const file = path.join(root, 'miniprogram/utils/api.js')
  vm.runInNewContext(fs.readFileSync(file, 'utf8'), { module, wx: f.wx, console,
    require: name => { assert.equal(name, './chat-client.js'); return { resetAccount() {} } }
  }, { filename: file })
  return module.exports
}

test('an empty assistant only shows suggestions and composer until the user asks for verification', async () => {
  const { page, calls } = await openAssistant()
  assert.equal(calls.verification, 0)
  assert.equal(page.data.verificationCard, null)
  page.data.draft = '帮我完成认证'; await page.send()
  assert.equal(calls.verification, 1)
  assert.equal(calls.selections.length, 0)
  assert.equal(calls.uploads.length, 0)
  assert.match(page.data.verificationCard.items[1].detail, /当前A\+.*待审识别段位S10尚未生效/)
  assert.equal(page.data.verificationCard.items[1].action.scope, 'rank')
})

test('fixed verification card navigation uses original categories without opening photo APIs', async () => {
  const { page, calls } = await openAssistant()
  page.openVerification(event({ scope: 'student', url: 'https://evil.test' }))
  page.openVerification(event({ scope: 'rank' }))
  page.openVerification(event({ scope: '../../evil' }))
  assert.deepEqual(calls.navigations, ['/pages/verify/verify?focus=student&source=assistant', '/pages/verify/verify?focus=rank&source=assistant'])
  assert.equal(calls.selections.length, 0)
  assert.equal(calls.uploads.length, 0)
})

test('guest verification action asks for login without requesting authenticated state', async () => {
  const { page, calls } = await openAssistant({}, '')
  assert.equal(calls.verification, 0)
  page.openVerification(event({ scope: 'student' }))
  assert.deepEqual(calls.navigations, ['/pages/login/login'])
  assert.equal(calls.selections.length, 0)
})

test('returning from verification refreshes state even when the conversation already exists', async () => {
  let value = verification()
  const { page, api } = await openAssistant({ getAssistantVerificationStatus: async () => value,
    getAssistantConversation: async () => ({ messages: [{ role: 'assistant', content: '历史认证查询', cards: verification().cards }] }) })
  page.data.draft = '查询认证'; await page.send()
  page.onHide()
  value = verification({ student: '已通过在校认证', rank: '当前A+，新申请已驳回', pending: '' })
  await page.onShow()
  assert.equal(page.data.verificationCard.items[0].badge, '已通过在校认证')
  assert.match(page.data.verificationCard.items[1].detail, /新申请已驳回/)
  assert.equal(page.data.verificationLoading, false)
})

test('late status from the old account cannot overwrite the replacement account', async () => {
  const old = deferred()
  let n = 0
  const f = fixture('assistant', { getAssistantVerificationStatus: () => ++n === 1 ? old.promise : Promise.resolve(verification({ student: '新账号' })) })
  f.page.onLoad(); await f.page.onShow()
  f.page.data.messages = [{ id: 'old-status', cards: verification().cards }]
  const first = f.page.loadVerification()
  f.storage.token = 'account-two'; await f.page.onShow()
  old.resolve(verification({ student: '不应显示的旧结果' })); await first
  assert.equal(f.page.data.verificationCard, null)
  assert.equal(f.page.data.messages.length, 0)
  f.page.data.messages = [{ id: 'new-status', cards: verification().cards }]
  await f.page.loadVerification()
  assert.equal(f.page.data.verificationCard.items[0].badge, '新账号')
})

test('hiding the page rejects late verification results and re-entry performs a fresh request', async () => {
  const pending = deferred()
  const f = fixture('assistant', { getAssistantVerificationStatus: () => pending.promise })
  f.page.onLoad(); await f.page.onShow()
  f.page.data.messages = [{ id: 'old-status', cards: verification().cards }]
  const show = f.page.loadVerification(); f.page.onHide()
  pending.resolve(verification({ student: '过期结果' })); await show
  assert.equal(f.page.data.verificationCard, null)
  f.api.getAssistantVerificationStatus = async () => verification({ student: '重新查询' })
  await f.page.onShow()
  assert.equal(f.page.data.verificationCard.items[0].badge, '重新查询')
})

test('failed refresh clears the old apparent status but leaves a working verification entry', async () => {
  const { page, api, calls } = await openAssistant()
  page.data.draft = '查询认证'; await page.send()
  api.getAssistantVerificationStatus = async () => { throw { statusCode: 404, detail: 'Not Found' } }
  await page.loadVerification()
  assert.equal(page.data.verificationCard, null)
  assert.match(page.data.verificationError, /进入认证页/)
  assert.equal(page.data.messages[1].cards[0].items[0].badge, '待核查')
  page.openVerification(event({ scope: 'rank' }))
  assert.equal(calls.navigations.length, 1)
})

test('verification card actions use fixed scopes, reject arbitrary URLs and refresh actual status after the tool reply', async () => {
  const cards = verification().cards
  cards[0].items.push({ title: '无效入口', action: { kind: 'open_verify', scope: 'https://evil.test', url: 'https://evil.test' } })
  const { page, calls } = await openAssistant({ sendAssistantMessage: async () => ({ reply: '查询完成', cards }) })
  page.data.draft = '帮我认证'; await page.send()
  assert.equal(calls.verification, 1)
  const card = page.data.messages[1].cards[0]
  assert.match(card.summary, /上传不等于通过/)
  assert.ok(!card.items[2] || card.items[2].action === null)
  await page.openCardItem(event({ message: page.data.messages[1].id, card: 0, item: 1 }))
  assert.equal(calls.navigations[0], '/pages/verify/verify?focus=rank&source=assistant')
  assert.equal(calls.uploads.length, 0)
})

test('an existing verification card remains usable when the text model is unavailable', async () => {
  const { page, calls } = await openAssistant({ getAssistantStatus: async () => ({ enabled: true, available: false }) })
  page.openVerification(event({ scope: 'student' }))
  assert.deepEqual(calls.navigations, ['/pages/verify/verify?focus=student&source=assistant'])
})

test('no verification request or popup is made without an existing requested result card', async () => {
  let queried = 0
  const { page, calls } = await openAssistant({ getAssistantVerificationStatus: async () => { queried++; throw { statusCode: 404 } } })
  await page.loadVerification()
  assert.equal(queried, 0)
  assert.equal(calls.modals.length, 0)
  assert.equal(page.data.verificationError, '')
})

test('a prepared rank-only card keeps its row identity and scope after the live full-status refresh', async () => {
  const refresh = deferred()
  const focused = focusedVerification('rank', '更新段位材料')
  const f = await openAssistant({ sendAssistantMessage: async () => ({ reply: '请提交新的段位材料', cards: focused.cards }),
    getAssistantVerificationStatus: () => refresh.promise })
  f.page.data.draft = '帮我更新游戏段位'
  const work = f.page.send()
  await new Promise(resolve => setImmediate(resolve))
  const original = f.page.data.messages[1].cards[0]
  assert.equal(original.items.length, 1)
  assert.equal(original.items[0].actionLabel, '更新段位材料')
  const identity = { card: original.key, item: original.items[0].key }
  const fresh = verification()
  fresh.data.rank = { status: 'verified', current_rank: 'A+', application_status: 'approved', can_upload: true }
  refresh.resolve(fresh)
  await work
  const result = f.page.data.messages[1].cards[0]
  assert.equal(result.items.length, 1)
  assert.equal(result.key, identity.card)
  assert.equal(result.items[0].key, identity.item, 'rank initially occupies row zero even though the full response puts rank second')
  assert.equal(result.items[0].action.scope, 'rank')
  assert.equal(result.items[0].actionLabel, '更新段位材料')
  assert.equal(f.page.data.verificationCard.items.length, 1)
  assert.equal(f.page.data.verificationCard.items[0].action.scope, 'rank')
  assert.doesNotMatch(JSON.stringify(result), /在校认证/)
  assert.equal(f.calls.selections.length, 0)
  assert.equal(f.calls.uploads.length, 0)
})

test('each historical verification card refreshes its own scopes and keys instead of becoming the latest card', async () => {
  const f = await openAssistant()
  const rank = focusedVerification('rank').cards[0]
  const student = focusedVerification('student').cards[0]
  const both = focusedVerification('both').cards[0]
  rank.key = 'rank-card'; rank.items[0].key = 'rank-entry'
  student.key = 'student-card'; student.items[0].key = 'student-entry'
  both.key = 'both-card'; both.items[0].key = 'both-student'; both.items[1].key = 'both-rank'
  const ordinary = { key: 'ordinary', type: 'knowledge', title: '保持原内容', items: [] }
  f.page.data.messages = [
    { id: 'rank-message', cards: [rank, ordinary] },
    { id: 'student-message', cards: [student] },
    { id: 'both-message', cards: [both] }
  ]
  await f.page.loadVerification()
  const cards = f.page.data.messages.map(message => message.cards[0])
  assert.deepEqual(cards.map(card => Array.from(card.items, item => item.action.scope)), [['rank'], ['student'], ['student', 'rank']])
  assert.deepEqual(cards.map(card => Array.from(card.items, item => item.key)), [['rank-entry'], ['student-entry'], ['both-student', 'both-rank']])
  assert.deepEqual(cards.map(card => card.key), ['rank-card', 'student-card', 'both-card'])
  assert.equal(f.page.data.messages[0].cards[1], ordinary)
  assert.equal(cards[0].items[0].actionLabel, '查看段位审核进度')
  assert.equal(cards[1].items[0].actionLabel, '查看在校认证进度')
})

test('returning from rank material updates only the rank card and changes its action to review progress', async () => {
  let fresh = verification()
  fresh.data.rank = { status: 'verified', current_rank: 'A+', application_status: 'approved', can_upload: true }
  const focused = focusedVerification('rank', '更新段位材料')
  const f = await openAssistant({ getAssistantVerificationStatus: async () => fresh,
    sendAssistantMessage: async () => ({ reply: '已准备更新入口', cards: focused.cards }),
    getAssistantConversation: async () => ({ messages: [{ role: 'assistant', content: '已准备更新入口', cards: focused.cards }] }) })
  f.page.data.draft = '更新段位'; await f.page.send()
  assert.equal(f.page.data.messages[1].cards[0].items[0].actionLabel, '更新段位材料')
  f.page.onHide()
  fresh = verification()
  await f.page.onShow()
  const card = f.page.data.messages[0].cards[0]
  assert.equal(card.items.length, 1)
  assert.equal(card.items[0].action.scope, 'rank')
  assert.equal(card.items[0].actionLabel, '查看段位审核进度')
  await f.page.openCardItem(event({ message: f.page.data.messages[0].id, card: 0, item: 0 }))
  assert.equal(f.calls.navigations.at(-1), '/pages/verify/verify?focus=rank&source=assistant')
  assert.equal(f.calls.uploads.length, 0)
})

test('a missing focused status never falls back to another scope and failed refresh preserves the entry', async () => {
  const f = await openAssistant()
  const rank = focusedVerification('rank').cards[0]
  rank.key = 'focused'; rank.items[0].key = 'rank-stable'
  f.page.data.messages = [{ id: 'focused-message', cards: [rank] }]
  f.api.getAssistantVerificationStatus = async () => focusedVerification('student')
  await f.page.loadVerification()
  let card = f.page.data.messages[0].cards[0]
  assert.equal(card.items.length, 1)
  assert.equal(card.items[0].key, 'rank-stable')
  assert.equal(card.items[0].action.scope, 'rank')
  assert.equal(card.items[0].badge, '待核查')
  assert.doesNotMatch(card.items[0].detail, /在校认证/)
  f.api.getAssistantVerificationStatus = async () => { throw { statusCode: 503 } }
  await f.page.loadVerification()
  card = f.page.data.messages[0].cards[0]
  assert.equal(card.items.length, 1)
  assert.equal(card.items[0].action.scope, 'rank')
  assert.equal(card.items[0].actionLabel, '查看认证状态')
  assert.equal(f.page.data.verificationCard, null)
  assert.equal(f.calls.selections.length, 0)
})

test('verification entry labels follow current business states without invoking material APIs', async () => {
  const f = await openAssistant()
  f.page.data.messages = [{ id: 'both-statuses', cards: focusedVerification('both').cards }]
  for (const [student, rank, labels] of [
    [{ status: 'verified', can_upload: false }, { status: 'not_submitted', application_status: null, can_upload: true }, ['查看在校认证状态', '提交段位材料']],
    [{ status: 'rejected', can_upload: true }, { status: 'update_rejected', application_status: 'rejected', current_rank: 'A+', can_upload: true }, ['重新提交在校认证材料', '重新提交段位材料']],
    [{ status: 'not_submitted', can_upload: true }, { status: 'check_required', can_upload: false }, ['提交在校认证材料', '查看段位认证状态']]
  ]) {
    const fresh = verification()
    fresh.data = { student, rank }
    fresh.cards[0].items.forEach(item => {
      const current = fresh.data[item.action.scope]
      item.subtitle = item.badge = current.status
      item.detail = '来自本人状态接口'
    })
    f.api.getAssistantVerificationStatus = async () => fresh
    await f.page.loadVerification()
    assert.deepEqual(Array.from(f.page.data.messages[0].cards[0].items, item => item.actionLabel), labels)
  }
  assert.equal(f.calls.selections.length, 0)
  assert.equal(f.calls.uploads.length, 0)
})

test('verification focus is a fixed selector and never auto-selects or uploads', async () => {
  const f = fixture('verify')
  f.page.onLoad({ focus: 'rank', source: 'assistant' }); await f.page.onShow()
  assert.equal(f.calls.scrolls[0].selector, '#verify-rank')
  assert.equal(f.calls.selections.length, 0)
  assert.equal(f.calls.modals.length, 0)
  const bad = fixture('verify'); bad.page.onLoad({ focus: '#arbitrary' }); await bad.page.onShow()
  assert.equal(bad.calls.scrolls.length, 0)
})

test('assistant-origin upload requires a separate material-purpose decision before native selection', async () => {
  const f = fixture('verify'); f.page.onLoad({ focus: 'rank', source: 'assistant' }); await f.page.onShow()
  f.page.chooseRankImage()
  assert.equal(f.calls.modals[0].title, '认证材料用途')
  assert.match(f.calls.modals[0].content, /智谱视觉识别.*不会发送给文字赛事助手.*微信隐私授权/)
  assert.equal(f.calls.selections.length, 0)
  f.calls.modals[0].success({ confirm: true })
  assert.equal(f.calls.selections.length, 1)
  assert.deepEqual(Array.from(f.calls.selections[0].sizeType), ['original'])
  assert.equal(f.calls.uploads.length, 0, 'purpose consent does not create a selected file or upload')
})

test('declining the purpose decision does not select or upload and another explicit tap can retry', async () => {
  const f = fixture('verify'); f.page.onLoad({ focus: 'student', source: 'assistant' }); await f.page.onShow()
  f.page.chooseVerifyImage(); f.calls.modals[0].success({ confirm: false })
  assert.equal(f.calls.selections.length, 0)
  f.page.chooseVerifyImage(); f.calls.modals[1].success({ confirm: true })
  assert.equal(f.calls.selections.length, 1)
  assert.equal(f.calls.uploads.length, 0)
})

test('switching account or leaving during purpose consent cannot launch the old selection', async () => {
  for (const leave of ['account', 'hide']) {
    const f = fixture('verify'); f.page.onLoad({ focus: 'rank', source: 'assistant' }); await f.page.onShow()
    f.page.chooseRankImage()
    if (leave === 'account') f.storage.token = 'account-two'
    else f.page.onHide()
    f.calls.modals[0].success({ confirm: true })
    assert.equal(f.calls.selections.length, 0)
    assert.equal(f.calls.uploads.length, 0)
  }
})

test('selection and compression callbacks cannot upload after a changed account', async () => {
  const rank = fixture('verify'); rank.page.onLoad(); await rank.page.onShow(); rank.page.chooseRankImage()
  rank.storage.token = 'account-two'
  rank.calls.selections[0].success({ tempFiles: [{ tempFilePath: '/private-rank.jpg' }] })
  assert.equal(rank.calls.uploads.length, 0)
  const student = fixture('verify'); student.page.onLoad(); await student.page.onShow(); student.page.chooseVerifyImage()
  student.calls.selections[0].success({ tempFiles: [{ tempFilePath: '/private-student.jpg' }] })
  student.storage.token = 'account-two'
  student.calls.compressions[0].success({ tempFilePath: '/compressed.jpg' })
  assert.equal(student.calls.uploads.length, 0)
})

test('late upload responses cannot announce approval or overwrite a new account', async () => {
  const pending = deferred()
  const f = fixture('verify', { uploadRank: () => pending.promise })
  f.page.onLoad(); await f.page.onShow()
  const work = f.page.uploadRankWithPath('/synthetic.jpg')
  f.storage.token = 'account-two'; await f.page.onShow()
  pending.resolve({ auto_applied: true, rank: 'S20' }); await work
  assert.equal(f.page.data.user.id, 2)
  assert.equal(f.page.data.user.rank, null)
  assert.equal(f.calls.toasts.length, 0)
  assert.equal(f.calls.modals.length, 0)
})

test('material submitted for review remains pending and does not become the current rank', async () => {
  const f = fixture('verify')
  f.page.onLoad(); await f.page.onShow(); await f.page.uploadRankWithPath('/synthetic.jpg')
  assert.equal(f.page.data.user.rank, null)
  assert.equal(f.calls.modals[0].title, '已提交段位审核')
  assert.match(f.calls.modals[0].content, /等待管理员审核/)
  assert.equal(f.calls.toasts.length, 0)
})

test('ambiguous upload failure queries state and blocks a second photo selection while unknown', async () => {
  const f = fixture('verify', { uploadRank: async () => { throw { errMsg: 'uploadFile:fail timeout' } },
    getAssistantVerificationStatus: async () => { throw { statusCode: 503 } } })
  f.page.onLoad(); await f.page.onShow(); await f.page.uploadRankWithPath('/synthetic.jpg')
  assert.equal(f.calls.me, 2)
  assert.match(f.calls.toasts[0].title, /结果尚未确认/)
  f.page.chooseRankImage()
  assert.equal(f.calls.selections.length, 0)
  assert.equal(f.calls.modals[0].title, '先核查上次上传结果')
})

test('pending rank application resolves a timeout without reuploading or claiming approval', async () => {
  let submitted = false
  const f = fixture('verify', {
    uploadRank: async () => { submitted = true; throw { errMsg: 'uploadFile:fail timeout' } },
    getAssistantVerificationStatus: async () => verificationPageStatus(submitted)
  })
  f.page.onLoad(); await f.page.onShow(); await f.page.uploadRankWithPath('/synthetic.jpg')
  assert.equal(f.page._uncertainUpload, null)
  assert.match(f.calls.toasts.at(-1).title, /等待段位审核/)
  assert.equal(f.page.data.user.rank, null)
  assert.equal(f.calls.selections.length, 0)
  assert.equal(f.calls.uploads.length, 0)
})

test('real upload wrapper preserves HTTP validation errors so a corrected picture can be selected', async () => {
  const f = fixture('verify')
  const requests = []
  f.wx.uploadFile = options => { requests.push(options); options.success({ statusCode: 400, data: JSON.stringify({ detail: '图片格式不合法' }) }) }
  f.api.uploadRank = realUploadApi(f).uploadRank
  f.page.onLoad(); await f.page.onShow(); await f.page.uploadRankWithPath('/invalid.png')
  assert.equal(f.page._uncertainUpload, null)
  assert.equal(f.calls.toasts[0].title, '图片格式不合法')
  assert.equal(requests[0].name, 'file')
  assert.equal(requests[0].timeout, 240000)
  assert.equal(requests[0].header.Authorization, 'Bearer account-one')
  f.page.chooseRankImage()
  assert.equal(f.calls.selections.length, 1)
})

test('real upload wrapper keeps 5xx ambiguous and preserves 401 status without leaking into a new login', async () => {
  const f = fixture('verify', { getAssistantVerificationStatus: async () => { throw { statusCode: 503 } } })
  const api = realUploadApi(f)
  f.wx.uploadFile = options => options.success({ statusCode: 500, data: JSON.stringify({ detail: '服务暂不可用' }) })
  f.api.uploadRank = api.uploadRank
  f.page.onLoad(); await f.page.onShow(); await f.page.uploadRankWithPath('/synthetic.png')
  assert.ok(f.page._uncertainUpload)
  f.page.chooseRankImage()
  assert.equal(f.calls.selections.length, 0)
  assert.equal(f.calls.modals[0].title, '先核查上次上传结果')
  f.wx.uploadFile = options => options.success({ statusCode: 401, data: '{}' })
  await assert.rejects(api.uploadVerify('/synthetic-school.png'), error => error.statusCode === 401 && error.unauthorized === true)
  assert.equal(f.storage.token, undefined)
})

test('unknown uploads survive a fresh page instance and require an informed retry decision', async () => {
  const pending = deferred()
  const f = fixture('verify', { uploadRank: () => pending.promise,
    getAssistantVerificationStatus: async () => { throw { statusCode: 503 } } })
  f.page.onLoad(); await f.page.onShow()
  const work = f.page.uploadRankWithPath('/synthetic.png')
  f.page.onUnload()
  const fresh = f.newPage(); fresh.onLoad({ focus: 'rank', source: 'assistant' }); await fresh.onShow()
  fresh.chooseRankImage()
  assert.equal(f.calls.selections.length, 0)
  assert.equal(f.calls.modals[0].title, '先核查上次上传结果')
  f.calls.modals[0].success({ cancel: true })
  assert.match(f.calls.modals[1].content, /旧请求可能仍在处理.*重复消耗识别额度/)
  assert.equal(f.calls.selections.length, 0)
  f.calls.modals[1].success({ confirm: true })
  assert.equal(f.calls.modals[2].title, '认证材料用途')
  assert.equal(f.calls.selections.length, 0)
  f.calls.modals[2].success({ confirm: true })
  assert.equal(f.calls.selections.length, 1)
  pending.resolve({ need_review: true, rank: 'A+' }); await work
})

test('an unresolved marker is isolated from another login in a fresh page', async () => {
  const pending = deferred()
  const f = fixture('verify', { uploadRank: () => pending.promise })
  f.page.onLoad(); await f.page.onShow()
  const work = f.page.uploadRankWithPath('/synthetic.png'); f.page.onUnload()
  f.storage.token = 'account-two'
  const fresh = f.newPage(); fresh.onLoad(); await fresh.onShow(); fresh.chooseRankImage()
  assert.equal(f.calls.selections.length, 1)
  assert.equal(f.calls.modals.length, 0)
  pending.resolve({ need_review: true, rank: 'A+' }); await work
})

test('verification and assistant templates keep fixed controls and valid conditional chains', () => {
  const assistant = fs.readFileSync(path.join(root, 'miniprogram/pages/assistant/assistant.wxml'), 'utf8')
  const verify = fs.readFileSync(path.join(root, 'miniprogram/pages/verify/verify.wxml'), 'utf8')
  assert.match(verify, /id="verify-student"/)
  assert.match(verify, /id="verify-rank"/)
  const tree = parseWxml(assistant)
  tree.scope = Object.assign({}, fixture().page.data, { loading: false, enabled: true, available: true, logged: true })
  const html = createRenderer({})(tree)
  assert.match(html, /今天想了解什么/)
  assert.match(html, /帮我完成认证/)
  assert.match(html, /placeholder="问赛事、报名或认证…"/)
  assert.doesNotMatch(assistant, /verification-shortcuts|verification-panel|context-tools|scope-picker/)
  assert.doesNotMatch(html, /选择赛事|选择队伍|选择选手|我的认证|查询认证状态/)
  assert.match(assistant, /<button size="mini" class="send-button"/)
  const css = fs.readFileSync(path.join(root, 'miniprogram/pages/assistant/assistant.wxss'), 'utf8')
  assert.match(css, /\.send-button\s*\{[^}]*flex:0 0 104rpx;[^}]*width:104rpx;[^}]*height:72rpx/)
  assert.match(css, /\.new-conversation\s*\{[^}]*flex:0 0 auto;[^}]*width:auto/)
})
