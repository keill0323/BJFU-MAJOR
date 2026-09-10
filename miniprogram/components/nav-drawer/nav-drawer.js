const api = require('../../utils/api.js')
const { rankDisplay, rankBadge } = require('../../utils/rank.js')
const { returnToEvents } = require('../../utils/navigation.js')

Component({
  properties: {
    title: { type: String, value: '' },
    showAdminNotice: { type: Boolean, value: false }
  },
  data: {
    open: false, statusBarHeight: 20, navHeight: 44, capsuleSpace: 100,
    user: null, isManager: false, currentRoute: '',
    adminTodoCount: null, adminTodoBadge: '', adminTodoSummary: '', adminTodoError: false,
    navigation: [
      { title: '赛事中心', icon: 'grid', url: '/pages/index/index' },
      { title: '我的队伍', icon: 'team', url: '/pages/team/team' },
      { title: '所有队伍', icon: 'team', url: '/pages/teams/teams' },
      { title: '名人堂', icon: 'honor', url: '/pages/hall/hall' },
      { title: '个人资料', icon: 'shield', url: '/pages/profile/profile' }
    ]
  },
  lifetimes: {
    attached() {
      this._detached = false
      const info = wx.getWindowInfo ? wx.getWindowInfo() : wx.getSystemInfoSync()
      const statusBarHeight = info.statusBarHeight || 20
      const capsule = wx.getMenuButtonBoundingClientRect ? wx.getMenuButtonBoundingClientRect() : null
      const hasCapsule = capsule && capsule.width > 0 && capsule.top >= statusBarHeight
      const pages = getCurrentPages()
      const route = pages.length ? '/' + pages[pages.length - 1].route : ''
      this.setData({
        statusBarHeight,
        navHeight: hasCapsule ? (capsule.top - statusBarHeight) * 2 + capsule.height : 44,
        capsuleSpace: hasCapsule ? info.windowWidth - capsule.left + 8 : 100,
        currentRoute: route
      })
      this.loadUser()
    },
    detached() {
      this._detached = true
      if (this._todoUnsubscribe) this._todoUnsubscribe()
      this._todoUnsubscribe = null
    }
  },
  pageLifetimes: {
    show() { this.loadUser() }
  },
  methods: {
    loadUser() {
      const token = wx.getStorageSync('token')
      if (!token) {
        this.stopAdminTodos()
        this.setData({ user: null, isManager: false })
        this.triggerEvent('userchange', { user: null })
        return Promise.resolve()
      }
      // attached 与首页 onShow 共用在途请求，不增加首页的认证请求数。
      if (this._userLoading && this._userToken === token) return this._userLoading
      this._userToken = token
      const request = api.getMe().then(data => {
        if (this._detached || wx.getStorageSync('token') !== token) return
        const user = data ? Object.assign({}, data, {
          display_rank: rankDisplay(data.rank),
          rank_badge: rankBadge(data.rank),
          avatar_full: data.avatar ? (/^https?:\/\//.test(data.avatar) ? data.avatar : api.BASE + data.avatar) : ''
        }) : null
        this.setData({ user, isManager: !!user && (user.role === 'admin' || user.role === 'reviewer') })
        this.triggerEvent('userchange', { user })
        if (this.data.isManager) this.loadAdminTodos()
        else this.stopAdminTodos()
      }).catch(() => {
        if (!this._detached && !wx.getStorageSync('token')) {
          this.stopAdminTodos()
          this.setData({ user: null, isManager: false })
          this.triggerEvent('userchange', { user: null })
        }
      }).finally(() => {
        if (this._userLoading === request) this._userLoading = null
      })
      this._userLoading = request
      return request
    },
    loadAdminTodos() {
      if (!this.data.isManager || this._detached) return Promise.resolve()
      const todos = require('../../utils/admin-todos.js')
      if (!this._todoUnsubscribe) {
        this._todoUnsubscribe = todos.subscribe(state => {
          if (this._detached || !this.data.isManager) return
          const counts = state.data
          const count = counts ? Number(counts.total) || 0 : null
          this.setData({
            adminTodoCount: count,
            adminTodoBadge: count > 99 ? '99+' : count > 0 ? String(count) : '',
            adminTodoError: !!state.error,
            adminTodoSummary: state.error ? '待办更新失败，进入管理页重试' : counts
              ? '认证 ' + counts.verification_count + ' · 段位 ' + counts.rank_application_count + ' · 队伍 ' + counts.team_count + ' · 报名 ' + counts.registration_count : ''
          })
        })
      }
      return todos.refresh().catch(() => {})
    },
    stopAdminTodos() {
      if (this._todoUnsubscribe) this._todoUnsubscribe()
      this._todoUnsubscribe = null
      this.setData({ adminTodoCount: null, adminTodoBadge: '', adminTodoSummary: '', adminTodoError: false })
    },
    goAdmin() {
      if (!this.data.isManager) return
      this.setData({ open: false })
      wx.navigateTo({ url: '/pages/admin/admin' })
    },
    openDrawer() {
      this.setData({ open: true })
      if (this.data.isManager) this.loadAdminTodos()
    },
    closeDrawer() { this.setData({ open: false }) },
    goEvents() {
      this.setData({ open: false })
      return returnToEvents()
    },
    preventMove() {},
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
      } else if (url === '/pages/index/index') {
        return this.goEvents()
      } else if (mains.indexOf(url) >= 0) {
        wx.reLaunch({ url: target })
      } else {
        wx.navigateTo({ url: target })
      }
    }
  }
})
