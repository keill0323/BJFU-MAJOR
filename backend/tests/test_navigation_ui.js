// Run: node --test backend/tests/test_navigation_ui.js
// Navigation runs against isolated wx mocks and cannot open a live page.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const test = require('node:test')
const { parseWxml, createRenderer } = require('../../tools/preview-miniprogram.js')
const appRoot = path.resolve(__dirname, '../../miniprogram')

function setup(routes, overrides = {}) {
  const calls = { back: [], launches: [], toasts: [] }
  const pages = routes.map(route => ({ route }))
  const wx = Object.assign({
    navigateBack: options => calls.back.push(options),
    reLaunch: options => calls.launches.push(options),
    showToast: options => calls.toasts.push(options),
    getStorageSync: () => '',
    getWindowInfo: () => ({ statusBarHeight: 20, windowWidth: 390 }),
    getMenuButtonBoundingClientRect: () => ({ top: 24, left: 293, width: 87, height: 32 })
  }, overrides)
  const context = { wx, getCurrentPages: () => pages, module: { exports: {} }, console }
  vm.runInNewContext(fs.readFileSync(path.join(appRoot, 'utils/navigation.js'), 'utf8'), context)
  const navigation = context.module.exports
  function component(name) {
    let definition
    vm.runInNewContext(fs.readFileSync(path.join(appRoot, 'components', name, name + '.js'), 'utf8'), {
      ...context,
      Component: value => { definition = value },
      require(file) {
        if (file.endsWith('/navigation.js')) return navigation
        if (file.endsWith('/api.js')) return {}
        if (file.endsWith('/rank.js')) return require(path.join(appRoot, 'utils/rank.js'))
        throw new Error('Unexpected dependency: ' + file)
      }
    })
    const instance = Object.assign({ data: JSON.parse(JSON.stringify(definition.data || {})) }, definition.methods)
    instance.setData = values => Object.assign(instance.data, values)
    instance.triggerEvent = () => {}
    if (definition.lifetimes && definition.lifetimes.attached) definition.lifetimes.attached.call(instance)
    return instance
  }
  return { navigation, calls, pages, component }
}

test('return locates the actual events page across intermediate pages and keeps its state', async () => {
  const { navigation, calls, pages } = setup(['pages/index/index', 'pages/hall/hall', 'pages/match/match'])
  pages[0].data = { searchKeyword: '秋季', activeFilter: 'registration' }
  const result = navigation.returnToEvents()
  assert.equal(calls.back.length, 1)
  assert.equal(calls.back[0].delta, 2)
  assert.equal(calls.launches.length, 0)
  calls.back[0].success()
  assert.equal(await result, true)
  assert.equal(pages[0].data.searchKeyword, '秋季')
  assert.equal(pages[0].data.activeFilter, 'registration')
})

test('return chooses the nearest existing events page when it occurs twice in the stack', async () => {
  const { navigation, calls } = setup(['pages/index/index', 'pages/match/match', '/pages/index/index', 'pages/hall/hall'])
  const result = navigation.returnToEvents()
  assert.equal(calls.back[0].delta, 1)
  calls.back[0].success()
  assert.equal(await result, true)
})

test('directly launched pages return to events even if the previous page is unrelated', async () => {
  for (const routes of [[], ['pages/team/team'], ['pages/admin/admin', 'pages/admin/mdetail/mdetail']]) {
    const { navigation, calls } = setup(routes)
    const result = navigation.returnToEvents()
    assert.equal(calls.back.length, 0)
    assert.equal(calls.launches[0].url, '/pages/index/index')
    calls.launches[0].success()
    assert.equal(await result, true)
  }
})

test('rapid taps share one navigation while return or its fallback is pending', async () => {
  const { navigation, calls } = setup(['pages/index/index', 'pages/hall/hall'])
  const first = navigation.returnToEvents()
  assert.equal(navigation.returnToEvents(), first)
  assert.equal(calls.back.length, 1)
  calls.back[0].fail()
  assert.equal(navigation.returnToEvents(), first)
  assert.equal(calls.launches.length, 1)
  calls.launches[0].success()
  assert.equal(await first, true)
})

test('failed back navigation falls back to the explicit events route', async () => {
  const { navigation, calls } = setup(['pages/index/index', 'pages/profile/profile'])
  const result = navigation.returnToEvents()
  calls.back[0].fail({ errMsg: 'navigateBack:fail' })
  assert.equal(calls.launches[0].url, '/pages/index/index')
  calls.launches[0].success()
  assert.equal(await result, true)
  assert.equal(calls.toasts.length, 0)
})

