/**
 * 登录页
 * 功能：微信一键登录，拿 token 后跳转首页
 * 流程：wx.login() 拿临时code → 后端换 JWT → 存本地 → 跳首页
 */
const api = require('../../utils/api.js')

Page({
  data: {
    agreed: false
  },

  // 进入登录页时：已有 token 则直接进首页，无需重复登录
  onLoad() {
    if (wx.getStorageSync('token')) {
      wx.reLaunch({ url: '/pages/index/index' })
    }
  },

  // 勾选 / 取消勾选协议
  toggleAgree() {
    this.setData({ agreed: !this.data.agreed })
  },

  // 查看赛事规则与参赛须知
  showRules() {
    wx.navigateTo({ url: '/pages/rules/rules' })
  },

  // 点登录按钮触发
  async handleLogin() {
    // 未勾选协议禁止登录
    if (!this.data.agreed) {
      wx.showToast({ title: '请先阅读并同意用户服务协议和隐私政策', icon: 'none' })
      return
    }
    try {
      // ① 每次登录都重新取 code（code 是一次性、5 分钟有效的，不能缓存）
      const wxRes = await wx.login()
      const code = wxRes.code
      const data = await api.login(code)          // ② 交给后端换 JWT token
      wx.setStorageSync('token', data.access_token) // ③ 存本地，下次免登录
      wx.reLaunch({ url: '/pages/index/index' })   // ④ 跳首页（tab 页必须用 switchTab）
    } catch (err) {
      console.error('登录失败', err)
      wx.showToast({ title: '登录失败', icon: 'error' })
    }
  }
})
