const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const root = path.resolve(__dirname, '../../miniprogram')
const agreeEvent = () => ({ currentTarget: { id: 'agree-privacy-images' } })

function fixture({ autoSettings = true } = {}) {
  const state = { listener: null, pages: [], settings: [], toasts: [], modals: [], contracts: [],
    media: [], compressions: [], uploads: [], granted: false, chooseResult: { tempFiles: [{ tempFilePath: '/selected.jpg' }] } }
  const wx = {
    getStorageSync: () => 'fixture-token',
    onNeedPrivacyAuthorization: listener => { state.listener = listener },
    getPrivacySetting: options => {
      state.settings.push(options)
      if (autoSettings) options.success({ needAuthorization: true, privacyContractName: '《起源杯隐私保护指引》' })
    },
    openPrivacyContract: options => state.contracts.push(options),
    showModal: options => state.modals.push(options),
    showToast: options => state.toasts.push(options),
    chooseMedia: options => {
      state.media.push(options)
      const finish = () => options.success(state.chooseResult)
      if (state.granted) finish()
      else state.listener(result => {
        if (result.event === 'agree') {
          // 模拟微信核验：同意回调必须来自仍显示在当前页面的专用按钮。
          assert.equal(result.buttonId, agreeEvent().currentTarget.id)
          assert.equal(dialog.data.visible, true)
          state.granted = true
          finish()
        } else options.fail({ errMsg: 'chooseMedia:fail privacy permission is not authorized' })
      })
    },
    compressImage: options => { state.compressions.push(options); options.success({ tempFilePath: '/compressed.jpg' }) }
  }
  function load(file) {
    let definition
    const module = { exports: {} }
    const filename = path.join(root, file)
    vm.runInNewContext(fs.readFileSync(filename, 'utf8'), {
      wx, console, module, exports: module.exports,
      getCurrentPages: () => state.pages,
      App: value => { definition = value },
      Page: value => { definition = value },
      Component: value => { definition = value },
      require: request => request.endsWith('/api.js') ? {} : load(path.relative(root, path.resolve(path.dirname(filename), request)))
    }, { filename })
    return definition || module.exports
  }
  const dialogDefinition = load('components/privacy-dialog/privacy-dialog.js')
  const dialog = { ...dialogDefinition.methods, data: JSON.parse(JSON.stringify(dialogDefinition.data)) }
  dialog.setData = value => Object.assign(dialog.data, value)
  dialogDefinition.lifetimes.attached.call(dialog)
  const app = load('app.js')
  app.onLaunch()
  function page(name) {
    const definition = load('pages/' + name + '/' + name + '.js')
    const result = { ...definition, data: JSON.parse(JSON.stringify(definition.data)),
      selectComponent: id => { assert.equal(id, '#privacy-dialog'); return dialog } }
    result.setData = value => Object.assign(result.data, value)
    for (const method of ['uploadWithPath', 'uploadRankWithPath', 'uploadLogoPath', 'uploadAvatar']) {
      if (result[method]) result[method] = value => state.uploads.push({ method, value })
    }
    state.pages = [result]
    return result
  }
  return { state, wx, app, dialog, page, load,
    detach: () => dialogDefinition.lifetimes.detached.call(dialog) }
}

for (const [name, action, upload] of [
  ['verify', 'chooseVerifyImage', 'uploadWithPath'],
  ['verify', 'chooseRankImage', 'uploadRankWithPath'],
  ['team', 'uploadLogo', 'uploadLogoPath']
]) {
  test(action + ': fresh consent resumes the original selection and uploads exactly once', () => {
    const { state, dialog, page } = fixture()
    const current = page(name)
    current.data.myTeam = { id: 10 }
    current.data.isCaptain = true
    current[action]()
    assert.equal(dialog.data.visible, true)
    assert.equal(dialog.data.contractName, '《起源杯隐私保护指引》')
    assert.equal(state.modals.length, 0, 'ordinary showModal cannot grant privacy authorization')
    assert.equal(state.compressions.length, 0)
    assert.equal(state.uploads.length, 0)
    dialog.agree(agreeEvent())
    assert.deepEqual(state.uploads, [{ method: upload, value: '/compressed.jpg' }])
    assert.equal(state.media.length, 1, 'do not call chooseMedia again after native resume')
    assert.equal(dialog.data.visible, false)
    dialog.agree(agreeEvent())
    assert.equal(state.uploads.length, 1)
  })
}

