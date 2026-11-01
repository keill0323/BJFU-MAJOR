# 北林 CS2 Major · 上线部署指南

项目变更与发布状态统一记录在[项目日志](项目总结与踩坑记录.md)。发布包及逐次部署记录保存在仓库外或被忽略的 `artifacts/deploy/`；不要复用旧发布包，始终从待发布代码重新打包。

> 目标：把 FastAPI 后端部署到云服务器（MySQL + Nginx + HTTPS），小程序正式发布。
> 前置：已注册微信小程序、已买国内云服务器、已备案域名（约 1-2 周）。

---

## 一、准备清单

| 项 | 说明 |
|----|------|
| 云服务器 | 腾讯云/阿里云**轻量应用服务器**，2核2G+，系统选 Ubuntu 22.04 / CentOS |
| 域名 | 已 ICP 备案的域名（**国内服务器必须备案**，小程序才允许请求） |
| 小程序 | 已注册小程序，拿到 **AppID** 和 **AppSecret**（后台→开发→开发设置） |
| 工具 | 本地装 Docker、Docker Compose（或用宝塔面板） |

---

## 二、服务器初始化

```bash
# 1. 安装 Docker 与 Compose
curl -fsSL https://get.docker.com | sh
sudo systemctl enable --now docker

# 2. 开放防火墙端口（安全组）
# 控制台放行 80、443；3306 不要对外开放（仅内网）
```

---

## 三、上传项目

```bash
# 服务器上
mkdir -p /opt/cs2 && cd /opt/cs2
# 用 git 或 scp 把项目传上来（含 backend/、docker-compose.yml、nginx.conf）
```

> ⚠️ 不要上传 `backend/cs2_competition.db`（那是本地 SQLite 数据）、`.env`（本地配置）、`node_modules`。

---

## 四、配置环境变量

```bash
# 复制模板生成 .env（docker-compose 会读取）
cp backend/.env.example .env
nano .env
```

必改项：
```bash
# MySQL 连接（密码要和下面 compose 里一致）
DATABASE_URL=mysql+pymysql://cs2_user:你的密码@mysql:3306/cs2_competition?charset=utf8mb4
SECRET_KEY=python -c "import secrets; print(secrets.token_hex(32))"  # 生成后填这里
ADMIN_PASSWORD=你的强密码
CORS_ORIGINS=https://你的域名
WX_APPID=你的AppID
WX_SECRET=你的AppSecret
WX_MOCK_LOGIN=false          # 关键：切真实微信登录
```

> 首次启动 MySQL 会自动建库 `cs2_competition`，后端启动时自动建表。**已有数据库更新版本时，`create_all` 不会补字段，必须执行下方升级流程。**

---

## 五、启动服务

```bash
# 构建并启动（-d 后台运行）
docker compose up -d --build

# 查看状态与日志
docker compose ps
docker compose logs -f backend
```

验证：`curl http://127.0.0.1:8000/api/matches` 应返回 JSON。

### 已有服务器更新代码与数据库

先将当前代码同步到服务器既有项目目录，保留服务器 `.env` 和数据卷，再在该目录执行以下命令。冠军补录需要升级 `champion_snapshots`，认证学号识别需要增加 `users.ai_student_id`，队长 QQ 需要增加 `teams.captain_qq`；迁移均可重复执行。仅重建/重启容器不会给旧表补列，可能导致录分、登录或队伍接口报错。

当前版本已移除建队 QQ 收集和强制填写。线上 2026-09-12 部署过的版本仍有此限制，必须先更新后端再发布新的小程序；定向站内通知的新表迁移与发布步骤见第十二节。历史发布过程统一保留在项目日志。

```bash
(
set -eu
docker compose build backend
docker compose stop backend

# 停止 API 写入后备份；任何一步失败都会终止本次流程。
upgrade_backup="before-schema-upgrade-$(date +%Y%m%d-%H%M%S).sql"
docker compose exec -T mysql sh -c 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysqldump -uroot --single-transaction --routines --triggers cs2_competition' > "$upgrade_backup"

docker compose run --rm --no-deps backend python -m app.migrations.manual_champions
docker compose run --rm --no-deps backend python -m app.migrations.verification_student_id
docker compose run --rm --no-deps backend python -m app.migrations.test_data_visibility
docker compose run --rm --no-deps backend python -m app.migrations.schedule_notifications
docker compose run --rm --no-deps backend python -m app.migrations.recruitment_notifications
docker compose run --rm --no-deps backend python -m app.migrations.team_request_invalidation
docker compose run --rm --no-deps backend python -m app.migrations.team_captain_qq
docker compose up -d --no-deps backend
docker compose restart nginx
docker compose ps backend
docker compose logs --tail=80 backend
)
```

MySQL 结构变更不能整段回滚；迁移失败时先查看具体错误，不要继续启动新后端。完成后确认冠军列表正常，并在管理后台重新提交之前失败的 BO3 比分。非 Docker 环境在实际服务虚拟环境的 `backend` 目录执行上面的各项 `python -m` 命令，再重启服务。

