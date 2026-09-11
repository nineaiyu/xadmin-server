#!/bin/bash
# PostgreSQL 定时备份脚本（在 postgres 镜像容器内运行，由 docker-compose db-backup 服务挂载）
#
# 备份能力：
#   1. 数据库：pg_dump | gzip -> <库名>_<时间戳>.sql.gz，并生成 .sha256 校验和 sidecar
#   2. 媒体目录：BACKUP_MEDIA=true 时把 MEDIA_DIR 打包为 <同名>.media.tar.gz
#   3. 异地副本：BACKUP_REMOTE_TYPE=local|rsync|rclone 同步到 BACKUP_REMOTE_TARGET
#   4. RPO：默认 BACKUP_INTERVAL=21600（6h，原 24h）
#
# 单次模式：BACKUP_ONCE=1 只跑一轮即退出（utils/backup_drill.sh 演练、单测、外部 cron 用）
# 产出标记：每轮成功后写 ${BACKUP_DIR}/.latest_backup（首行为 sql.gz 路径，次行为媒体包路径）
set -euo pipefail

BACKUP_DIR=${BACKUP_DIR:-/backups}
KEEP_DAYS=${KEEP_DAYS:-7}
# RPO 目标 6h（原 24h）；仍需更短窗口时改走 WAL 归档/PITR
BACKUP_INTERVAL=${BACKUP_INTERVAL:-21600}
PGHOST=${PGHOST:-postgresql}
PGPORT=${PGPORT:-5432}
PGUSER=${PGUSER:-server}
PGDATABASE=${PGDATABASE:-xadmin}

# 媒体目录（可选，compose 只读挂载 ./data/upload:/media）
BACKUP_MEDIA=${BACKUP_MEDIA:-false}
MEDIA_DIR=${MEDIA_DIR:-/media}

# 异地副本（可选；未配置时跳过，不阻断本地备份）
#   local -> cp 到本地/挂载点目录
#   rsync -> rsync -a 到 user@host:/path
#   rclone-> rclone copy 到对象存储 remote（凭据在宿主机 rclone config 侧配置，不入仓库）
BACKUP_REMOTE_TYPE=${BACKUP_REMOTE_TYPE:-}
BACKUP_REMOTE_TARGET=${BACKUP_REMOTE_TARGET:-}
BACKUP_REMOTE_KEEP_DAYS=${BACKUP_REMOTE_KEEP_DAYS:-${KEEP_DAYS}}

BACKUP_ONCE=${BACKUP_ONCE:-0}

# 失败告警回调（可选；S2）：向服务端 POST 失败事件，由服务端节流（60s 同源去重）
# 后发站内信/邮件给超管。未配置 URL/TOKEN 时静默跳过（保持纯日志模式）。
BACKUP_ALERT_URL=${BACKUP_ALERT_URL:-}
BACKUP_ALERT_TOKEN=${BACKUP_ALERT_TOKEN:-}

LAST_BASE=""

log() { echo "[$(date '+%F %T')] $*"; }
warn() { echo "[$(date '+%F %T')] WARN: $*" >&2; }

# 上报失败事件：告警投递失败只再记一条 WARN，绝不影响备份主流程
send_alert() {
    local event=$1 detail=${2:-}
    if [[ -z "${BACKUP_ALERT_URL}" || -z "${BACKUP_ALERT_TOKEN}" ]]; then
        return 0
    fi
    if ! command -v curl > /dev/null 2>&1; then
        warn "报警跳过：curl 不可用（${event}）"
        return 0
    fi
    local payload
    payload=$(printf '{"source":"db-backup","event":"%s","host":"%s","time":"%s","detail":"%s"}' \
        "${event}" "$(hostname 2>/dev/null || echo unknown)" "$(date '+%F %T')" "${detail}")
    if ! curl -sS -m 10 -X POST "${BACKUP_ALERT_URL}" \
        -H "Content-Type: application/json" \
        -H "User-Agent: xadmin-db-backup/1.0" \
        -H "X-Backup-Token: ${BACKUP_ALERT_TOKEN}" \
        -d "${payload}" > /dev/null 2>&1; then
        warn "告警投递失败（备份流程不受影响）：${event}"
    fi
}

# sha256sum 在 Debian 系容器存在，macOS 只有 shasum（本机演练/单测需要兜底）
sha256_of() {
    if command -v sha256sum > /dev/null 2>&1; then
        sha256sum "$1" | awk '{print $1}'
    else
        shasum -a 256 "$1" | awk '{print $1}'
    fi
}

write_checksum() {
    printf '%s  %s\n' "$(sha256_of "$1")" "$(basename "$1")" > "${1}.sha256"
}

# 落盘后校验：非空 + gzip 结构完整，避免把损坏包同步到异地
verify_archive() {
    local file=$1
    if [[ ! -s "${file}" ]]; then
        warn "备份文件为空: ${file}"
        return 1
    fi
    if ! gzip -t "${file}" > /dev/null 2>&1; then
        warn "备份文件 gzip 校验失败: ${file}"
        return 1
    fi
    return 0
}

