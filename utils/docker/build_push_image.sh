#!/bin/bash

DOCKER_IMAGE_PREFIX="swr.cn-north-4.myhuaweicloud.com/nineaiyu"

platform="linux/amd64,linux/arm64"

declare -A images

images["node:22.11.0-slim"]="node"
images["python:3.12.7-slim"]="python"
images["xadmin-redis:7.4.1"]="redis"
images["xadmin-mariadb:11.5.2"]="mariadb"
images["xadmin-server:4.1.2"]="../../"


find ./ -name '*.sh' -exec chmod a+x {} \;

if ! docker buildx ls |grep xadmin-builder &>/dev/null;then
    docker buildx create --platform "${platform}" --name xadmin-builder --driver docker-container \
      --buildkitd-config buildkitd.toml --bootstrap --use
fi

for key in ${!images[*]}
do
    docker buildx build -t "${DOCKER_IMAGE_PREFIX}/${key}" --platform  "${platform}" --push --provenance=false "${images[$key]}"
done
