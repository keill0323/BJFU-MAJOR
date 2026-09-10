# 本次手动部署命令

> 已由 [2026-09-08 新部署包](../20260908-135210/DEPLOY.md) 替代。后续更新请使用新包及其当次备份、校验、回滚步骤；本目录保留为历史记录，旧压缩包未修改。

本包是 2026-09-07 已验证代码的快照，仅包含 `backend/app/**/*.py` 和 `backend/requirements.txt`，共 24 个文件。不会上传本地 `.env`、数据库、截图或密码。

正式服务器：`ubuntu@192.144.191.253`。已核实实际目录为 `/home/ubuntu/app`，运行方式为 Docker Compose，服务名为 `backend`、`mysql`、`nginx`。

当前只完成备份，尚未上传此包、替换线上代码、重建镜像或重启服务。小程序也尚未上传或发布。

## 1. 本机 PowerShell 上传

```powershell
cd 'D:\vs code 库\北林major报名系统'
scp '.\artifacts\deploy\20260907-213309\backend-release.tar.gz' ubuntu@192.144.191.253:/home/ubuntu/backend-release-20260907-213309.tar.gz
ssh ubuntu@192.144.191.253
```

密码只在 SSH/SCP 提示时输入。以下命令不包含密码。

## 2. 登录服务器后部署后端

整段复制到服务器终端；任何步骤失败都会停止后续步骤。已有数据库、上传卷、服务器环境配置及 Nginx 配置均沿用当前值。

```bash
(
set -e
cd /home/ubuntu/app
echo '1d0c11eb3d8a0bf42d8040a561d73d485f0ce501fd617d99c42922344d08652d  /home/ubuntu/backend-release-20260907-213309.tar.gz' | sha256sum -c -
tar -xzf /home/ubuntu/backend-release-20260907-213309.tar.gz -C /home/ubuntu/app
docker compose up -d --build --no-deps backend
docker compose exec -T nginx nginx -t
docker compose exec -T nginx nginx -s reload
docker compose ps
docker compose logs --tail=50 backend
)
```

随后确认新接口已出现（如服务刚启动时暂时失败，数秒后重试此只读检查）：

```bash
curl -fsS https://bjfumajor.com/openapi.json | python3 -c 'import json,sys; p=json.load(sys.stdin)["paths"]; assert "post" in p.get("/api/matches/{match_id}/registrations/approve-team", {}); assert "put" in p.get("/api/matches/{match_id}/registration-window", {}); print("OK: new backend APIs are online")'
curl -fsS https://bjfumajor.com/ -o /dev/null && echo 'API reachable'
```

## 3. 小程序上传和正式发布

本机微信开发者工具需先开启「设置 → 安全设置 → 服务端口」。回到本机 PowerShell（退出 SSH 后），执行：

```powershell
& 'D:\微信小程序开发\微信web开发者工具\cli.bat' upload --project 'D:\vs code 库\北林major报名系统' --version '2026.09.07.1' --desc '修复报名审核与时间设置，支持三四组赛制及段位图标展示' --lang zh
```

也可在开发者工具点击「上传」。上传后在微信公众平台的「版本管理」提交审核，通过后发布；CLI 上传不等于正式发布。只在开发者工具测试时重新编译即可。

## 已完成的备份及回退

备份目录权限为 700，数据库备份只保存在服务器：

- `/home/ubuntu/releases/20260907-213309/source-before.tar.gz`：后端源码及服务器配置。
- `/home/ubuntu/releases/20260907-213309/database-before.sql`：数据库一致性转储，非空检查通过。
- `app-backend:rollback-20260907-213309`：更新前镜像标签。

若新后端启动失败，回退应用代码和镜像（不自动回退或覆盖数据库）：

```bash
(
set -e
cd /home/ubuntu/app
tar -xzf /home/ubuntu/releases/20260907-213309/source-before.tar.gz -C /home/ubuntu/app backend/app backend/requirements.txt
docker tag app-backend:rollback-20260907-213309 app-backend:latest
docker compose up -d --no-deps --no-build backend
docker compose exec -T nginx nginx -t
docker compose exec -T nginx nginx -s reload
docker compose ps
)
```

本次无数据库迁移，不要运行 `docker compose down -v` 或覆盖服务器 `.env`。历史错误晋级记录不由部署自动重写。
