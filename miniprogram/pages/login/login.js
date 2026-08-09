/**
 * 登录页
 * 功能：微信一键登录，拿 token 后跳转首页
 * 流程：wx.login() 拿临时code → 后端换 JWT → 存本地 → 跳首页
 */
const api = require('../../utils/api.js')

Page({
  data: {},

  // 点登录按钮触发
  async handleLogin() {
    try {
      // ① 取 code：优先用本地存的（保持身份稳定），没有才找微信要
      let code = wx.getStorageSync('wx_code')
      if (!code) {
        const wxRes = await wx.login()
        code = wxRes.code
        wx.setStorageSync('wx_code', code)   // 存起来，下次编译还是同一身份
      }
      const data = await api.login(code)          // ② 交给后端换 JWT token
      wx.setStorageSync('token', data.access_token) // ③ 存本地，下次免登录
      wx.switchTab({ url: '/pages/index/index' })   // ④ 跳首页（tab 页必须用 switchTab）
    } catch (err) {
      console.error('登录失败', err)
      wx.showToast({ title: '登录失败', icon: 'error' })
    }
  }
})
