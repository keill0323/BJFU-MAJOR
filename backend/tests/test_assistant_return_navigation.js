// Run: node --test backend/tests/test_assistant_return_navigation.js
// Offline wx mocks cover routing/state reuse; native WeChat acceptance remains separate.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const test = require('node:test')
const { parseWxml, createRenderer } = require('../../tools/preview-miniprogram.js')
const appRoot = path.resolve(__dirname, '../../miniprogram')
const ASSISTANT_URL = '/pages/assistant/assistant'

function setup(routes, overrides = {}) {
  const calls = { back: [], open: [], replace: [], launch: [], toast: [] }
  const pages = routes.map(route => ({ route }))
  const wx = Object.assign({
    navigateBack: options => calls.back.push(options),
    navigateTo: options => calls.open.push(options),
    redirectTo: options => calls.replace.push(options),
    reLaunch: options => calls.launch.push(options),
    showToast: options => calls.toast.push(options)
  }, overrides)
  const context = { wx, getCurrentPages: () => pages, module: { exports: {} }, console }
  vm.runInNewContext(fs.readFileSync(path.join(appRoot, 'utils/navigation.js'), 'utf8'), context)
  const navigation = context.module.exports
  const component = source => {
    let definition
    vm.runInNewContext(fs.readFileSync(path.join(appRoot, 'components/event-return/event-return.js'), 'utf8'), {
      Component: value => { definition = value }, require: () => navigation
    })
    return Object.assign({ data: { source: source == null ? definition.properties.source.value : source } }, definition.methods)
  }
  return { pages, calls, navigation, component }
}

test('assistant return reuses the nearest original Page through intermediate detail pages', async () => {
  const { pages, calls, navigation } = setup([
    'pages/assistant/assistant', 'pages/match/match', '/pages/assistant/assistant', 'pages/team/team', 'pages/match/match'
  ])
  pages[2].data = { messages: [{ content: '原对话' }], draft: '未发送内容', conversationId: 'existing' }
  const retained = pages[2]
  const result = navigation.returnToAssistant()
  assert.equal(calls.back[0].delta, 2)
  assert.equal(calls.open.length + calls.replace.length + calls.launch.length, 0)
  calls.back[0].success()
  assert.equal(await result, true)
  assert.equal(pages[2], retained)
  assert.equal(pages[2].data.conversationId, 'existing')
  assert.equal(pages[2].data.draft, '未发送内容')
})

test('assistant return is a no-op on the assistant Page', async () => {
  const { calls, navigation } = setup(['/pages/assistant/assistant'])
  assert.equal(await navigation.returnToAssistant(), true)
  assert.equal(calls.back.length + calls.open.length + calls.replace.length + calls.launch.length, 0)
})

test('missing assistant opens only its fixed route and ignores an injected destination', async () => {
  for (const routes of [[], ['pages/match/match'], ['pages/index/index', 'pages/match/match']]) {
    const { calls, navigation } = setup(routes)
    const result = navigation.returnToAssistant('https://untrusted.invalid/anything')
    assert.equal(calls.back.length, 0)
    assert.equal(calls.open[0].url, ASSISTANT_URL)
    calls.open[0].success()
    assert.equal(await result, true)
  }
})

test('a full WeChat page stack replaces the detail instead of pushing an eleventh Page', async () => {
  const { calls, navigation } = setup(Array.from({ length: 10 }, () => 'pages/match/match'))
  const result = navigation.returnToAssistant()
  assert.equal(calls.open.length, 0)
  assert.equal(calls.replace[0].url, ASSISTANT_URL)
  calls.replace[0].success()
  assert.equal(await result, true)
})

test('a full stack still reuses an existing assistant before considering replacement', async () => {
  const { calls, navigation } = setup(['pages/assistant/assistant', ...Array.from({ length: 9 }, () => 'pages/match/match')])
  const result = navigation.returnToAssistant()
  assert.equal(calls.back[0].delta, 9)
  assert.equal(calls.open.length + calls.replace.length, 0)
  calls.back[0].success()
  assert.equal(await result, true)
})

test('failed navigateBack falls back to opening the fixed assistant route', async () => {
  const { calls, navigation } = setup(['pages/assistant/assistant', 'pages/match/match'])
  const result = navigation.returnToAssistant()
  calls.back[0].fail()
  assert.equal(calls.open[0].url, ASSISTANT_URL)
  calls.open[0].success()
  assert.equal(await result, true)
})

