#!/bin/bash
# 备份恢复演练（含异地副本校验，一键闭环）
#
# 一键闭环：触发真实备份链路 → 校验异地副本 → 恢复到独立验证库 → 逐表行数对比 → 输出报告片段
#
# 用法（宿主机，xadmin-server 目录下；脚本用了数组等 bash 特性，请用 bash 而非 sh）：
#   bash utils/backup_drill.sh
#   BACKUP_REMOTE_DIR=../xadmin-db-backups-remote bash utils/backup_drill.sh  # 校验 local 异地副本
#   KEEP_VERIFY_DB=1 bash utils/backup_drill.sh                               # 保留验证库便于人工核查
#
# 环境变量：
#   BACKUP_DIR        备份目录（默认 ../xadmin-db-backups，与 compose 卷一致）
#   BACKUP_REMOTE_DIR 异地副本目录（local 模式）；为空则异地项记 SKIP
#   BACKUP_CONTAINER  备份容器名（默认 xadmin-db-backup，走真实任务链路而非手工 pg_dump）
#   DB_CONTAINER      数据库容器名（默认 xadmin-postgresql）
#   DB_USER           数据库用户（默认 server）
#   SOURCE_DB         源库（默认 xadmin）
#   TARGET_DB         验证库（默认 xadmin_restore_test，脚本会 DROP 重建）
set -euo pipefail

BASE_DIR=$(cd "$(dirname "$0")/.." && pwd)
BACKUP_DIR=${BACKUP_DIR:-${BASE_DIR}/../xadmin-db-backups}
BACKUP_REMOTE_DIR=${BACKUP_REMOTE_DIR:-}
BACKUP_CONTAINER=${BACKUP_CONTAINER:-xadmin-db-backup}
DB_CONTAINER=${DB_CONTAINER:-xadmin-postgresql}
DB_USER=${DB_USER:-server}
SOURCE_DB=${SOURCE_DB:-xadmin}
TARGET_DB=${TARGET_DB:-xadmin_restore_test}
KEEP_VERIFY_DB=${KEEP_VERIFY_DB:-0}

STEP_STATUS=()

log() { echo "[$(date '+%F %T')] $*"; }
step() { STEP_STATUS+=("| $1 | $2 |"); }

now() {
    local t
    t=$(date +%s.%N 2>/dev/null || true)
    if [[ "${t}" =~ ^[0-9]+\.[0-9]+$ ]]; then
        echo "${t}"
    else
        python3 -c 'import time; print(time.time())' 2>/dev/null || date +%s
    fi
}

elapsed() { awk -v s="$1" -v e="$2" 'BEGIN { printf "%.2f", e - s }'; }

sha256_of() {
    if command -v sha256sum > /dev/null 2>&1; then
        sha256sum "$1" | awk '{print $1}'
    else
        shasum -a 256 "$1" | awk '{print $1}'
    fi
}

psql_in() {
    local db=$1 sql=$2
    docker exec -i "${DB_CONTAINER}" psql -U "${DB_USER}" -d "${db}" -Atc "${sql}"
}

# 逐表行数快照（排除系统 schema），输出 "schema.table count" 排序后便于 diff。
# 表清单必须取自「被统计的库自身」：连到 postgres 库只能看到 postgres 库的表，
# 会产出空清单并让对比假通过
count_tables() {
    local db=$1 query
    query=$(psql_in "${db}" \
        "SELECT 'SELECT '''||schemaname||'.'||tablename||''' AS t, count(*) FROM '||schemaname||'.'||tablename FROM pg_tables WHERE schemaname NOT IN ('pg_catalog','information_schema')" \
        | awk 'NF { printf "%s union all ", $0 }' | sed 's/ union all $//')
    if [[ -z "${query}" ]]; then
        echo ""
        return 0
    fi
    psql_in "${db}" "${query}" | sed 's/|/ /' | sort
}

# ── 1. 触发真实备份链路 ────────────────────────────────────────────────
log "步骤 1/5：经 ${BACKUP_CONTAINER} 容器触发一次真实备份（BACKUP_ONCE=1）"
T0=$(now)
if docker exec -e BACKUP_ONCE=1 "${BACKUP_CONTAINER}" bash /utils/db_backup.sh; then
    step "触发备份链路" "PASS"
else
    step "触发备份链路" "**FAIL**（见上方日志，演练中止）"
    printf '%s\n' "${STEP_STATUS[@]}"
    exit 1