test('avatar capability uses the same dialog and continues only after its privacy button event', () => {
  const { state, dialog, page } = fixture()
  const current = page('profile')
  state.listener(result => {
    if (result.event === 'agree') {
      assert.equal(result.buttonId, agreeEvent().currentTarget.id)
      assert.equal(dialog.data.visible, true)
      current.onChooseAvatar({ detail: { avatarUrl: '/avatar.jpg' } })
    }
  })
  assert.equal(state.uploads.length, 0)
  dialog.agree({ currentTarget: { id: 'ordinary-modal-button' } })
  assert.equal(state.uploads.length, 0)
  dialog.agree(agreeEvent())
  assert.deepEqual(state.uploads, [{ method: 'uploadAvatar', value: '/avatar.jpg' }])
  assert.equal(state.media.length, 0)
})

test('an already-authorized user still selects directly without another privacy prompt', () => {
  const { state, dialog, page } = fixture()
  state.granted = true
  page('verify').chooseVerifyImage()
  assert.equal(dialog.data.visible, false)
  assert.equal(state.settings.length, 0)
  assert.equal(state.uploads.length, 1)
})

test('declining ends the pending operation and a later tap can authorize again', () => {
  const { state, dialog, page } = fixture()
  const current = page('verify')
  current.chooseVerifyImage()
  dialog.disagree()
  assert.equal(state.uploads.length, 0)
  assert.equal(dialog._resolvers.size, 0)
  assert.match(state.toasts[0].title, /未同意/)
  current.chooseVerifyImage()
  dialog.agree(agreeEvent())
  assert.equal(state.uploads.length, 1)
  assert.equal(state.media.length, 2)
})

test('concurrent privacy requests are settled once each without overwriting an earlier resolver', () => {
  const { state, dialog, page } = fixture()
  page('verify')
  const results = []
  const first = value => results.push(['first', value.event])
  state.listener(first)
  state.listener(first)
  state.listener(value => results.push(['second', value.event]))
  assert.equal(state.settings.length, 1)
  dialog.agree(agreeEvent())
  dialog.disagree()
  assert.deepEqual(results, [['first', 'agree'], ['second', 'agree']])
})

test('opening the official contract does not itself agree, and viewing failures keep consent pending', () => {
  const { state, dialog, page } = fixture()
  page('verify').chooseVerifyImage()
  dialog.openContract()
  assert.equal(state.contracts.length, 1)
  assert.equal(state.uploads.length, 0)
  state.contracts[0].fail({ errMsg: 'openPrivacyContract:fail' })
  assert.match(state.toasts.at(-1).title, /打开失败/)
  assert.equal(dialog.data.visible, true)
  assert.equal(dialog._resolvers.size, 1)
})

test('failed contract metadata can be retried and stale responses cannot reopen a closed dialog', () => {
  const { state, dialog, page } = fixture({ autoSettings: false })
  page('verify').chooseVerifyImage()
  dialog.agree(agreeEvent())
  assert.equal(state.uploads.length, 0)
  state.settings[0].fail()
  assert.match(dialog.data.error, /重新|重试/)
  dialog.loadContract()
  state.settings[1].success({ privacyContractName: '本次正式指引' })
  assert.equal(dialog.data.ready, true)
  state.settings[0].success({ privacyContractName: '旧回复' })
  assert.equal(dialog.data.contractName, '本次正式指引')
  dialog.disagree()
  state.settings[1].success({ privacyContractName: '迟到回复' })
  assert.equal(dialog.data.visible, false)
  assert.equal(dialog.data.ready, false)
})

test('leaving the page rejects pending callbacks and ignores late metadata and button events', () => {
  const { state, dialog, page, detach } = fixture({ autoSettings: false })
  page('verify').chooseVerifyImage()
  let updates = 0
  dialog.setData = () => { updates++ }
  detach()
  state.settings[0].success({ privacyContractName: '迟到回复' })
  dialog.agree(agreeEvent())
  let rejected = false
  dialog.requestAuthorization(result => { rejected = result.event === 'disagree' })
  assert.equal(rejected, true)
  assert.equal(dialog._resolvers.size, 0)
  assert.equal(state.uploads.length, 0)
  assert.equal(updates, 0)
})