约赛消息功能新增 `schedule_notifications` 表，对应迁移显式创建该表；现有启动建表流程也会创建不存在的新表。该功能还需上传对应小程序，详见[约赛消息通知](项目总结与踩坑记录.md)。申请和邀请还需执行 `team_request_invalidation` 补充失效标记，详见下方升级说明。

队长 QQ 收集已停用：创建队伍不再要求或保存 `captain_qq`；旧客户端多传的字段会被忽略，所有队伍响应均不返回历史 QQ。旧 `PUT /api/teams/{team_id}/contact` 在校验队长权限后返回 410，不修改资料。原可空列与迁移保留用于已有数据库兼容，本次不删除历史数据。新版前端移除输入、展示、复制和催填入口；应先部署后端取消必填校验，再发布新版小程序。

---

## 六、配置 HTTPS（Let's Encrypt 免费证书）

```bash
# 1. 先改 nginx.conf 里的 server_name 为你的域名
# 2. 安装 certbot
sudo apt install -y certbot

# 3. 签发证书（先停 nginx 的 443 或临时只留 80）
sudo certbot certonly --webroot -w ./certbot/www -d 你的域名

# 4. 证书生成后，docker compose restart nginx
docker compose restart nginx
```

> 证书续期（每 90 天）：`sudo certbot renew`（可加 crontab 自动续期）。

---

## 七、小程序端配置

1. **改后端地址**：`miniprogram/utils/api.js` 的 `BASE` → `https://你的域名`
2. **配置合法域名**：小程序后台 → 开发 → 开发设置 → **服务器域名** → request 合法域名添加 `https://你的域名`
3. **检查登录**：`WX_MOCK_LOGIN=false` 后，后端会用微信 code 换真实 openid，登录走真实流程
4. **上传发布**：开发者工具 → 上传 → 后台版本管理 → 提交审核 → 发布

---

## 八、上线前安全检查清单

- [ ] `SECRET_KEY`、`ADMIN_PASSWORD` 已替换（不是默认值）
- [ ] `CORS_ORIGINS` 限定为你的域名（不是 `*`）
- [ ] `DB_ECHO=False`（不打印 SQL）
- [ ] `WX_MOCK_LOGIN=false`（真实微信登录）
- [ ] 数据库已切 MySQL（`DATABASE_URL` 指向 mysql）
- [ ] 3306 端口未对公网开放

---

## 九、常见问题

| 问题 | 处理 |
|------|------|
| 小程序请求报"不在合法域名列表" | 后台服务器域名没配 / 域名没备案 / 不是 HTTPS |
| 登录失败 | 检查 WX_APPID/WX_SECRET、`WX_MOCK_LOGIN`、域名已备案 |
| 后端 502 | `docker compose logs backend` 看报错，一般是数据库连接或 .env 问题 |
| 淘汰赛 BO3 录分 500、冠军列表也失败；或提示“冠军档案数据库尚未升级” | 核对日志是否有 `champion_snapshots` 缺列/缺表，按上方流程执行 `python -m app.migrations.manual_champions`；仅重启不能补列。详见[BO3 录分排查](项目总结与踩坑记录.md) |
| 中文乱码 | MySQL 连接串必须带 `?charset=utf8mb4` |
| 数据备份 | `docker compose exec mysql mysqldump -ucs2_user -p cs2_competition > backup.sql` |

---

## 十、备份建议

```bash
# 每天凌晨备份（crontab）
0 3 * * * docker compose -f /opt/cs2/docker-compose.yml exec -T mysql mysqldump -ucs2_user -p你的密码 cs2_competition > /opt/cs2/backup/$(date +\%F).sql
```


## 十一、招募大厅、主页消息与微信订阅配置

招募大厅默认展示“全部选手”：所有非隐藏用户均可查看，包含未报名、已入队和待认证用户；支持昵称/游戏 ID 搜索及分页，队长可邀请无队伍的选手。新生、老生直接显示，尚未认证或身份不明的显示待确认；列表不返回或搜索学号。“队伍招募”保留为独立页签。招募大厅不要求先报名赛事。队伍审核通过后，队长可发布、编辑或关闭一条招募；阵容齐备后由队长关闭。普通选手浏览招募后可提交入队申请，选填最多 200 字自我介绍。尚未结束的赛事继续按人数上限和认证条件补员；分组锁定后不能新增参赛队伍，已有参赛队伍沿用原补员规则。已结束赛事不再约束当前阵容，新成员也不会被补进历史报名。主页显示当前账号收到的待处理邀请、队长待处理申请及未读约赛通知；返回首页或下拉刷新重新获取数量。

申请卡片和入队后的队员资料均显示新生/老生/身份待确认。队长在消息页和队伍页看到申请人的头像、昵称、游戏 ID、段位、水平分、学籍认证状态、新老生身份、个人介绍和本次入队自我介绍。接口采用字段白名单，**不返回学号、AI 候选学号、认证截图或微信 OpenID**；本人和具备权限的管理审核入口继续按既有规则查看资料，按学号精确查找保留。

