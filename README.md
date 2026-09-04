# 北林 CS2 Major 报名系统

北京林业大学 CS2（Counter-Strike 2）校赛报名小程序 —— 微信小程序 + FastAPI 全栈项目。

> 一个功能完整的电竞校赛报名平台：用户注册登录、队伍组建、赛事报名、赛程自动编排、赛程时间协商、段位体系、在校认证、管理后台。

---

## ✨ 功能特性

### 用户端
- **微信登录**：JWT 令牌认证，支持 mock / 真实微信登录切换
- **段位体系**：D/C/B/A 三小段 + S 段 50 星（金 S / 钻 S / 魔王 S），水平分与段位自动挂钩；AI 识别段位截图自动设段位
- **段位认证必审**：报名与入队均强制要求完成段位认证（上传完美/5E 段位截图，AI 识别），防止高段位炸鱼；前端报名拦截 + 后端接口二次校验 + 报名后入队自动补报名时同步拦截
- **队伍系统**：创建队伍、申请入队、队长审核、邀请/按学号拉人、退队/踢人/解散、人才市场
- **队伍 Logo**：队长可上传/更换自定义队伍头像；无 Logo 时自动用队名首字符生成徽章（按评分分级配色）
- **赛事系统**：赛事浏览、队伍/个人报名、报名审核、状态管理
- **赛程编排**：按队伍水平分配种子 → 蛇形分组 → 双循环/单循环小组赛 → 淘汰赛，全自动生成
- **比分系统**：BO1 大比分 / BO3 三局小分录入，淘汰赛小局弹层查看
- **时间协商**：管理员为各阶段设限定时段 → 双方队长在窗口内提交比赛时间 → 对方同意生效 / 拒绝作废
- **在校认证**：上传学信网截图 → AI 预审（置信度分级自动通过/驳回）→ 人工复核兜底
- **界面（浅色主题）**：白 + 天蓝青配色；左侧抽屉导航替代底部 tabBar；功能拆分为独立页面（消息 / 认证 / 设置 / 个人资料 / 我的队伍 / 所有队伍）

### 管理端（admin / reviewer）
- 用户管理：搜索、设学号、设段位（自动同步水平分）、权限分配
- 队伍审核、赛事管理、报名审核
- **认证审核**：待认证列表、截图预览、AI 预审置信度展示、通过/驳回
- **段位申请审批**：AI 识别段位更新申请，管理员通过（可覆盖 AI 结果）/驳回
- **时间窗口管理**：为各阶段（小组赛/附加赛/传奇组上下区/淘汰赛）设置限定时段
- 赛程一键编排（种子/分组/对阵/晋级推进）

### 技术亮点
- 前后端分离，RESTful API，Pydantic 全量校验
- SQLAlchemy ORM + 数据库自动建表（带字段注释）
- SQLite 开发 / MySQL 生产可切换（已跑通 MySQL）
- 文件上传 + 静态资源服务
- AI 视觉审核：智谱 GLM-5V-Turbo（学信网/校园卡真伪审核 + 完美/5E 段位识别）
- Docker / docker-compose / nginx HTTPS 部署方案

---

## 🛠 技术栈

| 层 | 技术 |
|----|------|
| 前端 | 微信小程序（原生 WXML/WXSS/JS，es6:false） |
| 后端 | Python 3 · FastAPI 0.115 · Uvicorn |
| ORM | SQLAlchemy 2.0.35 |
| 数据库 | MySQL 8（生产）/ SQLite（开发），pymysql 驱动 |
| 认证 | python-jose（JWT HS256）+ passlib（bcrypt） |
| 校验 | Pydantic v2 + pydantic-settings（.env 配置） |
| 部署 | Docker · docker-compose · Nginx（HTTPS） |

---

## 📁 项目结构