sync_remote() {
    local file=$1
    local files=("${file}")
    [[ -f "${file}.sha256" ]] && files+=("${file}.sha256")

    if [[ -z "${BACKUP_REMOTE_TYPE}" || -z "${BACKUP_REMOTE_TARGET}" ]]; then
        return 0
    fi

    case "${BACKUP_REMOTE_TYPE}" in
        local)
            mkdir -p "${BACKUP_REMOTE_TARGET}"
            cp -f "${files[@]}" "${BACKUP_REMOTE_TARGET}/"
            ;;
        rsync)
            # shellcheck disable=SC2086
            rsync -a "${files[@]}" "${BACKUP_REMOTE_TARGET}/"
            ;;
        rclone)
            local f
            for f in "${files[@]}"; do
                rclone copy "${f}" "${BACKUP_REMOTE_TARGET}"
            done
            ;;
        *)
            warn "未知 BACKUP_REMOTE_TYPE=${BACKUP_REMOTE_TYPE}（支持 local|rsync|rclone），跳过异地同步"
            return 0
            ;;
    esac
    log "异地副本同步完成: ${file} -> ${BACKUP_REMOTE_TYPE}:${BACKUP_REMOTE_TARGET}"
}

do_backup() {
    local stamp base file tmp
    stamp=$(date +%Y%m%d_%H%M%S)
    base="${BACKUP_DIR}/${PGDATABASE}_${stamp}"
    file="${base}.sql.gz"
    tmp="${file}.tmp"
    log "start backup -> ${file}"
    # pg_dump 失败时 pipefail 会让整条管道返回非零，临时文件不落正式名
    if pg_dump -h "${PGHOST}" -p "${PGPORT}" -U "${PGUSER}" -d "${PGDATABASE}" --no-owner | gzip > "${tmp}"; then
        mv "${tmp}" "${file}"
    else
        warn "backup FAILED, remove partial file"
        rm -f "${tmp}"
        send_alert "pg_dump failed" "database=${PGDATABASE} host=${PGHOST}:${PGPORT}"
        return 1
    fi
    if ! verify_archive "${file}"; then
        rm -f "${file}"
        send_alert "backup archive verify failed" "file=${file}"
        return 1
    fi
    write_checksum "${file}"
    log "backup done: $(du -h "${file}" | cut -f1)"
    LAST_BASE="${base}"
    printf '%s\n' "${file}" > "${BACKUP_DIR}/.latest_backup"
    if ! sync_remote "${file}"; then
        warn "异地同步失败（本地备份仍有效）: ${file}"
        send_alert "remote sync failed" "file=${file} target=${BACKUP_REMOTE_TYPE}:${BACKUP_REMOTE_TARGET}"
    fi
}

# 媒体目录打包：目录缺失仅告警，不阻断数据库备份
backup_media() {
    local base=$1 file tmp
    if [[ "${BACKUP_MEDIA}" != "true" ]]; then
        return 0
    fi
    if [[ ! -d "${MEDIA_DIR}" ]]; then
        warn "BACKUP_MEDIA=true 但媒体目录不存在: ${MEDIA_DIR}（跳过媒体备份）"
        return 0
    fi
    file="${base}.media.tar.gz"
    tmp="${file}.tmp"
    log "start media backup -> ${file}"
    if tar -czf "${tmp}" -C "$(dirname "${MEDIA_DIR}")" "$(basename "${MEDIA_DIR}")"; then
        mv "${tmp}" "${file}"
    else
        warn "媒体目录打包失败: ${MEDIA_DIR}"
        rm -f "${tmp}"
        send_alert "media archive failed" "media_dir=${MEDIA_DIR}"
        return 1
    fi
    if ! verify_archive "${file}"; then
        rm -f "${file}"
        send_alert "media archive verify failed" "file=${file}"
        return 1
    fi
    write_checksum "${file}"
    log "media backup done: $(du -h "${file}" | cut -f1)"
    printf '%s\n' "${file}" >> "${BACKUP_DIR}/.latest_backup"
    if ! sync_remote "${file}"; then
        warn "媒体包异地同步失败（本地备份仍有效）: ${file}"
        send_alert "media remote sync failed" "file=${file} target=${BACKUP_REMOTE_TYPE}:${BACKUP_REMOTE_TARGET}"
    fi
}

prune_local() {
    find "${BACKUP_DIR}" -type f \( -name '*.sql.gz' -o -name '*.media.tar.gz' -o -name '*.sha256' \) -mtime +"${KEEP_DAYS}" -delete
}

# 异地保留期只对 local 类型生效：rsync/rclone 远端不可控，删错代价大，交由远端生命周期策略
prune_remote() {
    if [[ "${BACKUP_REMOTE_TYPE}" == "local" && -n "${BACKUP_REMOTE_TARGET}" && -d "${BACKUP_REMOTE_TARGET}" ]]; then
        find "${BACKUP_REMOTE_TARGET}" -type f \( -name '*.sql.gz' -o -name '*.media.tar.gz' -o -name '*.sha256' \) -mtime +"${BACKUP_REMOTE_KEEP_DAYS}" -delete
    fi
}

mkdir -p "${BACKUP_DIR}"
log "db-backup started (interval=${BACKUP_INTERVAL}s keep=${KEEP_DAYS}d media=${BACKUP_MEDIA} remote=${BACKUP_REMOTE_TYPE:-none} once=${BACKUP_ONCE})"
FAILED=0
while true; do
    if do_backup; then
        backup_media "${LAST_BASE}" || warn "本轮媒体备份未成功（数据库备份不受影响）"
    else
        FAILED=1
        warn "本轮数据库备份失败，详见上方日志"
    fi
    prune_local
    prune_remote
    if [[ "${BACKUP_ONCE}" == "1" ]]; then
        log "BACKUP_ONCE=1，单轮备份结束"
        break
    fi
    sleep "${BACKUP_INTERVAL}"
done

# 单次模式（演练/外部 cron）必须把失败暴露为退出码，否则调度侧无法感知；
# 常驻循环模式继续存活等待下一轮，避免容器抖动重启
if [[ "${FAILED}" == "1" && "${BACKUP_ONCE}" == "1" ]]; then
    exit 1
fi