全部选手支持段位、身份与昵称/游戏 ID 搜索组合筛选，条件在服务端分页前执行。身份可选新生、老生或待确认，沿用已认证且人工身份优先的规则；未认证或无法判定者归待确认。段位包含 D 至 A++ 的各档、普通 S（旧 S / 0–9 星）、金 S（10–24 星）、钻 S（25–49 星）、魔王 S（50 星）及待认证。段位与学籍是独立条件，已有队伍或未报名的用户仍可筛选。

筛选更新无需数据库迁移，但需先更新后端 `recruitment_service.py` 和 `routers/recruitment.py`，再发布对应小程序。`GET /api/recruitment/players` 新增 `rank`、`identity` 参数并回显 `filters`；旧小程序不传新参数时保持原行为。新版小程序检测不到筛选回显会明确提示更新后端，不会将全量选手误显示成筛选结果。带加号段位需正确 URL 编码，例如 `?rank=A%2B&identity=new_student`。

### 后端升级

同步对应源码与新版 compose 文件后，在服务器应用目录执行（先构建新镜像，再执行迁移）：

```bash
docker compose build backend
docker compose stop backend
docker compose run --rm --no-deps -T backend python -m app.migrations.schedule_notifications
docker compose run --rm --no-deps -T backend python -m app.migrations.recruitment_notifications
docker compose run --rm --no-deps -T backend python -m app.migrations.team_request_invalidation
docker compose up -d --no-deps --force-recreate backend
docker compose exec -T nginx nginx -s reload
```

升级前备份数据库和原镜像；停止旧后端后执行迁移，避免清理期间旧代码继续产生失效申请。迁移添加 `recruitment_posts`、`wechat_subscriptions`、`wechat_outbox` 等新表，并给申请、邀请表补充可空的 `invalidated_at`，可重复执行。申请迁移仅将当前已入队用户的待处理记录标为失效，不删除历史、不将其改成队长拒绝、不修改成员或赛事。

加入或创建队伍后，其余待处理申请和邀请自动失效；退队、踢出或解散后旧请求不会恢复，需要重新申请。队伍申请列表、消息页、主页计数及微信发送共用有效性判断。只有本次申请失效修复时，升级后端并刷新页面即可；招募大厅等新界面仍需重新上传、审核并发布小程序。后端升级不会自动发布小程序。

### 微信公众平台需选用的模板

在小程序后台「订阅消息」中选用与你的小程序服务类目匹配的模板，拿到实际模板 ID 和字段列表。优先覆盖：

| 事件配置键 | 收到提醒的人 | 建议模板内容 | 点击后打开 |
|---|---|---|---|
| `team_invitation` | 被邀请的选手 | 队伍名称、邀请人、入队邀请、时间 | 消息页 |
| `team_application` | 队长 | 队伍名称、申请人、入队申请、时间 | 我的队伍 |

若平台允许同一个模板表达这些业务，多个事件可配置相同 ID；前端会展示全部用途，按模板 ID 去重后发起订阅。同一模板的多个用途共享微信订阅次数，一次授权不是每种用途各获得一次通知。字段必须与实际所选模板完全一致，以下键仅是配置结构示例，不是平台提供的固定字段或真实 ID。

### 配置项

Docker Compose 部署时修改 `/home/ubuntu/app/.env`（与 compose 同目录）；直接运行后端时修改 `backend/.env`：

| 配置项 | 值/含义 |
|---|---|
| `WX_APPID` / `WX_SECRET` | 当前小程序 AppID 与 AppSecret，沿用真实登录配置，仅服务端保存 |
| `WX_MOCK_LOGIN` | `false`，模拟登录不发送微信消息 |
| `WX_SUBSCRIBE_ENABLED` | 默认 `false`；模板和字段配置完毕后改为 `true` |
| `WX_SUBSCRIBE_TEMPLATES` | 单行 JSON，按事件填写模板 ID 与字段映射 |
| `WX_SUBSCRIBE_ENV` | 正式版 `formal`，体验版 `trial`，开发版 `developer` |

```dotenv
WX_SUBSCRIBE_ENABLED=true
WX_SUBSCRIBE_ENV=formal
WX_SUBSCRIBE_TEMPLATES='{"team_invitation":{"template_id":"替换为实际邀请模板ID","fields":{"thing1":"team_name","name2":"actor_name","phrase3":"event","time4":"time"}},"team_application":{"template_id":"替换为实际申请模板ID","fields":{"thing1":"team_name","name2":"actor_name","phrase3":"event","time4":"time"}}}'
```

映射值支持 `team_name`（队伍名称）、`actor_name`（邀请人或申请人昵称）、`event`（入队邀请/入队申请）、`time`（事件时间）。支持 `thingN`、`nameN`、`phraseN`、`timeN` 类型键；`timeN` 必须映射 `time`，其他类型不能映射时间。姓名、短语、事项会按微信长度限制裁剪。**实际模板如使用其他字段类型，需先扩展映射实现，不能随意套用示例。**

已选用的「队伍通知」模板包含 `phrase6`（温馨提示）和 `thing7`（队伍名称）。邀请与申请共用它时，配置如下；将两个 ID 替换为该模板的同一个真实 ID：

