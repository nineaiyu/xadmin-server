#!/bin/bash
# PostgreSQL 备份恢复脚本（宿主机执行，通过 docker exec 恢复）
#
# 用法:
#   sh utils/db_restore.sh <备份文件.sql.gz> [目标数据库名]
#
# 说明:
# - 默认目标库为 xadmin；恢复前会自动重建目标库（先 DROP 再 CREATE），数据会被覆盖
# - 目标库正在被占用时会先断开其全部连接
# - 示例: sh utils/db_restore.sh ../xadmin-db-backups/xadmin_20260904_120000.sql.gz
#
# 非交互（演练/自动化）：
#   YES_I_KNOW=1 跳过「确认请输入 yes」提示（utils/backup_drill.sh 依赖）
#
# 媒体目录恢复（可选，默认关闭）：
#   RESTORE_MEDIA=1 MEDIA_TARGET=./data/upload 时，若存在同名 .media.tar.gz 会一并解包
set -euo pipefail

FILE=${1:?用法: sh utils/db_restore.sh <备份文件.sql.gz> [目标数据库名]}
TARGET_DB=${2:-xadmin}
CONTAINER=${CONTAINER:-xadmin-postgresql}
DB_USER=${DB_USER:-server}
YES_I_KNOW=${YES_I_KNOW:-0}
RESTORE_MEDIA=${RESTORE_MEDIA:-0}
MEDIA_TARGET=${MEDIA_TARGET:-./data/upload}

if [ ! -f "${FILE}" ]; then
    echo "错误: 备份文件不存在: ${FILE}" >&2
    exit 1
fi

echo "即将把 ${FILE} 恢复到容器 ${CONTAINER} 的数据库 ${TARGET_DB}（用户 ${DB_USER}）"
echo "警告: 目标库 ${TARGET_DB} 现有数据将被清空重建！"
if [ "${YES_I_KNOW}" = "1" ]; then
    echo "YES_I_KNOW=1，跳过交互确认"
else
    read -r -p "确认请输入 yes: " confirm
    if [ "${confirm}" != "yes" ]; then
        echo "已取消"
        exit 0
    fi
fi

# 亚秒计时：GNU date 支持 %N，BSD/macOS 的 date 不支持（会输出字面量 N），故兜底 python3
now() {
    local t
    t=$(date +%s.%N 2>/dev/null || true)
    if [[ "${t}" =~ ^[0-9]+\.[0-9]+$ ]]; then
        echo "${t}"
    else
        python3 -c 'import time; print(time.time())' 2>/dev/null || date +%s
    fi
}

START_TS=$(now)

# 断开目标库现有连接并重建
docker exec -i "${CONTAINER}" psql -U "${DB_USER}" -d postgres -v ON_ERROR_STOP=1 <<SQL
SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='${TARGET_DB}' AND pid <> pg_backend_pid();
DROP DATABASE IF EXISTS "${TARGET_DB}";
CREATE DATABASE "${TARGET_DB}" OWNER "${DB_USER}";
SQL

# 恢复数据
gunzip -c "${FILE}" | docker exec -i "${CONTAINER}" psql -v ON_ERROR_STOP=1 -U "${DB_USER}" -d "${TARGET_DB}"

END_TS=$(now)
ELAPSED=$(awk -v s="${START_TS}" -v e="${END_TS}" 'BEGIN { printf "%.2f", e - s }')
echo "数据库恢复完成: ${TARGET_DB}（耗时 ${ELAPSED}s）"

# 媒体目录恢复（可选）：解包时保留既有文件，仅覆盖同名文件
if [ "${RESTORE_MEDIA}" = "1" ]; then
    MEDIA_PKG="${FILE%.sql.gz}.media.tar.gz"
    if [ ! -f "${MEDIA_PKG}" ]; then
        echo "警告: RESTORE_MEDIA=1 但未找到媒体包 ${MEDIA_PKG}（跳过）" >&2
    else
        mkdir -p "${MEDIA_TARGET}"
        tar -xzf "${MEDIA_PKG}" -C "${MEDIA_TARGET}" --strip-components=1
        echo "媒体目录恢复完成: ${MEDIA_TARGET}"
    fi
fi