test('failed push and replacement use a final fixed assistant relaunch', async () => {
  const { calls, navigation } = setup(['pages/match/match'])
  const result = navigation.returnToAssistant()
  calls.open[0].fail()
  assert.equal(calls.replace[0].url, ASSISTANT_URL)
  calls.replace[0].fail()
  assert.equal(calls.launch[0].url, ASSISTANT_URL)
  calls.launch[0].success()
  assert.equal(await result, true)
  assert.equal(calls.toast.length, 0)
})

test('rapid return taps share one request throughout the fallback chain', async () => {
  const { calls, navigation } = setup(['pages/assistant/assistant', 'pages/match/match'])
  const first = navigation.returnToAssistant()
  assert.equal(navigation.returnToAssistant(), first)
  calls.back[0].fail()
  assert.equal(navigation.returnToAssistant(), first)
  calls.open[0].fail()
  assert.equal(navigation.returnToAssistant(), first)
  calls.replace[0].success()
  assert.equal(await first, true)
  assert.equal(calls.back.length, 1)
  assert.equal(calls.open.length, 1)
  assert.equal(calls.replace.length, 1)
})

test('final navigation failure explains the failure and allows another attempt', async () => {
  const { calls, navigation } = setup(Array.from({ length: 10 }, () => 'pages/match/match'))
  const first = navigation.returnToAssistant()
  calls.replace[0].fail()
  calls.launch[0].fail()
  assert.equal(await first, false)
  assert.equal(calls.toast[0].title, '返回失败，请重试')
  const retry = navigation.returnToAssistant()
  assert.notEqual(retry, first)
  calls.replace[1].success()
  assert.equal(await retry, true)
})

test('synchronous WeChat navigation errors also release the retry guard', async () => {
  const fail = () => { throw new Error('wx unavailable') }
  const { calls, navigation } = setup(['pages/assistant/assistant', 'pages/match/match'], {
    navigateBack: fail, navigateTo: fail, redirectTo: fail, reLaunch: fail
  })
  assert.equal(await navigation.returnToAssistant(), false)
  assert.equal(await navigation.returnToAssistant(), false)
  assert.equal(calls.toast.length, 2)
})

test('return component switches only the whitelisted assistant source, including its accessible label', async () => {
  const template = fs.readFileSync(path.join(appRoot, 'components/event-return/event-return.wxml'), 'utf8')
  for (const source of ['assistant', '', 'Assistant', '/pages/assistant/assistant', 'https://untrusted.invalid']) {
    const { component, calls } = setup(['pages/index/index', 'pages/assistant/assistant', 'pages/match/match'])
    const control = component(source)
    const tree = parseWxml(template)
    tree.scope = control.data
    const html = createRenderer({})(tree)
    const isAssistant = source === 'assistant'
    const button = tree.children.find(node => node.tag).children.find(node => node.tag === 'button')
    const accessibleLabel = vm.runInNewContext(button.attrs['aria-label'].slice(2, -2), control.data)
    assert.equal(accessibleLabel, isAssistant ? '返回助手' : '返回赛事中心')
    assert.ok(html.includes('>' + (isAssistant ? '返回助手' : '返回赛事') + '</span>'))
    const result = control.goEvents()
    assert.equal(calls.back[0].delta, isAssistant ? 1 : 2)
    calls.back[0].success()
    assert.equal(await result, true)
  }
})

test('match records the whitelisted source before loading so errors retain a useful return', async () => {
  let definition
  vm.runInNewContext(fs.readFileSync(path.join(appRoot, 'pages/match/match.js'), 'utf8'), {
    Page: value => { definition = value }, require: () => ({}), console
  })
  for (const source of ['assistant', undefined, 'Assistant', '/pages/assistant/assistant']) {
    let detailRequests = 0
    const page = Object.assign({}, definition, {
      data: { ...definition.data },
      setData(values) { Object.assign(this.data, values) },
      requestDetail() { detailRequests++ }
    })
    await page.onLoad({ source })
    assert.equal(page.data.returnSource, source === 'assistant' ? 'assistant' : '')
    assert.equal(detailRequests, 0)
    await page.onLoad({ source, id: '9' })
    assert.equal(page.matchId, '9')
    assert.equal(detailRequests, 1)
  }
  const tree = parseWxml(fs.readFileSync(path.join(appRoot, 'pages/match/match.wxml'), 'utf8'))
  const returnControl = tree.children.find(node => node.tag)
  assert.equal(returnControl.tag, 'event-return')
  assert.equal(returnControl.attrs.source, '{{returnSource}}')
  for (const returnSource of ['', 'assistant']) {
    const html = createRenderer({})({ children: [returnControl], scope: { returnSource } })
    assert.ok(html.includes(returnSource ? '返回助手' : '返回赛事'))
  }
})
