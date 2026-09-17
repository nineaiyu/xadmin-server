#!/usr/bin/env bash
# 容器 OOM 事件告警（A1）：监听 `docker events` 的 `oom` 事件并回调服务端。
#
# 部署位置：Docker 宿主机（需可访问 docker socket 与 xadmin 服务端 HTTP）。
#
#   OPS_ALERT_URL=https://<xadmin-host>/api/common/api/ops-alert \
#   OPS_ALERT_TOKEN=<与系统配置 OPS_ALERT_TOKEN 一致> \
#   ./oom_alert.sh
#
# 用法与约定：
# - 常驻流式监听；docker 守护进程重启/断线自动重连，游标（事件时间纳秒）落状态
#   文件：重连按秒级游标回放、按纳秒去重，保证同一事件只投递一次、不静默漏事件；
# - `OOM_ALERT_ONCE=1`：投递首个事件后退出（验证 / 单次场景）；
# - `OOM_ALERT_CONTAINER=<name>`：只监听指定容器；
# - `OOM_ALERT_STATE=<path>`：游标文件位置（默认 /tmp/xadmin-oom-alert.cursor）；
# - 未配置 URL/TOKEN 时只记录日志不投递（保持纯日志模式）；
# - 告警投递失败只记 WARN，绝不阻塞监听（下一个事件继续）。
set -u

OPS_ALERT_URL=${OPS_ALERT_URL:-}
OPS_ALERT_TOKEN=${OPS_ALERT_TOKEN:-}
OOM_ALERT_CONTAINER=${OOM_ALERT_CONTAINER:-}
OOM_ALERT_ONCE=${OOM_ALERT_ONCE:-0}
OOM_ALERT_STATE=${OOM_ALERT_STATE:-/tmp/xadmin-oom-alert.cursor}

log() { echo "[$(date '+%F %T')] $*"; }
warn() { echo "[$(date '+%F %T')] WARN: $*" >&2; }

# 上报 OOM 事件：告警投递失败只再记一条 WARN，不影响监听主流程
send_alert() {
    local name=$1 image=$2 cid=$3 at=$4
    if [[ -z "${OPS_ALERT_URL}" || -z "${OPS_ALERT_TOKEN}" ]]; then
        return 0
    fi
    if ! command -v curl > /dev/null 2>&1; then
        warn "告警跳过：curl 不可用（container oom ${name}）"
        return 0
    fi
    local payload
    payload=$(printf '{"source":"container-oom","event":"container oom","host":"%s","time":"%s","detail":"container=%s image=%s id=%s"}' \
        "$(hostname 2>/dev/null || echo unknown)" "${at}" "${name}" "${image}" "${cid}")
    if ! curl -sS -m 10 -X POST "${OPS_ALERT_URL}" \
        -H "Content-Type: application/json" \
        -H "User-Agent: xadmin-oom-alert/1.0" \
        -H "X-Ops-Token: ${OPS_ALERT_TOKEN}" \
        -d "${payload}" > /dev/null 2>&1; then
        warn "告警投递失败（监听不受影响）：container oom ${name}"
    fi
    return 0
}

main() {
    if ! command -v docker > /dev/null 2>&1; then
        warn "docker 不可用，无法监听事件"
        return 1
    fi
    if [[ -z "${OPS_ALERT_URL}" || -z "${OPS_ALERT_TOKEN}" ]]; then
        warn "OPS_ALERT_URL / OPS_ALERT_TOKEN 未配置：仅记录日志不投递告警"
    fi
    if [[ ! -w "$(dirname "${OOM_ALERT_STATE}")" ]]; then
        warn "游标文件目录不可写：${OOM_ALERT_STATE}（重连可能重放事件，由服务端节流兜底）"
    fi

    local cursor=0
    [[ -f "${OOM_ALERT_STATE}" ]] && cursor=$(cat "${OOM_ALERT_STATE}" 2>/dev/null || echo 0)
    [[ "${cursor}" =~ ^[0-9]+$ ]] || cursor=0
    log "监听 docker events（event=oom${OOM_ALERT_CONTAINER:+, container=${OOM_ALERT_CONTAINER}}）游标=${cursor}"

    while true; do
        local -a args=(events --filter event=oom --format '{{.TimeNano}}|{{.Actor.ID}}|{{.Actor.Attributes.name}}|{{.Actor.Attributes.image}}')
        # 有游标时按上次事件所在秒回放（纳秒游标去重）；无游标从"现在"开始，不回放历史事件
        if [[ "${cursor}" -gt 0 ]]; then
            args+=(--since "$((cursor / 1000000000))")
        fi
        [[ -n "${OOM_ALERT_CONTAINER}" ]] && args+=(--filter "container=${OOM_ALERT_CONTAINER}")

        while IFS='|' read -r nanos cid name image; do
            [[ -z "${nanos}" ]] && continue
            # 去重：断线重连后 --since 秒级游标会重放同一秒内的事件
            if [[ "${nanos}" -le "${cursor}" ]]; then
                continue
            fi
            cursor=${nanos}
            echo "${cursor}" > "${OOM_ALERT_STATE}" 2> /dev/null || true
            [[ -z "${name}" ]] && name=${cid:-unknown}
            log "OOM 事件：container=${name} image=${image:-unknown} id=${cid:-unknown}"
            send_alert "${name}" "${image:-unknown}" "${cid:-unknown}" "$(date '+%F %T')"
            if [[ "${OOM_ALERT_ONCE}" == "1" ]]; then
                log "OOM_ALERT_ONCE=1：已处理首个事件，退出"
                return 0
            fi
        done < <(docker "${args[@]}" 2> /dev/null)

        # docker events 流结束（守护进程重启 / 网络断线）：短暂等待后重连
        log "docker events 流结束，5s 后重连"
        sleep 5
    done
}

main
