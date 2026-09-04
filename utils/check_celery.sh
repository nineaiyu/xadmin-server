#!/bin/bash
# Celery worker 健康检查：按队列名参数化（worker 启动名 = 队列名@主机名）
# 用法: bash utils/check_celery.sh [celery|heavy]
set -e

QUEUE="${1-celery}"

test -e /tmp/worker_ready_${QUEUE}
test -e /tmp/worker_heartbeat_${QUEUE} && test $(($(date +%s) - $(stat -c %Y /tmp/worker_heartbeat_${QUEUE}))) -lt 20
