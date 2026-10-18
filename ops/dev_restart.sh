#!/usr/bin/env bash
# 开发栈快速重启（改动后端源码后用）。
#
# 背景：compose 为生产优雅停机设置了 stop_grace_period（server 40s / worker 120s /
# heavy 300s）。celery-heavy 使用 threads 池，热停机会等待在途线程——即使空闲也
# 可能挂住，于是 `docker compose restart` 要等满 300s（实测 5:00）。`-t` 覆盖本次
# 重启的宽限期：空闲时服务仍会正常优雅退出，只有卡住时才在 N 秒后强制终止。
#
# 仅用于本地开发：在途任务/请求最多等待 RESTART_TIMEOUT 秒（默认 3s）。
# 生产环境请直接用 `docker compose restart <service>`，保留完整优雅停机窗口。
#
# 用法：
#   bash ops/dev_restart.sh            # 默认 3s 宽限
#   RESTART_TIMEOUT=10 bash ops/dev_restart.sh
set -euo pipefail
cd "$(dirname "$0")/.."
TIMEOUT="${RESTART_TIMEOUT:-3}"
exec docker compose restart -t "${TIMEOUT}" server celery-worker celery-heavy
