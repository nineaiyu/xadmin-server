#!/bin/bash
# xadmin 开发环境一键启动（幂等，可重复执行）：
#   1) 启动后端 compose 全栈（nginx / postgresql / redis / server / celery×3 / db-backup）
#   2) 等待健康检查通过 → 容器内执行 init_data（幂等；升级后同样适用）
#   3) 启动前端 dev server（存在 ../xadmin-client 时；Ctrl+C 仅退出前端）
#
# 依赖：docker（含 compose v2）、curl；启动前端需 Node >= 22.22.1 与 pnpm >= 11
# 用法：bash utils/dev_up.sh [--with-demo] [--backend-only]
set -euo pipefail

cd "$(dirname "$0")/.."

WITH_DEMO=0
BACKEND_ONLY=0

usage() {
  cat <<'EOF'
用法: bash utils/dev_up.sh [选项]

选项:
  --with-demo     初始化后追加演示数据（组织 / 审批 / 表单 / 聊天 / 知识库等，耗时约 1-2 分钟）
  --backend-only  仅启动后端，不启动前端 dev server
  -h, --help      显示本帮助
EOF
}

for arg in "$@"; do
  case "$arg" in
    --with-demo) WITH_DEMO=1 ;;
    --backend-only) BACKEND_ONLY=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "[dev-up] 未知参数: $arg"; usage; exit 1 ;;
  esac
done

if ! command -v docker >/dev/null 2>&1; then
  echo "[dev-up] 未找到 docker，请先安装 Docker Desktop（macOS）或 docker-ce（Linux）"
  exit 1
fi
if ! docker compose version >/dev/null 2>&1; then
  echo "[dev-up] 未找到 docker compose（v2），请升级 Docker 后重试"
  exit 1
fi

echo "[dev-up] 1/3 启动后端容器（首次运行会自动构建镜像，可能需要数分钟）..."
docker compose up -d

HEALTH_URL="http://127.0.0.1:8896/api/common/api/health"
echo "[dev-up] 2/3 等待服务就绪（${HEALTH_URL}）..."
ready=0
for _ in $(seq 1 60); do
  if curl -fs "$HEALTH_URL" >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 5
done
if [ "$ready" != "1" ]; then
  echo "[dev-up] 服务未在 5 分钟内就绪，请排查：docker compose logs server"
  exit 1
fi

INIT_ARGS=""
if [ "$WITH_DEMO" = "1" ]; then
  INIT_ARGS="--with-demo"
fi
echo "[dev-up] 初始化 / 补全数据（幂等；随机初始密码仅打印一次，请留意下方输出）..."
# shellcheck disable=SC2086  # INIT_ARGS 为固定白名单参数，需按词拆分传参
docker exec xadmin-server python utils/init_data.py ${INIT_ARGS}

echo "[dev-up] 环境自检（doctor；失败不阻塞启动，按输出中的修复命令处理）..."
docker exec xadmin-server python manage.py doctor || true

echo "[dev-up] 后端就绪: http://127.0.0.1:8896  (API 文档: /api-docs/swagger/)"

if [ "$BACKEND_ONLY" = "1" ]; then
  echo "[dev-up] --backend-only：跳过前端启动；停止后端: bash utils/dev_down.sh"
  exit 0
fi

FRONT_DIR="../xadmin-client"
if [ ! -f "${FRONT_DIR}/package.json" ]; then
  echo "[dev-up] 未找到 ${FRONT_DIR}，跳过前端。请在 xadmin-client 目录执行: pnpm install && pnpm dev"
  exit 0
fi
if ! command -v pnpm >/dev/null 2>&1; then
  echo "[dev-up] 未找到 pnpm（要求 Node >= 22.22.1 / pnpm >= 11），请手动启动前端: cd ${FRONT_DIR} && pnpm dev"
  exit 0
fi

cd "${FRONT_DIR}"
if [ ! -d node_modules ]; then
  echo "[dev-up] 安装前端依赖（pnpm install）..."
  pnpm install
fi

echo "[dev-up] 3/3 启动前端 dev server: http://127.0.0.1:8848"
echo "[dev-up] Ctrl+C 仅退出前端；后端容器保持运行（停止: bash xadmin-server/utils/dev_down.sh）"
exec pnpm dev
