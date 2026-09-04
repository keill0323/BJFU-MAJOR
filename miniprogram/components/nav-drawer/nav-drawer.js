// 通用顶部栏 + 左侧抽屉导航（X 风格）组件
const api = require('../../utils/api.js')
const { rankDisplay } = require('../../utils/rank.js')

Component({
  properties: {
    title: { type: String, value: '' }
  },
  data: {
    open: false,
    statusBarHeight: 20,
    user: null,
    isManager: false
  },
  lifetimes: {
    attached() {
      const info = wx.getSystemInfoSync()
      this.setData({ statusBarHeight: info.statusBarHeight || 20 })
      this.loadUser()
    }
  },
  methods: {
    loadUser() {
      if (!wx.getStorageSync('token')) return
      api.getMe().then(user => {
        if (!user) return
        user.display_rank = rankDisplay(user.rank)
        if (user.avatar && !user.avatar.startsWith('http')) user.avatar_full = api.BASE + user.avatar
        this.setData({ user, isManager: user.role === 'admin' || user.role === 'reviewer' })
      }).catch(() => {})
    },
    openDrawer() { this.setData({ open: true }) },
    closeDrawer() { this.setData({ open: false }) },
    go(e) {
      const url = e.currentTarget.dataset.url
      const seg = e.currentTarget.dataset.seg || ''
      const tab = e.currentTarget.dataset.tab || ''
      this.setData({ open: false })
      let target = url
      if (seg) target += '?seg=' + seg
      else if (tab) target += '?tab=' + tab
      const mains = ['/pages/index/index', '/pages/team/team', '/pages/teams/teams', '/pages/profile/profile', '/pages/messages/messages', '/pages/verify/verify', '/pages/settings/settings']
      if (url === '/pages/login/login') {
        wx.removeStorageSync('token')
        wx.reLaunch({ url: target })
      } else if (mains.indexOf(url) >= 0) {
        wx.reLaunch({ url: target })
      } else {
        wx.navigateTo({ url: target })
      }
    }
  }
})
