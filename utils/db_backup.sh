#!/bin/bash
# PostgreSQL 定时备份脚本（在 postgres 镜像容器内运行，由 docker-compose db-backup 服务挂载）
# - 启动即备份一次，之后每隔 BACKUP_INTERVAL 秒备份一次
# - gzip 压缩为 <库名>_<时间戳>.sql.gz，滚动保留 KEEP_DAYS 天
set -euo pipefail

BACKUP_DIR=${BACKUP_DIR:-/backups}
KEEP_DAYS=${KEEP_DAYS:-7}
BACKUP_INTERVAL=${BACKUP_INTERVAL:-86400}
PGHOST=${PGHOST:-postgresql}
PGPORT=${PGPORT:-5432}
PGUSER=${PGUSER:-server}
PGDATABASE=${PGDATABASE:-xadmin}

do_backup() {
    local stamp file tmp
    stamp=$(date +%Y%m%d_%H%M%S)
    file="${BACKUP_DIR}/${PGDATABASE}_${stamp}.sql.gz"
    tmp="${file}.tmp"
    echo "[$(date '+%F %T')] start backup -> ${file}"
    if pg_dump -h "${PGHOST}" -p "${PGPORT}" -U "${PGUSER}" -d "${PGDATABASE}" --no-owner | gzip > "${tmp}"; then
        mv "${tmp}" "${file}"
        echo "[$(date '+%F %T')] backup done: $(du -h "${file}" | cut -f1)"
    else
        echo "[$(date '+%F %T')] backup FAILED, remove partial file" >&2
        rm -f "${tmp}"
        return 1
    fi
    # 滚动清理过期备份
    find "${BACKUP_DIR}" -name '*.sql.gz' -mtime +"${KEEP_DAYS}" -delete
}

mkdir -p "${BACKUP_DIR}"
echo "[$(date '+%F %T')] db-backup loop started (interval=${BACKUP_INTERVAL}s keep=${KEEP_DAYS}d)"
while true; do
    do_backup || true
    sleep "${BACKUP_INTERVAL}"
done