```dotenv
WX_SUBSCRIBE_TEMPLATES='{"team_invitation":{"template_id":"替换为实际队伍通知模板ID","fields":{"phrase6":"event","thing7":"team_name"}},"team_application":{"template_id":"替换为实际队伍通知模板ID","fields":{"phrase6":"event","thing7":"team_name"}}}'
```

`captain_qq_reminder` 已停用，应从服务器 `WX_SUBSCRIBE_TEMPLATES` 中移除。即使旧配置仍包含它，新代码也不会向前端提供该用途，不再创建或发送催填消息；仍待发送的旧记录会在通知 worker 处理时跳过。邀请与申请共享模板的订阅继续有效。

配置完成后执行 `docker compose up -d --no-deps --force-recreate backend`，再 reload nginx。若微信接口提示 IP 不在白名单，在微信公众平台的开发设置中加入后端实际出口 IP。无需把 `api.weixin.qq.com` 加到小程序 request 域名，发送由后端完成。

### 授权、验收和发送状态

- 队长/选手在「我的队伍」「招募大厅」「消息」页主动点击「订阅微信提醒」，同意相应模板。一次性订阅不是永久授权，每个模板一次同意对应一次可用通知，使用后可再次订阅。前端回调记录仅代表用户选择，实际次数始终由微信校验。
- 没配模板时显示“微信提醒准备中”，仍可使用站内邀请、申请和约赛消息。不会补发配置前的旧邀请或申请。微信配置覆盖入队邀请与入队申请，约赛和管理员定向通知使用站内消息。
- 在两个自愿测试账号上完成授权，再分别发起真实申请/邀请并验收；不能以接口 200 或站内消息出现替代微信送达验证。此次自动测试全部模拟微信网络调用，没有向真实用户发测试通知。
- 出站队列随申请/邀请事务保存，进程每 10 秒读取待发送事件，原子领取防重复。网络推送发生在业务提交后；超时或微信拒绝不会回滚邀请/申请。已处理、隐藏或超过一天的队列消息不会继续发送。
- `wechat_outbox.status` 为 `pending` / `sending` / `sent` / `failed` / `unknown` / `expired` / `skipped`；`error_code=43101` 通常表示无可用订阅授权。发送超时或进程中断记为 `unknown`，不自动重发，避免重复消耗订阅次数。仅明确的 access_token 失效错误允许刷新令牌后重试一次。服务异常日志不写 token 或包含 token 的 URL。
- 只读检查：`GET /api/recruitment`、`GET /api/notifications/wechat/config`；`/api/notifications/summary`、`/api/notifications/team-applications`、`/api/notifications/schedules` 均需当前用户登录。公开配置接口仅返回启用的模板 ID/事件名称，不返回 AppSecret。


## 十二、定向站内通知与个人 QQ 整改

管理员入口：管理工作台 → 常用管理 → 定向通知。仅 `admin` 可搜索收件人、发送和查看最近发送记录；审核员无发送权限。按昵称、游戏 ID 或用户 ID 搜索，逐个选择最多 100 人；填写 40 字以内标题和 500 字以内正文，确认后写入收件箱。收件人目录不返回学号、认证凭证、QQ 或微信标识。

收件人在赛事主页「与你有关的消息」看到管理员通知未读数与最新未读标题。点击进入消息页，打开通知并确认后标记已读；普通用户只能读取自己的通知。后台显示最近 20 批发送及已读人数。这里是站内通知，不依赖微信模板或订阅授权，也不会触发微信推送。

在已有服务器项目目录完成源代码同步后，使用现有环境与数据卷执行：

```bash
docker compose build backend
docker compose run --rm --no-deps backend python -m app.migrations.admin_notices
docker compose up -d --no-deps --force-recreate backend
docker compose exec -T nginx nginx -s reload
```

迁移只幂等创建 `admin_notice_batches` 与 `admin_notices` 两张表，不创建历史消息、不发送通知、不清理 QQ 历史列或其他业务数据。通知批次与收件记录同事务写入；相同管理员的同一 `request_id` 重试不会重复发送，复用 ID 但更改内容返回 409。收件人不可用时整批失败，不静默漏发。已读查询包含收件人条件，不能读取或标记别人的通知。

先部署后端和通知表，再由维护者上传并发布新小程序。此次改动同时包含此前图片隐私授权修复；旧后端仍强制 QQ，会拒绝新版建队请求。发布前使用自愿测试账号验证创建队伍、管理员选人发送、仅指定用户收到、主页未读减少和非管理员权限；自动测试与离线预览不等同于微信真机或实际 MySQL 并发验收。

## 十三、一对一聊天

首版提供文字私聊和队伍／对阵卡片；首页「聊天」为独立入口，原系统通知继续使用消息页。完成学籍认证的用户可发起普通私聊，未认证用户可回复已有私聊或联系管理员。聊天资料不返回学号、QQ、认证截图或微信标识。管理员仅通过举报入口检查被举报的单条消息，支持驳回、隐藏和禁言，不提供任意浏览他人私聊的接口。

