/**
 * API 请求封装层
 * 作用：前端与后端通信的唯一入口，所有页面都通过这里调接口
 * 用法：const api = require('../../utils/api.js'); await api.login(code)
 *
 * 目录说明：
 *   request()     — 通用请求函数，处理 URL 拼接、token、错误判断
 *   module.exports — 把各接口映射成易读的函数，页面层只跟这些函数打交道
 */

/**
 * 后端地址说明：
 *  - 开发者工具模拟器：可用 http://127.0.0.1:8000
 *  - 真机调试/预览：必须用电脑的局域网 IP（手机与电脑同一 WiFi），且后端要监听 0.0.0.0
 *  - 上线：换成 HTTPS 域名
 */
const BASE = 'http://192.168.16.170:8000'   // 电脑当前局域网 IP（WLAN）

/**
 * 通用请求函数
 * @param path  接口路径，如 '/api/auth/login'
 * @param method HTTP 方法，默认 GET
 * @param data  请求体（JSON 对象），GET 请求可省略
 * @returns Promise，成功 resolve 后端返回的数据，失败 reject 错误信息
 */
function request(path, method = 'GET', data = {}) {
  const token = wx.getStorageSync('token')   // 从本地存储读 JWT
  return new Promise((resolve, reject) => {
    wx.request({
      url: BASE + path,          // 拼完整地址
      method: method,
      data: data,
      header: {
        'Authorization': token ? 'Bearer ' + token : ''   // 有 token 就带上
      },
      success(res) {
        if (res.statusCode < 400) {
          resolve(res.data)      // 2xx/3xx：成功，返回数据
        } else {
          // 401 = 令牌无效或用户不存在（如删库后），清 token 回登录页
          if (res.statusCode === 401) {
            wx.removeStorageSync('token')
            wx.redirectTo({ url: '/pages/login/login' })
          }
          // 统一错误信息：422 校验错误的 detail 是数组，转成可读字符串
          let detail = res.data && res.data.detail
          if (Array.isArray(detail)) {
            detail = detail.map(d => d.msg || JSON.stringify(d)).join('；')
          }
          reject({ detail: detail || ('请求失败（' + res.statusCode + '）') })
        }
      },
      fail() {
        reject({ detail: '网络错误，请检查后端服务器是否启动' })  // 网络层失败
      }
    })
  })
}


/**
 * 接口映射表
 * 每个方法对应后端一个路由，字段名与后端 Pydantic schema 保持一致
 */
