#!/usr/bin/env bash
# 容器化测试档一键重置（全真容器化测试迁移方案 §3.5 本地等价跑法的前置步骤）。
#
# compose.test.yml 是一次性专用容器：无任何 volume，`down` 即数据销毁。本脚本
# 每次「全新无数据状态」跑测试前执行——PG / Redis 从零开始，杜绝上一轮的
# 测试库（test_xadmin_realtest_gw*）、Redis 键（worker 前缀计数器/锁）与 E2E
# shard 库（xadmin_e2e_shard*）残留引发的跨轮串扰。
#
# 用法：bash utils/test_env_reset.sh
set -euo pipefail
cd "$(dirname "$0")/.."

COMPOSE_FILE="compose.test.yml"
PG_CONTAINER="xadmin-test-pg"
REDIS_CONTAINER="xadmin-test-redis"

# nightly 档遗留的同端口容器（docker run 方式起过 xadmin-pgtest-pg）会占住 55433，
# 迁移方案 §3.5 已声明其可移除（同为一次性测试容器，数据不保留）
if docker ps -a --format '{{.Names}}' | grep -qx "xadmin-pgtest-pg"; then
  echo "[reset] 移除 nightly 遗留容器 xadmin-pgtest-pg（占 55433 端口，一次性测试容器）"
  docker rm -f xadmin-pgtest-pg >/dev/null
fi

echo "[reset] 销毁测试容器（${PG_CONTAINER} / ${REDIS_CONTAINER}，数据随容器清空）..."
docker compose -f "${COMPOSE_FILE}" down --remove-orphans >/dev/null

echo "[reset] 重新拉起 postgres:17 + redis:8.10 ..."
docker compose -f "${COMPOSE_FILE}" up -d >/dev/null

for _ in $(seq 1 30); do
  if docker exec "${PG_CONTAINER}" pg_isready -U server >/dev/null 2>&1 &&
    docker exec "${REDIS_CONTAINER}" redis-cli ping 2>/dev/null | grep -q PONG; then
    echo "[reset] 容器就绪（零数据）：PG 127.0.0.1:55433 / Redis 127.0.0.1:56379"
    exit 0
  fi
  sleep 1
done

echo "[reset] 容器 30s 内未就绪，排查：docker compose -f ${COMPOSE_FILE} ps" >&2
exit 1
