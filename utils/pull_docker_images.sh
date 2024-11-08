#!/bin/bash

DOCKER_IMAGE_PREFIX="swr.cn-north-4.myhuaweicloud.com/nineaiyu"

images="xadmin-mariadb:11.5.2 xadmin-redis:7.4.1 python:3.12.7-slim xadmin-server:4.1.5"
for image in ${images};do
  if ! docker images --format "{{.Repository}}:{{.Tag}}" |grep "^${image}" &>/dev/null;then
    full_image_path="${DOCKER_IMAGE_PREFIX}/${image}"
    docker pull  "${full_image_path}"
    docker tag "${full_image_path}" "${image}"
    docker rmi -f "${full_image_path}"
  fi
done
