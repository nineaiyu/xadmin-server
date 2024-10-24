#!/bin/bash

PROJECT_DIR=$(dirname "$(readlink -f "$0")")

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
  echo "  install_docker           Install Docker"
  echo
}

EXE=""

function start() {
  pull_images
  ${EXE} up -d
}

function stop() {
  if [[ -n "${target}" ]]; then
    ${EXE} stop "${target}"
    return
  fi
  ${EXE} stop -v
}

function close() {
  if [[ -n "${target}" ]]; then
    ${EXE} stop "${target}"
    return
  fi
  services="xadmin-server xadmin-celery"
  for i in ${services}; do
    ${EXE} stop "${i}"
  done
}

function restart() {
  stop
  echo -e "\n"
  start
}

function pull_images() {
    bash "${PROJECT_DIR}/utils/pull_docker_images.sh"
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
  pull_images)
    pull_images
    ;;
  install_docker)
    bash "${PROJECT_DIR}/utils/install_centos_docker.sh"
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