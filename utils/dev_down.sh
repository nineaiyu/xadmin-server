#!/bin/bash
# 停止 xadmin 开发环境容器（保留数据卷与镜像；下次 dev_up.sh 数秒恢复）
# 用法：bash utils/dev_down.sh
set -euo pipefail

cd "$(dirname "$0")/.."

if ! docker compose version >/dev/null 2>&1; then
  echo "[dev-down] 未找到 docker compose（v2）"
  exit 1
fi

docker compose stop
echo "[dev-down] 已停止全部容器（数据保留）。重新启动: bash utils/dev_up.sh"