### 配置与启用

| 配置 | 默认与用途 |
|---|---|
| `CHAT_ENABLED` | `false`，关闭时聊天接口不可用；清理任务继续运行 |
| `CHAT_WRITE_ENABLED` | `false`，单独关闭新建会话和发送，已有会话仍可阅读、已读和清理 |
| `CHAT_ALLOW_USERS` | 空值为全部正常用户；灰度时填写两个自愿测试账号及管理员的用户 ID，以英文逗号分隔 |
| `CHAT_MODERATION_ENABLED` | `true`；发送前调用微信 `msg_sec_check`，检测失败拒绝发送，真实环境不得关闭检测 |
| `CHAT_DELETION_LEDGER` | Compose 固定为 `/app/chat-maintenance/deletions.jsonl`，独立 `chat_maintenance` 持久卷；非 Docker 运行必须设置可写的持久文件路径 |

微信内容检测沿用后端 `WX_APPID`、`WX_SECRET` 与真实登录 OpenID，不使用订阅通知模板。上线前用真实登录且满足微信接口访问条件的自愿测试账号确认检测能力、额度和相关隐私说明。在微信公众平台将 `wss://bjfumajor.com` 对应主机配置为 socket 合法域名，并确认 request 合法域名。聊天首版没有离线微信逐条推送，也不收集 QQ。

实时票据和广播在单个 Uvicorn 进程内；Dockerfile 显式 `--workers 1`、WebSocket 单帧最大 1KiB、传输待收队列最多 4 帧。不要直接扩为多进程或多个后端实例，扩容前需把票据、限流和广播迁移至共享设施。Nginx 为 `/api/chat/ws` 转发 Upgrade，容器日志限制为每容器 10MiB × 3。

经维护者批准部署后，在已同步源码的服务器项目目录执行；初次升级保持 `CHAT_ENABLED=false`、`CHAT_WRITE_ENABLED=false`，不要覆盖服务器现有密钥：

```bash
(
set -eu
docker compose build backend
docker compose stop backend
chat_backup="before-chat-$(date +%Y%m%d-%H%M%S).sql"
docker compose exec -T mysql sh -c 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysqldump -uroot --single-transaction --routines --triggers cs2_competition' > "$chat_backup"
test -s "$chat_backup"
docker compose run --rm --no-deps backend python -m app.migrations.chat
docker compose up -d --no-deps backend
docker compose exec -T nginx nginx -t
docker compose exec -T nginx nginx -s reload
docker compose ps backend
)
```

迁移仅创建聊天表，不生成测试账号或聊天，也不修改报名、比分或认证数据。日志轮转需要在适当维护窗口重建对应容器才对旧容器生效，单纯 reload nginx 不会应用 Docker logging 设置。随后维护者上传体验版，设置指定 `CHAT_ALLOW_USERS` 后开启两个聊天开关并 `docker compose up -d --no-deps --force-recreate backend`，完成双账号发送／重试／断线补收／已读／拉黑／举报验收，再决定发布并取消灰度名单。旧服务器未部署聊天接口时，新前端提示「聊天暂未开放」。

Nginx 配置是单文件挂载；同步时应原地写入，避免用解压或文件替换改变文件 inode 后，容器仍读取旧配置。部署后比较主机 `nginx.conf` 与容器 `/etc/nginx/nginx.conf` 的 SHA-256；若不同，先执行 `docker compose run --rm --no-deps nginx nginx -t` 校验新挂载，再执行 `docker compose up -d --no-deps --force-recreate nginx`，重新核验 HTTPS 与 WSS。这会短暂重启反向代理，不需要重建 MySQL。

### 缓存、同步和清理

- 消息与 `chat_events` 在同一 MySQL 事务内保存，提交后 HTTP 才确认发送成功。事件表不复制正文；实时 worker 合并同会话的更新，向有界内存队列发送会话 ID 和序号。在线提醒失败或服务重启后，客户端从数据库补收；「提醒尝试完成」不等于送达或已读。
- 手机缓存按账号隔离，只持久化最近 24 小时的内容，最多 20 个会话、每会话 100 条、总计 2MiB。进入页面须先向服务器验证权限并补齐撤回／删除事件，验证失败不显示旧正文。页面最多保留 200 条浏览窗口与 10 条待发送消息；更早历史分页浏览。退出、换号和当前登录失效会立即清缓存和连接。
- 成功发送的本人消息视为本人已读；接收方仅上报实际进入视口的消息 ID，打开最新一页不会把旧消息一并标读。每分钟、每批最多 200 条清理「发送满 24 小时且接收方已读」的正文；长期未读正文继续保留。撤回／管理员隐藏立即清正文。每次清正文前同步写入无正文删除清单；清单缺失或写入失败会中止删除，并保留待处理记录供重试。
- 清理后保留无正文去重元数据 35 天，增量事件保留 7 天；事件游标过期时取新快照。发送意图超过 24 小时不能新写入，因此去重元数据过期后也不能重放旧消息使其复活。举报原因、管理员处理记录与删除清单属于维护元数据，不能把它们当作已经清掉的普通消息正文；清单轮换须结合实际备份保留期，不自动删除最新清单。
- 普通聊天删除不等于已有数据库备份或 MySQL binlog 的副本立即擦除。备份及日志按运维保留期淘汰；删除清单须另行保存在独立位置并备份，不能随旧数据库一起回退。异机备份和报警仍需实际配置，不能仅依赖服务器内的数据卷。