```
├── backend/                  # 后端（FastAPI）
│   ├── app/
│   │   ├── main.py           # 入口：建表、CORS、静态文件、挂载路由
│   │   ├── config.py         # 配置（pydantic-settings，读取 .env）
│   │   ├── database.py       # 引擎、会话、get_db 依赖注入
│   │   ├── models/           # ORM 模型（11 张表，含字段注释）
│   │   ├── schemas/          # 请求/响应数据模型（Pydantic）
│   │   ├── services/         # 业务逻辑（登录/队伍/赛事/编排/审核）
│   │   └── routers/          # API 路由（auth/team/match）
│   ├── uploads/              # 上传文件（学信网截图等，git 忽略）
│   ├── requirements.txt
│   └── Dockerfile
├── miniprogram/              # 微信小程序前端
│   ├── pages/                # 首页/赛事/我的队伍/所有队伍/消息/认证/设置/个人资料/登录/管理后台
│   ├── components/           # nav-drawer（左侧抽屉导航）
│   └── utils/                # api.js（接口封装）、rank.js（段位显示）
├── docker-compose.yml        # MySQL + 后端 + Nginx 一键部署
├── nginx.conf                # HTTPS 反向代理模板
├── deploy.md                 # 部署指南
└── project.config.json       # 小程序项目配置
```

---

## 🗄 数据库设计（11 张表）

| 表 | 说明 |
|----|------|
| `users` | 用户：微信标识、学号、认证状态、角色、段位、水平分 |
| `rank_applications` | 段位更新申请：用户再次上传段位截图后，提交管理员审批 |
| `teams` | 队伍：队名(唯一)、队长、审核状态、水平分、Logo |
| `team_members` | 队伍成员：队伍/用户、队内角色（一人一队唯一约束） |
| `team_applications` | 入队申请：用户申请、队长审核 |
| `team_invitations` | 入队邀请：队长邀请、用户同意 |
| `matches` | 赛事：名称、队伍上限、人数、状态 |
| `match_rounds` | 对阵：轮次、双方队伍、比分、胜者、BO3 小分、比赛时间与双方确认状态 |
| `team_progress` | 队伍赛事进度：阶段（挑战者/传奇/淘汰）、种子、分组 |
| `registrations` | 报名：队伍/个人报名、审核状态 |
| `stage_windows` | 阶段时间窗口：管理员为各阶段设置的限定时段 |

---

## 🚀 快速开始

### 1. 启动 MySQL（生产形态）

```bash
# Windows 本地：双击运行 D:\mysql\start_mysql.ps1
# 或 Docker：
docker compose up -d mysql
```

### 2. 配置后端

```bash
cd backend
cp .env.example .env   # 按需修改数据库连接、密钥等
pip install -r requirements.txt
```

### 3. 启动后端

```bash
cd backend
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

启动后自动建表。接口文档：http://127.0.0.1:8000/docs

### 4. 运行小程序

- 用微信开发者工具导入项目**根目录**
- 开发者工具 → 详情 → 本地设置 → 勾选「不校验合法域名」
- 真机调试需把 `miniprogram/utils/api.js` 的 `BASE` 改为电脑局域网 IP

### 5. 测试账号

开发模式（`WX_MOCK_LOGIN=true`）下，任意 code 均可登录；管理员账号见 `.env`。

---

## 🧩 时间协商功能 · 上线须知

> 新增「阶段时间窗口 + 对阵时间协商」后，数据库结构有变更，需手动执行一次迁移。

`stage_windows` 新表会在后端启动时由 `create_all` 自动创建，但 `match_rounds` 的两个新字段**不会自动添加**（SQLAlchemy 的 `create_all` 只建新表、不修改已存在的表），需在 MySQL 中手动执行：

```sql
ALTER TABLE match_rounds
  ADD COLUMN team1_confirmed TINYINT(1) DEFAULT 0 COMMENT '队伍1是否已确认比赛时间',
  ADD COLUMN team2_confirmed TINYINT(1) DEFAULT 0 COMMENT '队伍2是否已确认比赛时间';
