/**
 * 登录页
 * 功能：微信一键登录，拿 token 后跳转首页
 * 流程：wx.login() 拿临时code → 后端换 JWT → 存本地 → 跳首页
 */
const api = require('../../utils/api.js')

Page({
  data: {},

  // 进入登录页时：已有 token 则直接进首页，无需重复登录
  onLoad() {
    if (wx.getStorageSync('token')) {
      wx.switchTab({ url: '/pages/index/index' })
    }
  },

  // 查看赛事规则与参赛须知
  showRules() {
    wx.showModal({
      title: '赛事规则与参赛须知',
      content: '1. 本赛事仅限北京林业大学在校学生参加。\n2. 报名需上传学信网/校园卡截图进行实名认证。\n3. 每位同学同一时间只能加入一支队伍。\n4. 报名信息须真实有效，弄虚作假将取消参赛资格。\n5. 请遵守比赛规则，文明竞技。',
      showCancel: false,
      confirmText: '我知道了'
    })
  },

  // 点登录按钮触发
  async handleLogin() {
    try {
      // ① 每次登录都重新取 code（code 是一次性、5 分钟有效的，不能缓存）
      const wxRes = await wx.login()
      const code = wxRes.code
      const data = await api.login(code)          // ② 交给后端换 JWT token
      wx.setStorageSync('token', data.access_token) // ③ 存本地，下次免登录
      wx.switchTab({ url: '/pages/index/index' })   // ④ 跳首页（tab 页必须用 switchTab）
    } catch (err) {
      console.error('登录失败', err)
      wx.showToast({ title: '登录失败', icon: 'error' })
    }
  }
})