### 恢复与回滚

恢复数据库前关闭聊天读写，保留当前 `chat_maintenance` 卷和最新删除清单。恢复后先执行：

```bash
docker compose run --rm --no-deps backend python -m app.services.chat_deletions apply --ledger /app/chat-maintenance/deletions.jsonl
docker compose run --rm --no-deps backend python -m app.services.chat_service
```

第一条按消息编号、会话、发送者、发送意图编号、请求摘要和创建时间核对并清除已删除正文，追加删除事件，支持幂等重试。清单损坏时非零退出；核对输出中的身份不匹配记录，未完成检查前不要开放聊天。第二条执行一批常规清理，大积压需重复运行至处理完。不要使用旧清单覆盖最新清单，也不要把整库回滚作为只回滚代码的方法。

聊天回滚优先关闭写入，再关闭聊天入口；保留聊天表和新增业务数据。若切回不含 worker 的旧后端，应在保留的新版本维护镜像中定时执行清理 CLI，继续遵守已确认的保留规则。发布状态和验证结果统一见项目日志。

## 十六、管理员操作审计与聊天列表刷新

管理员资料修改、角色修改、重新识别候选学号，以及段位申请通过/驳回，会在对应业务事务中写入 `admin_audits`。记录服务端鉴权操作者 ID、动作发生时角色、目标用户 ID、动作、申请 ID、变更前后值、中文说明及 UTC 时间。没有变化、重复审批和提交失败不新增成功记录；不回填上线前无法核实的操作者。原有资料提交后再计算队伍评分的流程保留，后续计算失败时已经提交的资料和审计仍存在。

仅管理员可调用 `GET /api/admin/audit-logs`，审核员不能查询。支持 `actor_id`、`target_user_id`、`action`、`application_id`、`created_from`、`created_to`、`page`、`page_size`；每页默认 20，最大 100。例如 `/api/admin/audit-logs?target_user_id=114&page=1&page_size=20`。时间筛选建议传带 `Z` 或时区偏移的 ISO 8601 值；无时区输入按 UTC。动作值为 `user.update`、`user.role.update`、`user.student_id.recognize`、`rank_application.approve`、`rank_application.reject`。返回 `items`、`total`、`page`、`page_size`，时间明确带 UTC 时区。

资料/角色/候选学号请求体支持可选 `audit_reason`，段位审批接口通过同名查询参数传递；最长 500 字，未填或空白使用固定中文说明，旧客户端仍可使用。审计中的学号属于受限资料，仅管理员查询；不保存截图路径、原图、OpenID 或令牌，截图只记录有无及是否替换。没有修改或删除审计的接口，用户删除后保留原操作 ID。首版提供查询 API，未增加管理端审计页面。

已有服务升级先保留数据库、配置和旧镜像备份，再从已审核代码构建并显式创建新表：

```bash
docker compose build backend
docker compose run --rm --no-deps backend python -m app.migrations.admin_audit
docker compose up -d --no-deps --force-recreate backend
docker compose exec -T nginx nginx -s reload
```

迁移可重复执行，只创建新审计表，不修改用户、上传文件或既有历史。非 Docker 环境在实际服务环境的 `backend` 目录执行 `python -m app.migrations.admin_audit` 后重启。发布后应核验管理员查询成功、审核员/普通用户被拒绝，并在隔离数据中检查业务修改和审计共同保存。回滚代码时保留审计表及记录，不恢复旧数据库。

聊天列表修复只涉及 `miniprogram/pages/chats/chats.js`，不改布局样式，也不新增聊天后端接口。后台事件从首屏重新核验已显示窗口，按 ID 去重并更新排序，成功后原子替换列表和游标；分页或刷新失败保留旧数据。每次后台刷新最多读取已加载页数加一页，大量变化超出预算时提示下拉刷新；主动下拉返回首屏。同账号返回页面保留分页，换号清空，迟到响应不能覆盖新页面；排序变化时尝试保持可见会话的滚动位置，主动滚动时不强制补偿。此项需维护者发布对应小程序后生效，后端升级不会自动更新手机前端；真机滚动和实时刷新仍需体验版核验。实际完成与发布状态见统一项目日志。


## 赛事助手与微信补订阅（独立配置）

2026-11-01已部署核心赛事助手、滚动摘要与补订阅后端。经维护者明确允许，生产助手复用现有智谱Key并完成真实文本工具调用、工具结果续答、摘要以及登录HTTP查询验收后启用；代码和示例默认仍关闭。微信原生授权/真实推送、摘要广泛质量与并发仍需各自验收，不能仅凭单元测试或status可用断言完成。助手与原识图、私聊配置相互独立，没有更换YOLO/OCR权重。新增认证协助属于后续本机开发，不在此核心发布包中，发布状态以项目日志为准。

