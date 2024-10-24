#!/bin/bash

DOCKER_IMAGE_PREFIX="swr.cn-north-4.myhuaweicloud.com/nineaiyu"
utils_dir=$(dirname "$(readlink -f "$0")")

version=$1
if [ -z "$version" ]; then
  echo "Usage: sh build version"
  exit
fi

SERVER_IMAGE="xadmin-server:${version}"
FULL_SERVER_IMAGE="${DOCKER_IMAGE_PREFIX}/${SERVER_IMAGE}"

cd "$(dirname "${utils_dir}")" \
  && docker build -t xadmin-server:"${version}" . \
  && [ -f ~/.docker/config.json ] && grep "$(dirname ${DOCKER_IMAGE_PREFIX})" ~/.docker/config.json \
  && docker tag "${SERVER_IMAGE}" "${FULL_SERVER_IMAGE}" \
  && docker push "${FULL_SERVER_IMAGE}" \
  && docker rmi -f "${FULL_SERVER_IMAGE}"