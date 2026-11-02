# 北林 Major 赛事报名系统与 Agent 助手

面向北京林业大学 CS2 校赛的微信小程序，使用原生小程序前端和 FastAPI 后端，提供组队报名、赛事管理、对阵比分、选手统计和即时通信，并集成 Agent 助手与视觉辅助审核。

## 主要功能

| 模块 | 功能 |
| --- | --- |
| 报名与组队 | 个人／队伍报名、创建队伍、申请与邀请入队、队伍招募、自由选手筛选 |
| 赛事与数据 | 赛程编排、分组与晋级、BO3 比分、管理员统一排期、选手统计与名人堂 |
| 身份与段位 | 在校与游戏段位认证、截图上传、视觉模型辅助识别、驳回原因查询与人工复核 |
| Agent 助手 | 自然语言查询赛事、对阵比分及选手数据；报名资格诊断、认证引导、招募推荐与联系入口 |
| 消息与通知 | 站内提醒、WebSocket 私聊、微信一次性订阅通知与使用后的补订阅提示 |
| 管理后台 | 用户与角色管理、认证和报名审核、赛程与比分维护、定向通知和操作审计 |

### Agent 的工作方式

- 使用 LangChain `create_agent`（由 LangGraph 运行）调度模型与业务工具，查询实时状态并展示文字与操作卡片。
- 报名先生成预览，由用户确认后提交；后端重新检查权限、资格和名额，使用事务与幂等记录处理重复请求。
- 查询队伍当前晋级进度与整场战绩，兼容赛事年份简称；同阶段并列，累计胜场与晋级名次分开。
- 检索版本化赛事规则与公开支持入口，支持多轮会话和可选滚动摘要。当前知识库使用赛事／版本／生效时间过滤与关键词检索。
- 认证助手解释本人状态和驳回原因，并引导进入认证页面；审核仍由原认证流程处理。
- 权限、事务、会话摘要和报名确认由现有后端管理；可用 `ASSISTANT_ENGINE=legacy` 显式切回原执行循环，无自动降级。

选手统计来自受控导入的本站赛事数据。AI、Agent 和微信订阅通知均需单独配置；拒绝订阅不会阻止报名，站内提醒保留。

## 技术栈

| 层次 | 技术 |
| --- | --- |
| 前端 | 微信原生小程序 · JavaScript / WXML / WXSS |
| 后端 | Python 3.12 · FastAPI · SQLAlchemy · Pydantic |
| 数据与认证 | MySQL 8 / SQLite · JWT |
| AI 接入 | LangChain / LangGraph · GLM 兼容接口 · Tool Calling · 视觉辅助审核 |
| 通信与部署 | WebSocket · Docker Compose · Nginx / HTTPS |

## 项目结构

```text
BJFU-MAJOR/
├── backend/
│   ├── app/
│   │   ├── routers/          # API 路由与鉴权入口
│   │   ├── services/         # 赛事、Agent、通知及审核逻辑
│   │   ├── models/           # 数据模型
│   │   ├── schemas/          # 请求与响应校验
│   │   └── data/             # 规则知识库等基础资料
│   ├── tests/                # 后端及小程序回归测试
│   ├── .env.example          # 配置示例
│   └── Dockerfile
├── miniprogram/              # 小程序页面、组件及接口封装
├── docs/                    # 功能设计文档
├── tools/                   # 可复用开发工具
├── docker-compose.yml
├── nginx.conf
└── project.config.json      # 微信开发者工具项目配置
```

## 拉取与本地运行

### 1. 获取代码并安装基础依赖

准备 Git、Python 3.12 和微信开发者工具。以下命令使用 Windows PowerShell，依赖安装在项目虚拟环境中。

```powershell
git clone https://github.com/keill0323/BJFU-MAJOR.git
cd BJFU-MAJOR
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r .\backend\requirements.txt -r .\backend\requirements-assistant.txt
if (!(Test-Path .\backend\.env)) {
    Copy-Item .\backend\.env.example .\backend\.env
}
```

已有仓库可在处理好本地改动后使用 `git pull --ff-only` 更新。Linux / macOS 将虚拟环境解释器路径换为 `.venv/bin/python`。

### 2. 配置本地开发环境

编辑 `backend/.env`。示例文件默认包含 MySQL 配置，本地快速联调可改为 SQLite，并关闭需要外部服务的功能：

```dotenv
DATABASE_URL=sqlite:///./local.db
SECRET_KEY=replace-with-your-own-random-secret
WX_MOCK_LOGIN=true
AI_REVIEW_ENABLED=false
ASSISTANT_ENABLED=false
ASSISTANT_ENGINE=langchain
WX_SUBSCRIBE_ENABLED=false
```

`SECRET_KEY` 请替换为自行生成的随机值。上述配置仅用于本地开发，基础联调无需模型密钥或本地 MySQL；模拟微信登录不可用于公开部署。视觉审核使用后端配置的视觉模型接口，本次代码不包含本地 YOLO/OCR 运行链。

### 3. 启动后端

必须从 `backend` 目录启动，使程序读取该目录下的 `.env`：

```powershell
cd backend
..\.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

- 接口文档：<http://127.0.0.1:8000/docs>
- 赛事列表：<http://127.0.0.1:8000/api/matches>，空库返回 `[]`。

首次启动会创建数据库和当前模型对应的表。这是空白开发环境，不包含正式赛事、用户资料或预置管理员账号；已有数据库升级需按部署文档核对迁移。

### 4. 连接微信小程序

1. 在微信开发者工具中导入仓库根目录，并填写自己的 AppID；`project.config.json` 已配置 `miniprogram/` 为小程序目录。
2. 将 `miniprogram/utils/api.js` 中的 `BASE` 改为 `http://127.0.0.1:8000`，避免本地前端请求正式服务。
3. 本地调试可在开发者工具中关闭合法域名校验，然后编译运行。
4. 真机调试时改用电脑的局域网 IP，确保手机和电脑能互相访问；手机上的 `127.0.0.1` 指向手机自身。

正式使用需配置 HTTPS 合法域名、真实微信登录和相应服务凭据。视觉审核、Agent 与微信订阅通知需分别配置；未配置 AI 时仍可走原站内流程及人工审核。

## 部署与测试

云端部署使用 Docker Compose 管理 MySQL、后端和 Nginx。构建前需核对服务配置、证书和数据库迁移，具体步骤见[部署与配置指南](deploy.md)。GitHub 推送、后端部署与小程序上传发布分别进行。

回归测试覆盖业务权限、并发报名、Agent 工具与上下文、通知和小程序交互，运行命令见[测试说明](backend/tests/README.md)。本地模拟测试与真实微信、模型接口及真机验收分别记录。

## 更多文档

- [部署与配置指南](deploy.md)
- [回归测试说明](backend/tests/README.md)
- [名人堂功能设计](docs/design/名人堂功能设计.md)
- [项目日志与开发记录](项目总结与踩坑记录.md)

## 使用说明

仅供学习交流使用。请勿提交 `.env`、密钥、用户认证截图、数据库或本地模型权重。