- 后端准备：使用原运行环境，不增加依赖。备份后在后端环境执行 `python -m app.migrations.assistant` 与 `python -m app.migrations.recruitment_notifications`。前者幂等建表，并检查MySQL/MariaDB助手轮次`created_at`精度，必要时执行`DATETIME(6)`升级；不删除或回填旧记录，已丢失的历史微秒无法恢复。该DDL可能短暂锁定助手轮次表，应随维护部署执行。现有启动入口的Base.metadata.create_all不会给既有列升级精度，不能代替显式迁移；有多实例时先准备表结构，再滚动启动。此处为后续维护步骤，本次未对任何生产数据库执行。
- 模型配置：默认候选更新为 `ASSISTANT_MODEL=glm-5.3-flash`，但保持 `ASSISTANT_ENABLED=false`、`ASSISTANT_EXTERNAL_ENABLED=false`、`ASSISTANT_TOOL_MODEL_VERIFIED=false`。启用前需配置独立 `ASSISTANT_API_KEY` 并验收当前账户的文本tools/tool_calls能力、公开业务资料范围及费用。支持的地址固定为智谱HTTPS `/api/paas/v4`，不接受任意代理/跳转，不自动借用识图密钥。GLM-5.3-Flash强制thinking.enabled，使用reasoning_effort=low、max_tokens=4096、clear_thinking=false；工具回合的reasoning_content仅在同一请求内存中原样回传，不进入数据库、trace、日志、API响应或后续用户轮次。单条思考最多24000 UTF-8字节、累计工具回合最多48000字节，请求正文最多100000字节，超限或异常明确结束，不截断思考伪装有效。保留显式配置glm-4.7-flash的关闭思考兼容模式，不自动切换模型或付费重试。2026-11-01官方标准API价：5.3-Flash输入0.8元/百万tokens、输出2.8元/百万tokens；该新模型不是免费版，后续价格以官网为准。官方资料：https://docs.bigmodel.cn/cn/guide/models/vlm/glm-5.3-flash 、https://docs.bigmodel.cn/cn/guide/start/pricing 、https://docs.bigmodel.cn/cn/guide/start/concept-param 、https://docs.bigmodel.cn/cn/guide/capabilities/thinking-mode 。文档能力与模拟协议回归不等于真实调用已验收；本次没有外部模型请求。
- 助手入口：小程序首页内容最顶部始终展示“赛事助手”卡片与“进入助手”按钮，不再受服务开关控制显隐。`GET /api/assistant/status`只影响状态标签，关闭、未就绪、404及网络失败都允许进入 `pages/assistant/assistant` 查看真实说明，不绕过后端开关。404明确提示后端服务待更新。此入口改动需要发布前端才会影响手机正式版。认证用户创建独立助手会话，输入client_message_id保证重试幂等；会话24小时后过期，最多100轮；独立维护任务每分钟按200会话批次全局清理过期正文/turn/trace，开关关闭后仍继续清理，不承诺精确秒级删除。未确认预览到期24小时后清理；完成的最小报名幂等账本保留30天供核查，之后旧操作ID只会失效，不会重新提交。模型等待不持事务。报名确认只由独立HTTP卡片按钮触发，不能通过工具循环调用确认；原个人approved、团队pending语义保留。
- 规则维护：管理员 `GET /api/assistant/knowledge` 读取当前version、entries、来源、scope、生效时间、maintainer和公开支持目录；整理审核后 `PUT`完整新版本（expected_version=读取的当前version，version=新编号、maintainer、entries、public_admin_contacts）。每条含id/title/text/keywords/status、可选scope_match_id/effective_from/effective_until及source{title,reference,version}；只有active且在作用域/生效期内可检索。发布后每次读取立即重建关键词索引摘要，不需另装向量库。并发发布基于唯一previous_version拒绝覆盖。公开目录当前仅指向参赛须知；不会因账号是admin/reviewer就公开其通讯录。
- 知识来源限制：内置种子在 `backend/app/data/assistant_knowledge.json`，来自当前仓库参赛须知及维护者确认的交互要求，附源版本；完整策划案和本会话后续赛制安排有冲突，奖金/完整赛制尚未作为active条款导入。维护者须按具体赛事整理确认后发布，未知项明确未知。赛程/比分/报名/选手地图统计始终用实时业务工具重新查询，不能维护为知识库旧快照。
- 微信模板只覆盖入队邀请team_invitation和入队申请team_application。个人报名引导订阅后续入队邀请，队长招募引导入队申请；不假称支持报名结果或赛程提醒。原 `WX_SUBSCRIBE_TEMPLATES` 的template_id及fields、正式/体验环境、AppID/Secret仍需与微信后台一致；无配置时允许继续并保留站内渠道，不借用不相干模板。本轮未读取或修改密钥。
- 订阅状态：`GET /api/notifications/wechat/state`按登录账号和模板隔离；enabled/has_accepted表示曾同意，actual_remaining固定unknown。POST原subscriptions路径新增可选attempt_id防重复回调；成功发送才记对应Outbox使用事件，unknown不冒称送达/消耗，不自动重发。新的授权不被在途旧发送覆盖；补订阅文案只建议补充，不宣称累计额度耗尽。
- 人工验收：先在体验环境核验首页关闭/开启、两账号会话隔离、对阵BO3小分与导入统计同名消歧；再对个人报名、队长发布招募/邀请/申请、助手个人确认逐一检查原生订阅弹窗。分别accept/reject/ban/取消/断网，确认业务和站内消息行为；由自愿测试账号接收一次真实通知后重进页面，验证补订阅提示、再次授权、切账号及模板变更。真实MySQL并发确认也须在隔离测试库验收；本机SQLite不替代该证据。

