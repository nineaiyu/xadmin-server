#!/bin/bash

# 框架内核为工作区成员：源码在 /data/xadmin-server/packages/xadmin-common/，
# 运行期经 PYTHONPATH 引入（镜像 ENV 同口径）。放在 entrypoint 里可让
# bind mount 部署「重启容器即生效」，无需重建镜像。
export PYTHONPATH="/data/xadmin-server/packages/xadmin-common${PYTHONPATH:+:${PYTHONPATH}}"

function cleanup()
{
    local pids
    pids=$(jobs -p)
    if [[ "${pids}" != ""  ]]; then
        kill "${pids}" >/dev/null 2>/dev/null
    fi
}

action="${1-start}"
service="${2-all}"

trap cleanup EXIT

# 语言包：.mo 是 gitignore 项（不随仓库分发），缺失或落后于 .po 时按需编译（幂等）。
# 跳过该步骤会让界面文案回退英文；无写权限（bind mount 属主不符）时仅告警，不阻断启动。
ensure_locale() {
    local po mo
    for po in /data/xadmin-server/locale/*/LC_MESSAGES/django.po; do
        [ -f "${po}" ] || continue
        mo="${po%.po}.mo"
        if [ ! -f "${mo}" ] || [ "${po}" -nt "${mo}" ]; then
            # 临时文件 + mv 原子替换：web/task/beat 容器可能同时启动并编译同一 po
            if msgfmt -o "${mo}.tmp" "${po}" 2>/dev/null && mv -f "${mo}.tmp" "${mo}"; then
                echo "locale compiled: ${po}"
            else
                rm -f "${mo}.tmp"
                echo "WARN: locale compile failed (check directory write permission): ${po}"
            fi
        fi
    done
}

if [[ "$action" != "bash" && "$action" != "sh" ]]; then
    ensure_locale
fi

# 一次性迁移动作（`command: ["migrate"]`）：在独立容器里跑完迁移即退出。
# 用途：多副本/滚动发布前的显式迁移步骤（web 侧配 AUTO_MIGRATE=false 即不再自动迁移），
# 也用于发布窗口内的手工迁移。必须**先于**下面的 pid 清理返回：service 缺省是 all，
# 清理会删掉其它正在运行容器管理的 pid 文件（见下方注释）。
if [[ "$action" == "migrate" ]]; then
    exec python manage.py migrate --noinput
fi

if [[ "$action" != "bash" && "$action" != "sh" && "$action" != "sleep" ]]; then
    # 只清理当前容器管理的服务 pid：多 worker 容器共享同一 tmp 目录，
    # 全量 rm 会误删其他容器的 pid 文件，导致其 watcher 误判停止而重复拉起同名 worker
    case "$service" in
        all)
            rm -f /data/xadmin-server/tmp/*.pid
            ;;
        task)
            rm -f /data/xadmin-server/tmp/celery_default.pid /data/xadmin-server/tmp/celery_heavy.pid /data/xadmin-server/tmp/beat.pid
            ;;
        web)
            rm -f /data/xadmin-server/tmp/gunicorn.pid /data/xadmin-server/tmp/flower.pid
            ;;
        beat|celery_default|celery_heavy|flower|gunicorn)
            rm -f /data/xadmin-server/tmp/$service.pid
            ;;
    esac
fi

if [[ "${action:0:1}" == "/" ]];then
    "$@"
elif [[ "$action" == "bash" || "$action" == "sh" ]];then
    bash
elif [[ "$action" == "sleep" ]];then
    echo "Sleep 365 days"
    sleep 365d
else
    python manage.py "${action}" "${service}"
fi

