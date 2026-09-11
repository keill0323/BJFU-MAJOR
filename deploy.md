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

> 首次启动 MySQL 会自动建库 `cs2_competition`，后端启动时自动建表，**无需手动建表**。

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

---

### 已有冠军表升级

已有环境必须先备份数据库并停止 API 写入，再用新镜像执行冠军补录迁移。`create_all()` 只创建缺失表，不为已有表补列。下面的命令适用于仓库已有的冠军补录版本；后续新增迁移需随对应功能代码同步。

```bash
(
set -eu
umask 077
docker compose build backend
docker compose stop backend
upgrade_backup="before-manual-champions-$(date +%Y%m%d-%H%M%S).sql"
docker compose exec -T mysql sh -c 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysqldump -uroot --single-transaction --routines --triggers cs2_competition' > "$upgrade_backup"
test -s "$upgrade_backup"
# 备份与迁移任一步失败时终止，不继续启动。
docker compose run --rm --no-deps backend python -m app.migrations.manual_champions
# 仅在迁移成功后恢复服务。
docker compose up -d --no-deps backend
docker compose restart nginx
)
```

迁移可以重跑，保留已有冠军数据；MySQL DDL 不能整段事务回滚。非 Docker 环境在 `backend` 目录执行 `python -m app.migrations.manual_champions`，成功后重启后端。

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
| 中文乱码 | MySQL 连接串必须带 `?charset=utf8mb4` |
| 数据备份 | `docker compose exec mysql mysqldump -ucs2_user -p cs2_competition > backup.sql` |

---

## 十、备份建议

```bash
# 每天凌晨备份（crontab）
0 3 * * * docker compose -f /opt/cs2/docker-compose.yml exec -T mysql mysqldump -ucs2_user -p你的密码 cs2_competition > /opt/cs2/backup/$(date +\%F).sql
```