```

> 本地（`D:\mysql` 的 `cs2_competition` 库）与生产（docker compose 的 mysql 容器）都要执行一次。

**功能流程**：

1. 管理端赛事详情页点「⏱ 时间窗口」，为各阶段（小组赛/附加赛/传奇组上下区/淘汰赛）设置限定时段；
2. 双方队长在用户端对阵行点时间状态，提交比赛时间（必须落在限定时段内，否则后端拒绝）；
3. 对方队长**同意**则时间生效，**拒绝**则作废重来；改时间需重新双方确认。

**接口**：`PUT /api/matches/{match_id}/stage-windows/{group_name}`、`PUT /api/matches/rounds/{round_id}/schedule`、`POST .../schedule/confirm`、`POST .../schedule/reject`。

---

## 🧭 近期改动（界面重构 + 报名权限加固）

### 界面重构：侧边栏导航 + 独立页面 + 浅色主题
- 移除底部 `tabBar`，改为**左上角头像开左侧抽屉导航**（`components/nav-drawer`），主导航 / 独立页用 `wx.reLaunch` 跳转。
- 把原本复用一个页面内分段 tab 的功能**拆成真正独立的页面**：
  - `messages`：入队邀请 + 段位更新申请（通知列表样式）
  - `verify`：学籍认证 + 段位认证（认证状态 Hero + 参赛必审提示）
  - `settings`：设置项分组的菜单页
  - `profile`：瘦身为纯「个人资料」页（用户卡 + 基础信息/账户分组）
  - `team`：纯「我的队伍」（队伍信息/成员/待审核申请/人才市场/创建）
  - `teams`：**所有队伍**（搜索 + 统计概览 + 卡片式列表 + 队伍详情 roster 弹层，按评分分级配色）
- 统一浅色设计令牌（`app.wxss`）：白 + 天蓝青 `#0891b2`，中性灰阶、柔和投影、圆角卡片。

### 报名权限业务逻辑加固
- **段位认证设为报名硬门槛**（防炸鱼）：`match_service.register_team` / `register_user` 强制要求全队 / 个人已完成段位认证（`user.rank` 非空）；前端 `match.js` 报名前同样拦截并引导到认证页。
- **堵住「报名后拉人绕过段位」漏洞**：报名之后再入队（拉人 / 申请通过 / 接受邀请）会触发自动补报名，原 `_ensure_can_join` 只校验学籍，未校验段位；已改为**学籍 + 段位都必须通过**才能加入已报名的队伍，覆盖 `join_team`、`approve_application`、`accept_invitation` 全部入口。
- **超编报名拦截**：`register_team` 校验队伍人数不得超过该赛事每队人数上限（`match.team_size`）。
- 说明：段位认证是「**必须完成**」而非「必须达到某段位」——人人都上传段位截图，但不卡分值，避免赶走普通玩家。

### 队伍自定义 Logo
- `teams` 表新增 `logo` 字段；队长通过 `POST /api/teams/{team_id}/logo` 上传（仅图片、≤5MB），网段路径存入 `logo`。
- 上传入口收敛到 `services/upload_service.py`，统一「类型校验 + 大小校验 + 落盘」逻辑，避免各上传接口重复实现。
- 展示：所有队伍列表卡片 / 队伍详情 roster / 我的队伍卡片优先显示 Logo，无 Logo 回退为首字徽章。
- **迁移（上线必做）**：`teams` 表的新列不会由 `create_all` 自动添加（它只建新表），需在 MySQL 手动执行：
  ```sql
  ALTER TABLE teams ADD COLUMN logo VARCHAR(255) NULL COMMENT '队伍Logo图片路径';
  ```
  本地（`D:\mysql` 的 `cs2_competition`）与生产（docker compose 的 mysql 容器）各执行一次。

---

## ☁️ 部署上线

详见 [`deploy.md`](./deploy.md)：云服务器 + Docker + Nginx HTTPS + 微信小程序真实登录配置。

---

## � 未来规划

- **排行榜**：用户端按水平分/段位排名
- **微信真实登录**：注册小程序后对接（代码已就绪，切换 `WX_MOCK_LOGIN=false` 即可）
- **部署上线**：云服务器 + HTTPS（见 `deploy.md`）

---

## �📄 License

仅供学习交流使用。
