#!/bin/bash

# 项目根定位 + 凭据同步：本脚本是 compose 管理入口，与 dev_up/dev_down 共用同一
# 凭据解析（config.yml 为唯一定义处，自动维护 .env 派生缓存），
# 否则裸 `docker compose` 命令会因 ${DB_PASSWORD:?} 插值失败
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)"
cd "${PROJECT_DIR}" || exit 1
XADMIN_PROJECT_DIR="${PROJECT_DIR}"
. "${PROJECT_DIR}/utils/compose_env.sh"
sync_compose_credentials

action=${1-}
target=${2-}

function usage() {
  echo "xAdmin Server Management Script"
  echo
  echo "Usage: "
  echo "  ./xadmin.sh [COMMAND] [ARGS...]"
  echo "  ./xadmin.sh --help"
  echo
  echo "  start             Start     API Server"
  echo "  stop              Stop      API Server"
  echo "  restart           Restart   API Server"
  echo "  status            Check     API Server"
  echo "  down              Offline   API Server"
  echo
  echo "More Commands: "
  echo "  init_data                Init demo info"
  echo
}

function start_docker() {
  if command -v systemctl&>/dev/null; then
    systemctl daemon-reload
    systemctl enable docker
    systemctl start docker
  fi
  if ! docker ps &>/dev/null; then
    echo "error: docker start failed"
    exit 1
  fi
}

function check_docker_start() {
  if ! which docker &>/dev/null ;then
    echo "error: docker not found, please run install_docker"
    exit 1
  fi
  if ! docker ps &>/dev/null; then
    start_docker
  fi
}

EXE=""

function start() {
  check_docker_start
  ${EXE} up -d
}

function stop() {
  if [[ -n "${target}" ]]; then
    ${EXE} stop "${target}"
    return
  fi
  ${EXE} stop
}

function close() {
  if [[ -n "${target}" ]]; then
    ${EXE} stop "${target}"
    return
  fi
  services="xadmin-server"
  for i in ${services}; do
    ${EXE} stop "${i}"
  done
}

function restart() {
  stop
  echo -e "\n"
  start
}

function init_data() {
  if ${EXE} ps|grep xadmin-server|grep healthy &>/dev/null;then
    docker exec -it xadmin-server sh -c 'python utils/init_data.py'
  else
    echo "error: xadmin-server not healthy"
    exit 1
  fi
}

function main() {

  if [[ "${action}" == "help" || "${action}" == "h" || "${action}" == "-h" || "${action}" == "--help" ]]; then
    echo ""
  else
    EXE="docker compose"
  fi
  case "${action}" in
  start)
    start
    ;;
  restart)
    restart
    ;;
  stop)
    stop
    ;;
  close)
    close
    ;;
  status)
    ${EXE} ps
    ;;
  down)
    if [[ -z "${target}" ]]; then
      ${EXE} down -v
    else
      ${EXE} stop "${target}" && ${EXE} rm -f "${target}"
    fi
    ;;
  init_data)
    init_data
    ;;
  help)
    usage
    ;;
  --help)
    usage
    ;;
  -h)
    usage
    ;;
  *)
    echo "No such command: ${action}"
    usage
    ;;
  esac
}

main "$@"