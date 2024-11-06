#!/bin/bash

DOCKER_IMAGE_PREFIX="swr.cn-north-4.myhuaweicloud.com/nineaiyu"

platform="linux/amd64,linux/arm64"

function build_image() {
    key="$1"
    path="$2"
    docker buildx use xadmin-builder
    docker buildx build -t "${DOCKER_IMAGE_PREFIX}/${key}" --platform  "${platform}" --push --provenance=false "${path}"
}


if ! docker buildx ls |grep xadmin-builder &>/dev/null;then
    docker buildx create --platform "${platform}" --name xadmin-builder --driver docker-container --buildkitd-config buildkitd.toml --bootstrap --use
fi


build_image "node:22.11.0-slim" "node"
build_image "python:3.12.7-slim" "python"
build_image "xadmin-redis:7.4.1" "redis"
build_image "xadmin-mariadb:11.5.2" "mariadb"
build_image "xadmin-server:4.1.2" "../../"
build_image "xadmin-node:22.11.0-slim" "../../../xadmin-client/"