fi
LATEST=$(ls -t "${BACKUP_DIR}"/*.sql.gz 2>/dev/null | head -n1 || true)
if [[ -z "${LATEST}" ]]; then
    echo "错误: ${BACKUP_DIR} 下未找到任何 .sql.gz 备份包" >&2
    exit 1
fi
log "最新备份包: ${LATEST}"
if [[ -f "${LATEST}.sha256" ]] && [[ "$(sha256_of "${LATEST}")" == "$(awk '{print $1}' "${LATEST}.sha256")" ]]; then
    step "备份包 sha256 自校验" "PASS"
else
    step "备份包 sha256 自校验" "**FAIL**"
fi

# ── 2. 异地副本校验 ────────────────────────────────────────────────────
log "步骤 2/5：异地副本校验"
REMOTE_RESULT="SKIP（未配置 BACKUP_REMOTE_DIR）"
if [[ -n "${BACKUP_REMOTE_DIR}" ]]; then
    REMOTE_FILE="${BACKUP_REMOTE_DIR}/$(basename "${LATEST}")"
    if [[ ! -f "${REMOTE_FILE}" ]]; then
        REMOTE_RESULT="**FAIL**（异地缺少 ${REMOTE_FILE}）"
    else
        LOCAL_SUM=$(sha256_of "${LATEST}")
        REMOTE_SUM=$(sha256_of "${REMOTE_FILE}")
        if [[ "${LOCAL_SUM}" == "${REMOTE_SUM}" ]]; then
            REMOTE_RESULT="PASS（sha256 一致）"
        else
            REMOTE_RESULT="**FAIL**（sha256 不一致：本地 ${LOCAL_SUM:0:12}… / 异地 ${REMOTE_SUM:0:12}…）"
        fi
    fi
fi
step "异地副本存在且一致" "${REMOTE_RESULT}"

# ── 3. 恢复到独立验证库 ────────────────────────────────────────────────
log "步骤 3/5：恢复到验证库 ${TARGET_DB}"
T1=$(now)
YES_I_KNOW=1 sh "${BASE_DIR}/utils/db_restore.sh" "${LATEST}" "${TARGET_DB}" > /dev/null
T2=$(now)
RTO=$(elapsed "${T1}" "${T2}")
step "恢复耗时 RTO" "${RTO}s（目标 ≤30min）"

# ── 4. 一致性对比 ──────────────────────────────────────────────────────
log "步骤 4/5：源库 vs 验证库 逐表行数对比"
SRC_FILE=$(mktemp)
DST_FILE=$(mktemp)
count_tables "${SOURCE_DB}" > "${SRC_FILE}"
count_tables "${TARGET_DB}" > "${DST_FILE}"
DIFF_OUT=$(diff "${SRC_FILE}" "${DST_FILE}" || true)
TABLE_TOTAL=$(grep -c . "${SRC_FILE}" || true)
rm -f "${SRC_FILE}" "${DST_FILE}"
if [[ "${TABLE_TOTAL}" == "0" ]]; then
    # 空清单必然是取表失败（连错库/权限），绝不能记为通过
    step "逐表行数一致" "**FAIL**（源库返回 0 张表，对比无效）"
elif [[ -z "${DIFF_OUT}" ]]; then
    step "逐表行数一致" "PASS（${TABLE_TOTAL} 表 0 不一致）"
else
    DIFF_TABLES=$(printf '%s\n' "${DIFF_OUT}" | grep -c '^[<>]' || true)
    step "逐表行数一致" "**FAIL**（${DIFF_TABLES} 处不一致，多为备份后源库持续写入，见下方差量）"
fi

# ── 5. 清理与报告 ──────────────────────────────────────────────────────
if [[ "${KEEP_VERIFY_DB}" != "1" ]]; then
    log "步骤 5/5：DROP 验证库 ${TARGET_DB}"
    psql_in postgres "DROP DATABASE IF EXISTS \"${TARGET_DB}\";" > /dev/null
else
    log "步骤 5/5：KEEP_VERIFY_DB=1，保留验证库 ${TARGET_DB}"
fi

T_END=$(now)
echo
echo "## 演练结果（$(date '+%F %T')）"
echo
echo "| 项 | 结果 |"
echo "|------|------|"
printf '%s\n' "${STEP_STATUS[@]}"
echo
echo "- 备份包：\`$(basename "${LATEST}")\`（$(du -h "${LATEST}" | cut -f1)）"
echo "- 备份目录：\`${BACKUP_DIR}\`"
echo "- 异地副本：\`${BACKUP_REMOTE_DIR:-未配置}\`"
echo "- 验证库：\`${TARGET_DB}\`（源库 \`${SOURCE_DB}\`）"
echo "- 全流程耗时：$(elapsed "${T0}" "${T_END}")s"
if [[ -n "${DIFF_OUT}" ]]; then
    echo
    echo "<details><summary>逐表行数差量</summary>"
    echo
    echo '```diff'
    printf '%s\n' "${DIFF_OUT}"
    echo '```'
    echo
    echo "</details>"
fi
