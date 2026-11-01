const EVENTS_URL = '/pages/index/index'
const ASSISTANT_URL = '/pages/assistant/assistant'
let pendingReturn = null
let pendingAssistantReturn = null

function pageRoute(page) {
  return page && page.route ? '/' + page.route.replace(/^\/+/, '') : ''
}

function isEventsPage(pages) {
  const stack = pages || getCurrentPages()
  return stack.length > 0 && pageRoute(stack[stack.length - 1]) === EVENTS_URL
}

// 主页面之间使用 reLaunch，不能假定上一页就是赛事首页。
function returnToEvents() {
  if (pendingReturn) return pendingReturn
  const pages = getCurrentPages()
  if (isEventsPage(pages)) return Promise.resolve(true)

  let settle
  const request = new Promise(resolve => { settle = resolve })
  pendingReturn = request
  const finish = success => {
    if (pendingReturn === request) pendingReturn = null
    settle(success)
  }
  const failed = () => {
    wx.showToast({ title: '返回失败，请重试', icon: 'none' })
    finish(false)
  }
  const openEvents = () => {
    try {
      wx.reLaunch({ url: EVENTS_URL, success: () => finish(true), fail: failed })
    } catch (_) {
      failed()
    }
  }

  // 复用已有首页，保留用户选择的赛事筛选和滚动位置。
  let homeIndex = -1
  for (let index = pages.length - 2; index >= 0; index--) {
    if (pageRoute(pages[index]) === EVENTS_URL) {
      homeIndex = index
      break
    }
  }
  if (homeIndex < 0) {
    openEvents()
  } else {
    try {
      wx.navigateBack({ delta: pages.length - 1 - homeIndex, success: () => finish(true), fail: openEvents })
    } catch (_) {
      openEvents()
    }
  }
  return request
}

// 助手详情入口只返回固定助手路由，不接收外部传入的跳转地址。
function returnToAssistant() {
  if (pendingAssistantReturn) return pendingAssistantReturn
  const pages = getCurrentPages()
  if (pages.length && pageRoute(pages[pages.length - 1]) === ASSISTANT_URL) return Promise.resolve(true)

  let settle
  const request = new Promise(resolve => { settle = resolve })
  pendingAssistantReturn = request
  const finish = success => {
    if (pendingAssistantReturn === request) pendingAssistantReturn = null
    settle(success)
  }
  const failed = () => {
    wx.showToast({ title: '返回失败，请重试', icon: 'none' })
    finish(false)
  }
  const relaunchAssistant = () => {
    try { wx.reLaunch({ url: ASSISTANT_URL, success: () => finish(true), fail: failed }) }
    catch (_) { failed() }
  }
  const replaceWithAssistant = () => {
    try { wx.redirectTo({ url: ASSISTANT_URL, success: () => finish(true), fail: relaunchAssistant }) }
    catch (_) { relaunchAssistant() }
  }
  const openAssistant = () => {
    // 微信页面栈最多 10 层；栈满时替换当前详情，避免 navigateTo 溢出。
    if (getCurrentPages().length >= 10) return replaceWithAssistant()
    try { wx.navigateTo({ url: ASSISTANT_URL, success: () => finish(true), fail: replaceWithAssistant }) }
    catch (_) { replaceWithAssistant() }
  }

  // 优先复用原助手 Page，保留对话、输入和滚动状态，也支持中间夹着其他页面。
  let assistantIndex = -1
  for (let index = pages.length - 2; index >= 0; index--) {
    if (pageRoute(pages[index]) === ASSISTANT_URL) {
      assistantIndex = index
      break
    }
  }
  if (assistantIndex < 0) openAssistant()
  else {
    try { wx.navigateBack({ delta: pages.length - 1 - assistantIndex, success: () => finish(true), fail: openAssistant }) }
    catch (_) { openAssistant() }
  }
  return request
}

module.exports = { EVENTS_URL, ASSISTANT_URL, isEventsPage, returnToEvents, returnToAssistant }
