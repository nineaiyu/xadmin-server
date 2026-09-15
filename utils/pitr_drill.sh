#!/usr/bin/env bash
# PITR 链路检查助手（只读，不改动任何数据/配置）。
#
# 用途：启用 WAL 归档（见 docs/ops/pitr.md）后，快速确认「归档链路是否健康」，
# 并在输出里给出时间点回放的标准步骤（回放需在隔离环境人工执行，不在本脚本内自动恢复生产）。
#
# 用法：
#   ./utils/pitr_drill.sh                 # 使用默认容器名/归档目录
#   PG_CONTAINER=xadmin-postgresql PG_ARCHIVE_DIR=/var/lib/postgresql/archive ./utils/pitr_drill.sh

set -uo pipefail

PG_CONTAINER="${PG_CONTAINER:-xadmin-postgresql}"
PG_ARCHIVE_DIR="${PG_ARCHIVE_DIR:-/var/lib/postgresql/archive}"
PG_USER="${PG_USER:-postgres}"

fail=0

echo "== PITR 链路检查 =="
echo "容器: ${PG_CONTAINER} | 归档目录: ${PG_ARCHIVE_DIR}"

if ! docker inspect "${PG_CONTAINER}" >/dev/null 2>&1; then
  echo "[FAIL] 容器 ${PG_CONTAINER} 不存在（用 PG_CONTAINER 指定正确名称）"
  exit 1
fi

archive_mode="$(docker exec "${PG_CONTAINER}" psql -U "${PG_USER}" -Atc "show archive_mode" 2>/dev/null || true)"
if [[ -z "${archive_mode}" ]]; then
  echo "[FAIL] 无法连接 PostgreSQL（检查 PG_USER/容器配置）"
  exit 1
fi
echo "archive_mode = ${archive_mode}"
if [[ "${archive_mode}" != "on" ]]; then
  echo "[WARN] 归档未启用：按 docs/ops/pitr.md §2.1 配置 archive_mode=on 并重启（重启前确认归档卷为独立盘）"
  fail=1
fi

command_line="$(docker exec "${PG_CONTAINER}" psql -U "${PG_USER}" -Atc "show archive_command" 2>/dev/null || true)"
echo "archive_command = ${command_line}"
timeout_value="$(docker exec "${PG_CONTAINER}" psql -U "${PG_USER}" -Atc "show archive_timeout" 2>/dev/null || true)"
echo "archive_timeout = ${timeout_value}"

count="$(docker exec "${PG_CONTAINER}" sh -c "ls ${PG_ARCHIVE_DIR} 2>/dev/null | wc -l" | tr -d ' ')"
echo "归档文件数 = ${count}"
if [[ "${count:-0}" -eq 0 ]]; then
  echo "[WARN] 归档目录为空：确认归档卷已挂载且 archive_command 可写"
  fail=1
fi

latest="$(docker exec "${PG_CONTAINER}" sh -c "ls -t ${PG_ARCHIVE_DIR} 2>/dev/null | head -1" | tr -d ' ')"
if [[ -n "${latest}" ]]; then
  echo "最新归档段 = ${latest}"
  age="$(docker exec "${PG_CONTAINER}" sh -c "echo \$(( \$(date +%s) - \$(stat -c %Y ${PG_ARCHIVE_DIR}/${latest}) ))" | tr -d ' ')"
  echo "最新归档距今 = ${age}s（滞后 > 600s 需告警排查）"
  if [[ "${age:-9999}" -gt 600 ]]; then
    echo "[WARN] 归档滞后超过 600s：检查归档目录写权限/磁盘空间/归档卷挂载"
    fail=1
  fi
fi

cat <<'STEPS'

== 时间点回放演练步骤（在隔离环境执行，勿覆盖生产数据目录）==
  1) 选目标时间点，先制造并记录一条可辨识测试数据（随后"误删"它）
  2) 在临时容器恢复基础备份（最近一次 pg_dump 或 pg_basebackup 副本）
  3) postgresql.auto.conf 配置：
       restore_command = 'cp <ARCHIVE_DIR>/%f %p'
       recovery_target_time = '<目标时间点>+08'
       recovery_target_action = 'promote'
  4) 启动临时实例，psql 校验：误删数据已恢复、其后正常数据未被回退
  5) 结果追加到 docs/ops/pitr.md §5 演练记录表（RTO/RPO 实测）
STEPS

if [[ "${fail}" -eq 0 ]]; then
  echo "[OK] 归档链路检查通过（archive_mode=on 且归档持续产出）"
else
  echo "[NOTE] 存在告警项：按上方提示处置后重跑本脚本"
fi

exit "${fail}"