### 可选滚动语义摘要（仅助手工作上下文）

此实现参考 LangChain SummarizationMiddleware 与 LangMem RunningSummary 的滚动策略，未复制其源码或引入 LangChain/LangGraph/LangMem 依赖；也未安装 LLMLingua 的额外模型。参考：https://docs.langchain.com/oss/python/langchain/middleware/built-in#summarization 、https://langchain-ai.github.io/langmem/guides/summarization/ 、https://github.com/langchain-ai/langmem 、https://github.com/microsoft/LLMLingua 。

新增 `assistant_summaries` 侧表随原助手迁移命令幂等创建，不给旧表追加列，也不删除或改写原始对话、报名账本；仅显式升级既有MySQL助手轮次时间列的精度，新侧表时间也保留微秒。旧同秒记录若作用域混合则整组与更旧历史不作摘要来源；秒精度scope起点的同秒整组也排除，不能靠UUID猜先后。摘要随24小时助手会话级联清理。旧库未建该表时压缩回退近期历史，仍需在正式启用前完成迁移。

- 配置：`ASSISTANT_SUMMARY_ENABLED=false` 默认关闭；触发预算 `ASSISTANT_SUMMARY_TRIGGER_BYTES=20000`，最多保留 `ASSISTANT_SUMMARY_KEEP_TURNS=6` 个近期完整轮次，工作历史上限 `ASSISTANT_HISTORY_BUDGET_BYTES=50000`；摘要输入上限50000字节、输出上限4000字节、调用超时最多8秒，对应 `ASSISTANT_SUMMARY_INPUT_MAX_BYTES`、`ASSISTANT_SUMMARY_MAX_BYTES`、`ASSISTANT_SUMMARY_TIMEOUT_SECONDS`。这里是JSON UTF-8字节预算，不能当作目标模型的精确token计数；实际出站仍受100000字节硬限制。
- 自动化范围：只在用户轮次之间，对本账号、本会话、当前连续赛事作用域的已完成且脱敏历史生成旧摘要；最多一次无工具摘要调用，保留近期完整user/assistant消息对，再加入本轮问题与独立受控对象。达到预算时可减少完整轮次数量，不截断某一条消息或JSON。原会话仍按既有24小时政策保留；摘要是有损资料，不能承诺永不遗忘。
- 一致性：记录作用域起点、`(created_at,id)`来源游标、来源SHA256及摘要版本。先获得会话lease再读源，网络等待无数据库事务或行锁；返回后重新验证lease、来源及版本并以CAS提交。切换/清空赛事隔离旧摘要，重复的空context不误当每轮重置；未成功摘要的来源不推进游标。
- 信任与权限：摘要仅保存topics/preferences/open_questions/source_notes，作为明确标记的低信任user层资料；不覆盖context_json/memory_json、实时比分/资格、授权或确认卡。当前轮工具调用及tool_call_id结果保持完整配对；reasoning_content仅当前轮内存回传，不入摘要。过大工具结果整体替换为有效的有界JSON提示，不截断协议。未提供用户私聊、截图、学号或确认令牌给摘要器。
- 失败处理：摘要禁用、表未迁移、预算不足、超时、格式/长度不合格、源变化或CAS冲突时不覆盖有效旧摘要，回退允许的近期完整历史；失去lease则本轮停止。摘要耗时计入原总60秒预算，每轮最多新增一次模型请求；不会因为压缩失败而自动切模型、重试付费摘要或删除原始历史。
- 启用验收：核心发布已经完成一次真实文本工具循环、结构摘要、登录HTTP公开查询与真实MySQL合成来源游标/锁失效冒烟检查，并按维护者授权开启；生产借用Key的授权例外不改变源码的独立Key/外传/验收门控。后续仍应检查真实摘要质量、遗漏、额外费用、长请求时延、MySQL并发和回退；仅status可用不代表所有链路均验收。此开关只影响助手，不开启私聊压缩或认证图片通道。

真实智谱协议注意：当前GLM-5.3-Flash即使thinking.enabled，部分合法工具响应也会省略reasoning_content。适配器接受该缺失，不补造思考；有字符串时仍原样回传并限制字节，异常类型/超限拒绝。不要把“应当回传供应商实际提供的思考”误写为“供应商每次必须提供思考”。Compose已添加ASSISTANT_*透传，单独修改.env但不透传容器仍不能配置助手。
