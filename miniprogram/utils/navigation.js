const EVENTS_URL = '/pages/index/index'
let pendingReturn = null

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

module.exports = { EVENTS_URL, isEventsPage, returnToEvents }
