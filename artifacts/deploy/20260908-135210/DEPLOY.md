# 2026-09-08 手动部署说明

本包包含报名审批后“待分组”、编排后锁定队伍名单，以及种子和分组统一事务的后端更新，同时包含此前已完成的报名时间、三/四小组赛制修复。共 24 个文件，仅限 `backend/app/**/*.py` 和 `backend/requirements.txt`。

发布编号：`20260908-135210`。SHA-256：`1ae0b8b1db3192d1bc7a1af5d6632a88870adbd80ededec3a64e08f093265331`。逐文件校验值见同目录 `manifest.json`。

验证记录：89 项后端测试、83 项前端测试及 14 个 WXML 文件编译通过。本轮无数据库迁移；名人堂仍未上线。

服务器沿用已核实的 `ubuntu@192.144.191.253`，项目目录 `/home/ubuntu/app`，Docker Compose 服务 `backend`、`mysql`、`nginx`。本次仅在本机准备包，没有连接服务器、备份当前线上数据、上传或执行部署。下方命令会在你实际部署时重新备份当前线上版本。

## 1. 本机 PowerShell 上传

```powershell
cd 'D:\vs code 库\北林major报名系统'
Get-FileHash '.\artifacts\deploy\20260908-135210\backend-release.tar.gz' -Algorithm SHA256
scp '.\artifacts\deploy\20260908-135210\backend-release.tar.gz' ubuntu@192.144.191.253:/home/ubuntu/backend-release-20260908-135210.tar.gz
ssh ubuntu@192.144.191.253
```

密码只在 SSH/SCP 提示时输入；不用在聊天或命令里写明。

## 2. 服务器备份并部署

在服务器的 Bash 终端完整执行下列代码。任一步失败都会停止，不要跳过报错继续执行后续行。备份从当前运行的后端容器和 MySQL 容器生成，每次运行都创建新的权限为 700 的目录，并为当前镜像增加独立回滚标签；不复用昨天的备份。

```bash
(
set -euo pipefail
umask 077
cd /home/ubuntu/app
release_id='20260908-135210'
archive_path="/home/ubuntu/backend-release-${release_id}.tar.gz"

# 为本次实际更新单独备份；权限限制在当前服务器用户。
mkdir -p /home/ubuntu/releases
backup_dir=$(mktemp -d "/home/ubuntu/releases/${release_id}-$(date +%Y%m%d-%H%M%S)-XXXXXX")
chmod 700 "$backup_dir"
printf '本次备份目录：%s\n' "$backup_dir"

backend_container=$(docker compose ps -q backend)
test -n "$backend_container"
image_id=$(docker inspect --format '{{.Image}}' "$backend_container")
test -n "$image_id"
printf '%s\n' "$image_id" > "$backup_dir/backend-image-id.txt"
rollback_image="bjfu-backend:rollback-$(basename "$backup_dir")"
docker image tag "$image_id" "$rollback_image"
printf '%s\n' "$rollback_image" > "$backup_dir/backend-rollback-tag.txt"
printf 'services:\n  backend:\n    image: %s\n' "$rollback_image" > "$backup_dir/backend.rollback.yml"

# 敏感配置仅写入服务器的私有备份，部署包不携带配置。
source_paths=(backend/app backend/requirements.txt backend/Dockerfile docker-compose.yml nginx.conf)
for extra in .env backend/.env backend/.dockerignore docker-compose.override.yml docker-compose.override.yaml; do
  if [ -f "$extra" ]; then
    source_paths+=("$extra")
  fi
done
tar -czf "$backup_dir/source-before.tar.gz" "${source_paths[@]}"
test -s "$backup_dir/source-before.tar.gz"
tar -tzf "$backup_dir/source-before.tar.gz" > /dev/null

# 密码取自 MySQL 容器现有环境，只作为环境变量传递，不写进命令参数。
docker compose exec -T mysql sh -c '
  set -eu
  : "${MYSQL_ROOT_PASSWORD:?MYSQL_ROOT_PASSWORD is unset}"
  : "${MYSQL_DATABASE:?MYSQL_DATABASE is unset}"
  export MYSQL_PWD="$MYSQL_ROOT_PASSWORD"
  exec mysqldump --user=root --single-transaction --routines --triggers --events \
    --hex-blob --no-tablespaces --set-gtid-purged=OFF --databases "$MYSQL_DATABASE"
' > "$backup_dir/database-before.sql"
test -s "$backup_dir/database-before.sql"
test -s "$backup_dir/backend-image-id.txt"
test -s "$backup_dir/backend-rollback-tag.txt"

# 所有备份成功后才校验并替换代码。
printf '1ae0b8b1db3192d1bc7a1af5d6632a88870adbd80ededec3a64e08f093265331  %s\n' "$archive_path" | sha256sum -c -
printf '%s\n' "$backup_dir" > "/home/ubuntu/releases/${release_id}-backup-path.txt"
tar -xzf "$archive_path" -C /home/ubuntu/app
docker compose up -d --build --no-deps backend
docker compose exec -T nginx nginx -t
docker compose exec -T nginx nginx -s reload
docker compose ps
docker compose logs --tail=50 backend
printf '部署命令完成；本次回滚备份：%s\n' "$backup_dir"
)
```

