# 北林 CS2 Major 报名系统

北京林业大学 CS2（Counter-Strike 2）校赛报名小程序 —— 微信小程序 + FastAPI 全栈项目。

> 一个功能完整的电竞校赛报名平台：用户注册登录、队伍组建、赛事报名、赛程自动编排、段位体系、在校认证、管理后台。

---

## ✨ 功能特性

### 用户端
- **微信登录**：JWT 令牌认证，支持 mock / 真实微信登录切换
- **段位体系**：D/C/B/A 三小段 + S 段 50 星（金 S / 钻 S / 魔王 S），水平分与段位自动挂钩
- **队伍系统**：创建队伍、申请入队、队长审核、邀请/按学号拉人、退队/踢人/解散、人才市场
- **赛事系统**：赛事浏览、队伍/个人报名、报名审核、状态管理
- **赛程编排**：按队伍水平分配种子 → 蛇形分组 → 双循环/单循环小组赛 → 淘汰赛，全自动生成
- **比分系统**：BO1 大比分 / BO3 三局小分录入，淘汰赛小局弹层查看
- **在校认证**：上传学信网截图 → 管理员人工审核 → 认证状态展示

### 管理端（admin / reviewer）
- 用户管理：搜索、设学号、设段位（自动同步水平分）、权限分配
- 队伍审核、赛事管理、报名审核
- **认证审核**：待认证列表、截图预览、通过/驳回
- 赛程一键编排（种子/分组/对阵/晋级推进）

### 技术亮点
- 前后端分离，RESTful API，Pydantic 全量校验
- SQLAlchemy ORM + 数据库自动建表（带字段注释）
- SQLite 开发 / MySQL 生产可切换（已跑通 MySQL）
- 文件上传 + 静态资源服务
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
│   │   ├── models/           # ORM 模型（9 张表，含字段注释）
│   │   ├── schemas/          # 请求/响应数据模型（Pydantic）
│   │   ├── services/         # 业务逻辑（登录/队伍/赛事/编排/审核）
│   │   └── routers/          # API 路由（auth/team/match）
│   ├── uploads/              # 上传文件（学信网截图等，git 忽略）
│   ├── requirements.txt
│   └── Dockerfile
├── miniprogram/              # 微信小程序前端
│   ├── pages/                # 登录/首页/赛事/队伍/个人中心/管理后台
│   └── utils/                # api.js（接口封装）、rank.js（段位显示）
├── docker-compose.yml        # MySQL + 后端 + Nginx 一键部署
├── nginx.conf                # HTTPS 反向代理模板
├── deploy.md                 # 部署指南
└── project.config.json       # 小程序项目配置
```

---

## 🗄 数据库设计（9 张表）

| 表 | 说明 |
|----|------|
| `users` | 用户：微信标识、学号、认证状态、角色、段位、水平分 |
| `teams` | 队伍：队名(唯一)、队长、审核状态、水平分 |
| `team_members` | 队伍成员：队伍/用户、队内角色（一人一队唯一约束） |
| `team_applications` | 入队申请：用户申请、队长审核 |
| `team_invitations` | 入队邀请：队长邀请、用户同意 |
| `matches` | 赛事：名称、队伍上限、人数、状态 |
| `match_rounds` | 对阵：轮次、双方队伍、比分、胜者、BO3 小分 |
| `team_progress` | 队伍赛事进度：阶段（挑战者/传奇/淘汰）、种子、分组 |
| `registrations` | 报名：队伍/个人报名、审核状态 |

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

## ☁️ 部署上线

详见 [`deploy.md`](./deploy.md)：云服务器 + Docker + Nginx HTTPS + 微信小程序真实登录配置。

---

## � 未来规划

- **AI 截图自动审核**：接入多模态大模型（Qwen-VL / GLM-4V 等），自动识别学信网/校园卡截图真伪，置信度分级 + 人工复核兜底（规划中，需申请大模型 API Key 后实现）
- **排行榜**：用户端按水平分/段位排名
- **微信真实登录**：注册小程序后对接（代码已就绪）
- **部署上线**：云服务器 + HTTPS（见 `deploy.md`）

---

## �📄 License

仅供学习交流使用。
