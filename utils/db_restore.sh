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
set -euo pipefail

FILE=${1:?用法: sh utils/db_restore.sh <备份文件.sql.gz> [目标数据库名]}
TARGET_DB=${2:-xadmin}
CONTAINER=${CONTAINER:-xadmin-postgresql}
DB_USER=${DB_USER:-server}

if [ ! -f "${FILE}" ]; then
    echo "错误: 备份文件不存在: ${FILE}" >&2
    exit 1
fi

echo "即将把 ${FILE} 恢复到容器 ${CONTAINER} 的数据库 ${TARGET_DB}（用户 ${DB_USER}）"
echo "警告: 目标库 ${TARGET_DB} 现有数据将被清空重建！"
read -r -p "确认请输入 yes: " confirm
if [ "${confirm}" != "yes" ]; then
    echo "已取消"
    exit 0
fi

# 断开目标库现有连接并重建
docker exec -i "${CONTAINER}" psql -U "${DB_USER}" -d postgres -v ON_ERROR_STOP=1 <<SQL
SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='${TARGET_DB}' AND pid <> pg_backend_pid();
DROP DATABASE IF EXISTS "${TARGET_DB}";
CREATE DATABASE "${TARGET_DB}" OWNER "${DB_USER}";
SQL

# 恢复数据
gunzip -c "${FILE}" | docker exec -i "${CONTAINER}" psql -v ON_ERROR_STOP=1 -U "${DB_USER}" -d "${TARGET_DB}"

echo "恢复完成: ${TARGET_DB}"
