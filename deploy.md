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