module.exports = {
  BASE: BASE,   // 后端地址（拼接上传图片完整 URL 用）

  // ===== 认证 =====
  login: (code) => request('/api/auth/login', 'POST', { code }),          // 登录，code 是微信登录凭证
  getMe: () => request('/api/auth/me'),                                    // 获取当前用户完整信息
  updateProfile: (data) => request('/api/auth/profile', 'PUT', data),     // 修改昵称/游戏ID

  // 上传学信网截图（multipart，走 wx.uploadFile 不走通用 request）
  uploadVerify: (filePath) => new Promise((resolve, reject) => {
    const token = wx.getStorageSync('token')
    wx.uploadFile({
      url: BASE + '/api/auth/upload-verify',
      filePath: filePath,
      name: 'file',
      header: { 'Authorization': token ? 'Bearer ' + token : '' },
      success(res) {
        if (res.statusCode < 400) {
          try {
            resolve(JSON.parse(res.data))
          } catch (e) {
            reject({ detail: '上传响应解析失败' })
          }
        } else {
          let detail = ''
          try { detail = JSON.parse(res.data).detail || '' } catch (e) {}
          if (res.statusCode === 401) {
            wx.removeStorageSync('token')
            wx.redirectTo({ url: '/pages/login/login' })
          }
          reject({ detail: detail || ('上传失败（' + res.statusCode + '）') })
        }
      },
      fail(err) {
        console.error('uploadVerify 失败详情:', err)
        reject({ detail: '网络错误：' + (err.errMsg || '请检查后端服务器是否启动') })
      }
    })
  }),

  // ===== 队伍 =====
  createTeam: (name) => request('/api/teams', 'POST', { name }),          // 创建队伍
  getTeams: () => request('/api/teams'),                                  // 队伍列表
  getTeam: (id) => request('/api/teams/' + id),                           // 查某支队伍详情
  getMyTeam: () => request('/api/teams/my'),                              // 查我的队伍（按 token）
  getTalentMarket: (matchId) => request('/api/teams/talent-market?match_id=' + matchId),  // 人才市场：某赛事的自由人
  recruitByStudent: (teamId, studentId) => request('/api/teams/recruit', 'POST', { team_id: teamId, student_id: studentId }),  // 队长按学号拉人入队
  joinTeam: (teamId, userId) => request('/api/teams/join', 'POST', { team_id: teamId, user_id: userId }),  // 队长拉人
  leaveTeam: (teamId) => request('/api/teams/' + teamId + '/leave', 'DELETE'),  // 主动退队
  kickMember: (teamId, userId) => request('/api/teams/' + teamId + '/members/' + userId, 'DELETE'),  // 队长踢人
  disbandTeam: (teamId) => request('/api/teams/' + teamId, 'DELETE'),     // 解散队伍

  // ===== 入队申请 =====
  applyJoin: (teamId, message) => request('/api/teams/' + teamId + '/apply', 'POST', { team_id: teamId, message: message || '' }),  // 申请加入
  getApplications: (teamId) => request('/api/teams/' + teamId + '/applications'),  // 队长看申请
  approveApplication: (appId) => request('/api/teams/applications/' + appId + '/approve', 'POST'),  // 同意申请
  rejectApplication: (appId) => request('/api/teams/applications/' + appId + '/reject', 'POST'),  // 拒绝申请

  // ===== 入队邀请 =====
  invitePlayer: (teamId, userId, message) => request('/api/teams/' + teamId + '/invite', 'POST', { user_id: userId, message: message || '' }),  // 队长发邀请
  getMyInvitations: () => request('/api/teams/invitations/my'),  // 我收到的邀请
  acceptInvitation: (invId) => request('/api/teams/invitations/' + invId + '/accept', 'POST'),  // 同意邀请
  rejectInvitation: (invId) => request('/api/teams/invitations/' + invId + '/reject', 'POST'),  // 拒绝邀请

  // ===== 赛事 =====
  getMatches: () => request('/api/matches'),                              // 全部赛事
  getMatch: (id) => request('/api/matches/' + id),                        // 赛事详情
  getRounds: (matchId) => request('/api/matches/' + matchId + '/rounds'), // 某赛事的对阵
  getMatchDetail: (matchId) => request('/api/matches/' + matchId + '/detail'),  // 赛事详情（队伍进度+对阵，用户端展示）
  searchMatches: (kw) => request('/api/matches/search?keyword=' + kw),    // 按名字搜索赛事

  // ===== 报名 =====
  registerTeam: (matchId, teamId) => request('/api/matches/' + matchId + '/register/team?team_id=' + teamId, 'POST'),  // 队伍报名
  registerUser: (matchId) => request('/api/matches/' + matchId + '/register/user', 'POST'),  // 个人报名
  getMyRegistration: (matchId) => request('/api/matches/' + matchId + '/my-registration'),  // 我是否已报名

  // ===== 管理后台（admin/reviewer） =====
  adminListUsers: (keyword) => request('/api/auth/admin/users' + (keyword ? '?keyword=' + keyword : '')),  // 用户列表/搜索
  getVerifyList: () => request('/api/auth/admin/verify-list'),  // 待认证审核列表（已上传截图未通过）
  adminUpdateUser: (userId, data) => request('/api/auth/admin/users/' + userId, 'PUT', data),  // 改学号/段位/评分/认证
  adminUpdateRole: (userId, role) => request('/api/auth/admin/users/' + userId + '/role', 'PUT', { role: role }),  // 改角色
  createMatch: (data) => request('/api/matches', 'POST', data),  // 创建赛事
  updateMatchStatus: (matchId, status) => request('/api/matches/' + matchId + '/status', 'PUT', { status: status }),  // 改赛事状态
  approveTeam: (teamId) => request('/api/teams/' + teamId + '/approve', 'POST'),  // 审核通过队伍
  rejectTeam: (teamId) => request('/api/teams/' + teamId + '/reject', 'POST'),  // 驳回队伍

  // ===== 自动编排（管理员） =====
  autoSeeds: (matchId) => request('/api/matches/' + matchId + '/auto-seeds', 'POST'),  // 分配种子
  autoGroup: (matchId, groupNames) => {
    // 后端要多个 group_names query 参数，如 ?group_names=A&group_names=B
    const qs = groupNames.map(g => 'group_names=' + encodeURIComponent(g)).join('&')
    return request('/api/matches/' + matchId + '/auto-group?' + qs, 'POST')
  },  // 蛇形分组
  autoMatches: (matchId, groupName, single) => request('/api/matches/' + matchId + '/auto-matches/' + (single ? 'single' : 'double') + '?group_name=' + encodeURIComponent(groupName), 'POST'),  // 生成对阵
  adminMatchDetail: (matchId) => request('/api/matches/' + matchId + '/admin-detail'),  // 管理后台赛事详情（队伍进度+对阵）
  updateRoundResult: (roundId, data) => request('/api/matches/rounds/' + roundId, 'PUT', data),  // 更新对阵比分
  finishGroupStage: (matchId) => request('/api/matches/' + matchId + '/finish-group', 'POST'),  // 结束小组赛（生成附加赛）
  finishPlayoffStage: (matchId) => request('/api/matches/' + matchId + '/finish-playoff', 'POST'),  // 结束附加赛（第1晋级）
  divideLegend: (matchId) => request('/api/matches/' + matchId + '/divide-legend', 'POST'),  // 传奇组分上下区
  finishLegendStage: (matchId) => request('/api/matches/' + matchId + '/finish-legend', 'POST'),  // 结束传奇组（每区前3晋级）
  generateKnockout: (matchId) => request('/api/matches/' + matchId + '/generate-knockout', 'POST'),  // 生成6强淘汰赛
  advanceKnockout: (matchId) => request('/api/matches/' + matchId + '/advance-knockout', 'POST'),  // 推进淘汰赛（半决赛/决赛）

  // ===== 数据导出（CSV） =====
  exportMatchCsv: (matchId, teamId) => new Promise((resolve, reject) => {
    const token = wx.getStorageSync('token')
    const url = BASE + '/api/matches/' + matchId + '/export' + (teamId ? '?team_id=' + teamId : '')
    wx.downloadFile({
      url: url,
      header: { 'Authorization': token ? 'Bearer ' + token : '' },
      success(res) {
        if (res.statusCode === 200) {
          wx.openDocument({
            filePath: res.tempFilePath,
            fileType: 'csv',
            showMenu: true,
            success: () => resolve(),
            fail: () => reject({ detail: '文件已下载，但设备无法预览 CSV' })
          })
        } else {
          let detail = ''
          try { detail = JSON.parse(res.data).detail || '' } catch (e) {}
          reject({ detail: detail || ('导出失败（' + res.statusCode + '）') })
        }
      },
      fail() {
        reject({ detail: '网络错误，请检查后端服务器是否启动' })
      }
    })
  }),
}