test('a failed relaunch informs the user and releases the guard for a retry', async () => {
  const { navigation, calls } = setup(['pages/team/team'])
  const first = navigation.returnToEvents()
  calls.launches[0].fail({ errMsg: 'reLaunch:fail' })
  assert.equal(await first, false)
  assert.equal(calls.toasts[0].title, '返回失败，请重试')
  const second = navigation.returnToEvents()
  assert.notEqual(first, second)
  assert.equal(calls.launches.length, 2)
  calls.launches[1].success()
  assert.equal(await second, true)
})

test('synchronous navigation exceptions also fall back and release the tap guard', async () => {
  const { navigation, calls } = setup(['pages/index/index', 'pages/hall/hall'], {
    navigateBack() { throw new Error('navigation unavailable') },
    reLaunch() { throw new Error('navigation unavailable') }
  })
  assert.equal(await navigation.returnToEvents(), false)
  assert.equal(await navigation.returnToEvents(), false)
  assert.equal(calls.toasts.length, 2)
})

test('return from the events page is a no-op, including slash-normalized routes', async () => {
  for (const route of ['pages/index/index', '/pages/index/index']) {
    const { navigation, calls } = setup([route])
    assert.equal(navigation.isEventsPage(), true)
    assert.equal(await navigation.returnToEvents(), true)
    assert.equal(calls.back.length + calls.launches.length, 0)
  }
})

test('custom headers show a labelled return outside events while keeping title and menu', () => {
  const template = fs.readFileSync(path.join(appRoot, 'components/nav-drawer/nav-drawer.wxml'), 'utf8')
  const cases = [
    ['pages/index/index', '北林 MAJOR', false],
    ['pages/hall/hall', '名人堂', true],
    ['pages/team/team', '我的队伍', true],
    ['pages/verify/verify', '参赛认证', true]
  ]
  for (const [route, title, visible] of cases) {
    const { component } = setup([route])
    const header = component('nav-drawer')
    const tree = parseWxml(template)
    tree.scope = { ...header.data, title }
    const html = createRenderer({})(tree)
    assert.equal(html.includes('返回赛事'), visible)
    assert.ok(html.includes(title))
    assert.equal((html.match(/class="menu-button"/g) || []).length, 1)
    assert.equal(header.data.capsuleSpace, 105)
    assert.equal(header.data.navHeight, 40)
  }
})

test('custom and native return buttons share the same navigation and pending-tap guard', async () => {
  const { component, calls } = setup(['pages/index/index', 'pages/hall/hall'])
  const header = component('nav-drawer')
  const nativeReturn = component('event-return')
  header.data.open = true
  const first = header.goEvents()
  const second = nativeReturn.goEvents()
  assert.equal(header.data.open, false)
  assert.equal(first, second)
  assert.equal(calls.back.length, 1)
  calls.back[0].success()
  assert.equal(await first, true)
})

test('the drawer events destination reuses the existing home page too', async () => {
  const { component, calls } = setup(['pages/index/index', 'pages/hall/hall'])
  const header = component('nav-drawer')
  const result = header.go({ currentTarget: { dataset: { url: '/pages/index/index' } } })
  assert.equal(calls.back.length, 1)
  assert.equal(calls.launches.length, 0)
  calls.back[0].success()
  assert.equal(await result, true)
})

test('every registered secondary page exposes return navigation before loading, guest or error states', () => {
  const config = JSON.parse(fs.readFileSync(path.join(appRoot, 'app.json'), 'utf8'))
  const gated = node => ['wx:if', 'wx:elif', 'wx:else', 'wx:for', 'hidden'].some(key => key in (node.attrs || {}))
  for (const route of config.pages.filter(value => value !== 'pages/index/index')) {
    const pageConfig = JSON.parse(fs.readFileSync(path.join(appRoot, route + '.json'), 'utf8'))
    const custom = (pageConfig.navigationStyle || config.window.navigationStyle) === 'custom'
    const name = custom ? 'nav-drawer' : 'event-return'
    assert.equal(pageConfig.usingComponents && pageConfig.usingComponents[name], '/components/' + name + '/' + name, route + ': component registration')
    const tree = parseWxml(fs.readFileSync(path.join(appRoot, route + '.wxml'), 'utf8'))
    const nodes = []
    const collect = (node, parents = []) => {
      if (node.tag) nodes.push({ node, parents })
      for (const child of node.children || []) collect(child, [...parents, node])
    }
    collect(tree)
    const position = nodes.findIndex(entry => entry.node.tag === name)
    assert.ok(position >= 0, route + ': visible navigation component')
    const entry = nodes[position]
    assert.ok(!gated(entry.node) && !entry.parents.some(gated), route + ': navigation must be independent of page state')
    assert.ok(nodes.slice(0, position).every(value => !gated(value.node)), route + ': navigation must precede conditional page states')
    if (!custom) assert.equal(tree.children.find(node => node.tag).tag, 'event-return', route + ': native return should precede page content')
  }
})