test('an absent current page or dialog rejects instead of leaving WeChat pending', () => {
  const { state } = fixture()
  for (const pages of [[], [{}], [{ selectComponent: () => null }]]) {
    state.pages = pages
    const results = []
    state.listener(result => results.push(result.event))
    assert.deepEqual(results, ['disagree'])
  }
  assert.equal(state.toasts.length, 3)
})

test('dispatch uses the visible top page, not a previous page in the navigation stack', () => {
  const { state, dialog, page } = fixture()
  const current = page('verify')
  state.pages.unshift({ selectComponent: () => { throw Error('hidden page must not receive consent') } })
  current.chooseVerifyImage()
  assert.equal(dialog.data.visible, true)
})

test('older API availability never turns an ordinary tap into synthetic authorization', () => {
  const { state, wx, dialog, page, app } = fixture()
  delete wx.getPrivacySetting
  page('verify').chooseVerifyImage()
  assert.equal(dialog.data.ready, false)
  assert.match(dialog.data.error, /更新微信/)
  dialog.agree(agreeEvent())
  assert.equal(state.uploads.length, 0)
  dialog.disagree()
  delete wx.onNeedPrivacyAuthorization
  assert.doesNotThrow(() => app._registerPrivacy())
})

test('selection cancellation is quiet, permission failures are explained, and empty results never upload', () => {
  const { state, wx, page } = fixture()
  const current = page('verify')
  state.granted = true
  state.chooseResult = { tempFiles: [] }
  current.chooseVerifyImage()
  assert.equal(state.uploads.length, 0)
  assert.match(state.toasts.at(-1).title, /选图失败/)
  const count = state.toasts.length
  state.media[0].fail({ errMsg: 'chooseMedia:fail cancel' })
  assert.equal(state.toasts.length, count)
  state.media[0].fail({ errMsg: 'chooseMedia:fail auth deny' })
  assert.match(state.modals[0].content, /权限/)
  wx.compressImage = options => options.fail({ errMsg: 'compressImage:fail' })
  state.chooseResult = { tempFiles: [{ tempFilePath: '/original.jpg' }] }
  current.chooseRankImage()
  assert.deepEqual(state.uploads, [{ method: 'uploadRankWithPath', value: '/original.jpg' }])
})

test('avatar errors are visible and missing avatar results are harmless', () => {
  const { state, page } = fixture()
  const current = page('profile')
  current.onChooseAvatar({ detail: {} })
  current.onChooseAvatar({})
  assert.equal(state.uploads.length, 0)
  current.onAvatarError({ detail: { errMsg: 'chooseAvatar:fail privacy permission is not authorized' } })
  assert.match(state.toasts[0].title, /隐私/)
})

test('every image entry page has a dialog and agreement is wired to the native privacy event', () => {
  const config = JSON.parse(fs.readFileSync(path.join(root, 'app.json'), 'utf8'))
  assert.equal(config.usingComponents['privacy-dialog'], '/components/privacy-dialog/privacy-dialog')
  for (const route of config.pages) {
    const js = fs.readFileSync(path.join(root, route + '.js'), 'utf8')
    const wxml = fs.readFileSync(path.join(root, route + '.wxml'), 'utf8')
    if (/wx\.chooseMedia|wx\.chooseImage/.test(js) || /open-type="chooseAvatar"/.test(wxml)) {
      assert.match(wxml, /<privacy-dialog id="privacy-dialog"><\/privacy-dialog>/, route)
    }
  }
  const wxml = fs.readFileSync(path.join(root, 'components/privacy-dialog/privacy-dialog.wxml'), 'utf8')
  assert.match(wxml, /id="agree-privacy-images"[^>]*open-type="agreePrivacyAuthorization"[^>]*bindagreeprivacyauthorization="agree"/)
  assert.doesNotMatch(wxml, /bindtap="agree"/)
  assert.match(wxml, /bindtap="openContract"/)
})