记录输出的本次备份目录。数据库和配置备份仅保存在服务器私有目录，不应公开分享。若备份或校验失败，代码尚未替换；若构建、启动或检查失败，可使用下方回滚命令。

## 3. 只读确认接口上线

```bash
set -o pipefail
curl -fsS https://bjfumajor.com/openapi.json | python3 -c 'import json,sys; p=json.load(sys.stdin)["paths"]; required={"/api/matches/{match_id}/registrations/approve-team":"post", "/api/matches/{match_id}/registration-window":"put", "/api/matches/{match_id}/seed-and-group":"post"}; missing=[method.upper()+" "+path for path,method in required.items() if method not in p.get(path,{})]; assert not missing, "Missing APIs: "+", ".join(missing); print("OK: all three backend APIs are online")'
```

这项检查只读取公开 OpenAPI，不会审批队伍或修改赛事。新后端刚启动时如出现暂时的 502，数秒后重试此只读检查；持续失败时检查日志或回滚。新原子编排接口必须出现，更新后的小程序才可使用“分组（含种子）”。

## 4. 小程序上传和正式发布

后端检查通过后，在微信开发者工具重新编译并测试。需上传时先开启「设置 → 安全设置 → 服务端口」，退出 SSH 后在本机 PowerShell 执行：

```powershell
& 'D:\微信小程序开发\微信web开发者工具\cli.bat' upload --project 'D:\vs code 库\北林major报名系统' --version '2026.09.08.1' --desc '报名通过显示待分组，编排后锁定名单，种子分组统一事务' --lang zh
```

也可在开发者工具点击「上传」。然后到微信公众平台「版本管理」提交审核，通过后发布。CLI 上传不代表已审核或正式发布；本次没有自动调用上传工具。前端上传的是当前项目工作目录，不包含在本后端压缩包中。

## 5. 回滚本次应用代码和镜像

下面读取本次部署成功完成备份后记录的目录，核对回滚标签仍对应更新前的镜像 ID，再恢复源码并以该镜像启动。服务器环境配置、数据库和上传卷保持当前状态。

```bash
(
set -euo pipefail
umask 077
cd /home/ubuntu/app
release_id='20260908-135210'
backup_dir=$(cat "/home/ubuntu/releases/${release_id}-backup-path.txt")
case "$backup_dir" in
  /home/ubuntu/releases/"${release_id}"-*) ;;
  *) echo '备份路径不属于本次发布，停止回滚' >&2; exit 1 ;;
esac
test -s "$backup_dir/source-before.tar.gz"
test -s "$backup_dir/backend-image-id.txt"
test -s "$backup_dir/backend-rollback-tag.txt"
rollback_image=$(cat "$backup_dir/backend-rollback-tag.txt")
expected_image_id=$(cat "$backup_dir/backend-image-id.txt")
actual_image_id=$(docker image inspect --format '{{.Id}}' "$rollback_image")
test "$expected_image_id" = "$actual_image_id"

# 恢复更新前源码；运行镜像同时回退到该备份对应版本。
tar -xzf "$backup_dir/source-before.tar.gz" -C /home/ubuntu/app backend/app backend/requirements.txt
compose_files=(-f docker-compose.yml)
for extra in docker-compose.override.yml docker-compose.override.yaml; do
  if [ -f "$extra" ]; then
    compose_files+=(-f "$extra")
    break
  fi
done
docker compose "${compose_files[@]}" -f "$backup_dir/backend.rollback.yml" up -d --no-deps --no-build --pull never backend
docker compose exec -T nginx nginx -t
docker compose exec -T nginx nginx -s reload
docker compose ps
docker compose logs --tail=50 backend
)
```

回滚不导入 `database-before.sql`，也不覆盖 `.env`、Compose 或 Nginx 配置。数据库备份仅用于另行处理的数据恢复场景。本轮不要执行 `docker compose down -v`，不要覆盖服务器凭据。已有错误历史数据不会被部署或回滚自动重写。
