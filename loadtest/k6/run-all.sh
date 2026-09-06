#!/usr/bin/env bash
# T3.1 性能基线：顺序执行六个关键接口的 k6 压测，结果落盘 results/。
# 用法（详见 docs/ops/performance-baseline.md）：
#   cd xadmin-server/loadtest/k6
#   BASE_URL=http://127.0.0.1:8896 USERNAME=admin PASSWORD=xxx ./run-all.sh
# 负载档位可用 VUS / DURATION 全局覆盖（如 VUS=20 DURATION=2m）。
set -euo pipefail
cd "$(dirname "$0")"

if ! command -v k6 >/dev/null 2>&1; then
  echo "未检测到 k6，请先安装：https://k6.io/docs/get-started/installation/" >&2
  exit 1
fi

mkdir -p "${RESULT_DIR:-results}"

for script in 01-login.js 02-routes.js 03-list.js 04-metadata.js 05-export.js 06-import.js; do
  echo ""
  echo "=== k6 run ${script%.js} ==="
  k6 run "$script"
done

echo ""
echo "六轮压测完成，结果见 ${RESULT_DIR:-results}/。"
echo "回填口径见 docs/ops/performance-baseline.md，登记到 docs/metrics.md。"
