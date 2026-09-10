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
 * 后端地址说明（按场景切换，只保留一行有效）：
 *  - 正式上线：https://bjfumajor.com（当前使用）
 *  - 临时测试（真机调试）：http://192.144.191.253（不需要备案）
 *  - 本地开发：http://127.0.0.1:8000 或电脑局域网 IP
 */
const BASE = 'https://bjfumajor.com'   // 正式：HTTPS 域名（备案 + 证书生效后可用）
// const BASE = 'http://192.144.191.253'   // 临时测试：公网 IP（真机调试用）
// const BASE = 'http://192.168.16.170:8000'   // 本地开发：局域网 IP

/**
 * 递归把返回数据里的 role 字段转小写
 * 原因：后端 SQLAlchemy 枚举存的是大写名（CAPTAIN/ADMIN），前端判断统一用小写
 */
function normalizeRole(data) {
  if (Array.isArray(data)) {
    return data.map(normalizeRole)
  }
  if (data && typeof data === 'object') {
    const out = {}
    for (const key in data) {
      const val = data[key]
      if (key === 'role' && typeof val === 'string') {
        out[key] = val.toLowerCase()
      } else if (val && typeof val === 'object') {
        out[key] = normalizeRole(val)
      } else {
        out[key] = val
      }
    }
    return out
  }
  return data
}

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
          resolve(normalizeRole(res.data))      // 2xx/3xx：成功，返回数据（role 统一转小写）
        } else {
          // 401 = 未登录或令牌过期：清 token，但不强制跳登录页
          // （让用户先浏览内容，只有主动操作时才引导登录，符合登录规范）
          if (res.statusCode === 401) {
            wx.removeStorageSync('token')
            reject({ detail: '请先登录', unauthorized: true, statusCode: res.statusCode })
            return
          }
          // 统一错误信息：422 校验错误的 detail 是数组，转成可读字符串
          let detail = res.data && res.data.detail
          if (Array.isArray(detail)) {
            detail = detail.map(d => d.msg || JSON.stringify(d)).join('；')
          }
          reject({ detail: detail || ('请求失败（' + res.statusCode + '）'), statusCode: res.statusCode })
        }
      },
      fail() {
        reject({ detail: '网络错误，请检查后端服务器是否启动' })  // 网络层失败
      }
    })
  })
}


// 整队审核要求后端同时验证阵容、批量通过报名并创建赛事进度。
// 旧服务器只有单条报名审核接口，不能回退调用，否则会绕过这些约束。
function backendVersionError(err, feature) {
  // FastAPI 未匹配到路由的默认 404；业务 404（如队伍未报名）保留原文。
  if (err.statusCode === 404 && err.detail === 'Not Found') {
    throw {
      detail: '当前服务器版本不支持' + feature + '，请更新后端后重试',
      statusCode: err.statusCode,
      code: 'BACKEND_UPGRADE_REQUIRED'
    }
  }
  throw err
}

function approveTeamRegistration(matchId, teamId) {
  return request('/api/matches/' + matchId + '/registrations/approve-team?team_id=' + teamId, 'POST')
    .catch(err => backendVersionError(err, '整队报名审核'))
}


/**
 * 通用文件上传函数（multipart）
 * @param path     上传接口路径，如 '/api/teams/12/logo'
 * @param filePath 本地临时文件路径（wx.chooseMedia 返回的 tempFilePath）
 * @param raw     是否原样返回后端 JSON（true 不归一化 role）——默认 true
 * @returns Promise，成功 resolve 后端返回数据，失败 reject 错误信息
 */
