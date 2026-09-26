#!/bin/bash
# 停止 xadmin 开发环境容器（保留数据卷与镜像；下次 dev_up.sh 数秒恢复）
# 用法：bash utils/dev_down.sh
set -euo pipefail

cd "$(dirname "$0")/.."

if ! docker compose version >/dev/null 2>&1; then
  echo "[dev-down] 未找到 docker compose（v2）"
  exit 1
fi

# compose 解析文件即需要凭据（${DB_PASSWORD:?}）：与 dev_up.sh 共用同一解析逻辑，
# 以 config.yml 为准同步 .env 派生缓存后，本脚本与直接操作 compose 的命令都可用
. "$(dirname "$0")/compose_env.sh"
sync_compose_credentials

docker compose stop
echo "[dev-down] 已停止全部容器（数据保留）。重新启动: bash utils/dev_up.sh"
