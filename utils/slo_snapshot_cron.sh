#!/bin/bash
# SLO 快照采集（宿主机 cron / systemd timer 每日执行）
#
# 目的：HTTP/任务指标为**进程启动起累计**，单点快照只反映当前进程；
# 目标值校准需要 ≥3 个月跨重启的趋势数据（docs/ops/observability.md §三）。
# 本脚本按天把快照追加进 JSONL（SLO_SNAPSHOT_FILE），供季度校准回填 metrics.md。
#
# 用法（cron 示例，每日 06:17）：
#   17 6 * * * METRICS_TOKEN=<与 config.yml 一致> \
#     /data/xadmin-server/utils/slo_snapshot_cron.sh >> /var/log/xadmin-slo.log 2>&1
#
# 环境变量：
#   METRICS_URL          指标端点（默认 http://127.0.0.1:8896/api/common/api/metrics）
#   METRICS_TOKEN        必需；与 config.yml 的 METRICS_TOKEN 一致（空则脚本报错退出）
#   SLO_SNAPSHOT_FILE    累积文件（默认 <仓库>/tmp/slo_snapshots.jsonl）
#   PYTHON               解释器（默认 python3；容器内可指向 .venv/bin/python）
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
METRICS_URL=${METRICS_URL:-http://127.0.0.1:8896/api/common/api/metrics}
SLO_SNAPSHOT_FILE=${SLO_SNAPSHOT_FILE:-"$ROOT/tmp/slo_snapshots.jsonl"}
PYTHON=${PYTHON:-python3}

export METRICS_URL
exec "$PYTHON" "$ROOT/scripts/slo_snapshot.py" --append "$SLO_SNAPSHOT_FILE"