function uploadFile(path, filePath, raw = true) {
  const token = wx.getStorageSync('token')
  return new Promise((resolve, reject) => {
    wx.uploadFile({
      url: BASE + path,
      filePath: filePath,
      name: 'file',
      header: { 'Authorization': token ? 'Bearer ' + token : '' },
      success(res) {
        if (res.statusCode < 400) {
          try {
            resolve(raw ? JSON.parse(res.data) : normalizeRole(JSON.parse(res.data)))
          } catch (e) {
            reject({ detail: '上传响应解析失败' })
          }
        } else {
          let detail = ''
          try { detail = JSON.parse(res.data).detail || '' } catch (e) {}
          if (res.statusCode === 401) {
            // 与 request() 保持一致：只清 token，不强制跳登录页
            wx.removeStorageSync('token')
            reject({ detail: '请先登录', unauthorized: true })
            return
          }
          reject({ detail: detail || ('上传失败（' + res.statusCode + '）') })
        }
      },
      fail(err) {
        console.error('上传失败详情:', err)
        reject({ detail: '网络错误：' + (err.errMsg || '请检查后端服务器是否启动') })
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

  // 名人堂公开榜单，分页按服务端顺序展示。
  getHallChampions: (offset = 0, limit = 20) => request('/api/hall/champions?offset=' + offset + '&limit=' + limit)
    .catch(err => backendVersionError(err, '名人堂')),
  getHallPlayers: (offset = 0, limit = 50) => request('/api/hall/players?offset=' + offset + '&limit=' + limit)
    .catch(err => backendVersionError(err, '名人堂')),
  getAdminChampion: matchId => request('/api/hall/admin/champions/' + matchId)
    .catch(err => backendVersionError(err, '冠军补录')),
  saveAdminChampion: (matchId, data) => request('/api/hall/admin/champions/' + matchId, 'PUT', data)
    .catch(err => backendVersionError(err, '冠军补录')),
  getAdminTodos: () => request('/api/admin/todos')
    .catch(err => backendVersionError(err, '管理待办提醒')),

  // ===== 认证 =====
  login: (code) => request('/api/auth/login', 'POST', { code }),          // 登录，code 是微信登录凭证
  getMe: () => request('/api/auth/me'),                                    // 获取当前用户完整信息
  updateProfile: (data) => request('/api/auth/profile', 'PUT', data),     // 修改昵称/游戏ID

  // 上传学信网截图（multipart，走共享 uploadFile 函数）
  uploadVerify: (filePath) => uploadFile('/api/auth/upload-verify', filePath, false),

  // 上传游戏段位截图（完美/5E平台，AI 识别段位）
  uploadRank: (filePath) => uploadFile('/api/auth/upload-rank', filePath, true),

  // 上传头像
  uploadAvatar: (filePath) => uploadFile('/api/auth/upload-avatar', filePath, false),

  // ===== 队伍 =====
  createTeam: (name) => request('/api/teams', 'POST', { name }),          // 创建队伍
  getTeams: () => request('/api/teams'),                                  // 队伍列表
  getAdminTeams: () => request('/api/teams/admin'),                        // 全部队伍（需管理权限）
  getTeam: (id) => request('/api/teams/' + id),                           // 查某支队伍详情
  getMyTeam: () => request('/api/teams/my'),                              // 查我的队伍（按 token）
  getTalentMarket: (matchId) => request('/api/teams/talent-market?match_id=' + matchId),  // 人才市场：某赛事的自由人
  recruitByStudent: (teamId, studentId) => request('/api/teams/recruit', 'POST', { team_id: teamId, student_id: studentId }),  // 队长按学号拉人入队
  joinTeam: (teamId, userId) => request('/api/teams/join', 'POST', { team_id: teamId, user_id: userId }),  // 队长拉人
  leaveTeam: (teamId) => request('/api/teams/' + teamId + '/leave', 'DELETE'),  // 主动退队
  kickMember: (teamId, userId) => request('/api/teams/' + teamId + '/members/' + userId, 'DELETE'),  // 队长踢人
  disbandTeam: (teamId) => request('/api/teams/' + teamId, 'DELETE'),     // 解散队伍
  deleteTeam: (teamId) => request('/api/teams/admin/' + teamId, 'DELETE'), // 管理员删队伍

  // ===== 入队申请 =====
  applyJoin: (teamId, message) => request('/api/teams/' + teamId + '/apply', 'POST', { team_id: teamId, message: message || '' }),  // 申请加入
  getApplications: (teamId) => request('/api/teams/' + teamId + '/applications'),  // 队长看申请
  approveApplication: (appId) => request('/api/teams/applications/' + appId + '/approve', 'POST'),  // 同意申请
  rejectApplication: (appId) => request('/api/teams/applications/' + appId + '/reject', 'POST'),  // 拒绝申请

  // 队长上传/更换队伍Logo（multipart）
  uploadTeamLogo: (teamId, filePath) => uploadFile('/api/teams/' + teamId + '/logo', filePath, false),

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
  approveTeamRegistration: approveTeamRegistration,  // 管理端：通过某队伍全部报名记录

  // ===== 管理后台（admin/reviewer） =====
  adminListUsers: (keyword) => request('/api/auth/admin/users' + (keyword ? '?keyword=' + keyword : '')),  // 用户列表/搜索
  getVerifyList: () => request('/api/auth/admin/verify-list'),  // 待认证审核列表（已上传截图未通过）
  getRankApplications: () => request('/api/auth/admin/rank-applications'),  // 段位更新申请列表
  getMyRankApplications: () => request('/api/auth/my-rank-applications'),  // 我的段位申请（含驳回原因）
  approveRankApplication: (appId, rank) => request('/api/auth/admin/rank-applications/' + appId + '/approve' + (rank ? '?rank=' + encodeURIComponent(rank) : ''), 'POST'),  // 通过段位更新申请（可指定段位）
  rejectRankApplication: (appId, reason) => request('/api/auth/admin/rank-applications/' + appId + '/reject' + (reason ? '?reject_reason=' + encodeURIComponent(reason) : ''), 'POST'),  // 驳回段位更新申请（可填原因）
  adminUpdateUser: (userId, data) => request('/api/auth/admin/users/' + userId, 'PUT', data),  // 改学号/段位/评分/认证
  adminUpdateRole: (userId, role) => request('/api/auth/admin/users/' + userId + '/role', 'PUT', { role: role }),  // 改角色
  createMatch: (data) => request('/api/matches', 'POST', data),  // 创建赛事
  updateRegistrationWindow: (matchId, data) => request('/api/matches/' + matchId + '/registration-window', 'PUT', data)
    .catch(err => backendVersionError(err, '报名时间设置')),
  updateMatchStatus: (matchId, status) => request('/api/matches/' + matchId + '/status', 'PUT', { status: status }),  // 改赛事状态
  approveTeam: (teamId) => request('/api/teams/' + teamId + '/approve', 'POST'),  // 审核通过队伍
  rejectTeam: (teamId) => request('/api/teams/' + teamId + '/reject', 'POST'),  // 驳回队伍

  // ===== 自动编排（管理员） =====
  autoSeeds: (matchId) => request('/api/matches/' + matchId + '/auto-seeds', 'POST'),  // 分配种子
  seedAndGroup: (matchId, groupNames) => {
    const qs = groupNames.map(g => 'group_names=' + encodeURIComponent(g)).join('&')
    return request('/api/matches/' + matchId + '/seed-and-group?' + qs, 'POST')
      .catch(err => backendVersionError(err, '一次完成种子分配与分组'))
  },
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

  // ===== 阶段时间窗口 + 对阵时间协商 =====
  // 管理员：设置某阶段（group_name）的限定时段
  setStageWindow: (matchId, groupName, data) => request('/api/matches/' + matchId + '/stage-windows/' + encodeURIComponent(groupName), 'PUT', data),
  // 管理员：查某赛事所有阶段窗口
  getStageWindows: (matchId) => request('/api/matches/' + matchId + '/stage-windows'),
  // 队长：提交/修改约定比赛时间（scheduledTime 传 ISO 字符串，如 '2026-09-03T19:00:00'）
  scheduleRound: (roundId, scheduledTime) => request('/api/matches/rounds/' + roundId + '/schedule', 'PUT', { scheduled_time: scheduledTime }),
  // 队长：确认约定时间（expectedTime 传客户端看到的提议时间，后端校验是否已被对方修改）
  confirmRoundSchedule: (roundId, expectedTime) => request('/api/matches/rounds/' + roundId + '/schedule/confirm', 'POST', { expected_time: expectedTime }),
  // 队长：拒绝/作废约定时间
  rejectRoundSchedule: (roundId, expectedTime) => request('/api/matches/rounds/' + roundId + '/schedule/reject', 'POST', { expected_time: expectedTime }),
}
