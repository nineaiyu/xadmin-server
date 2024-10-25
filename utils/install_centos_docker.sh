#!/bin/bash

utils_dir=$(dirname "$(readlink -f "$0")")

if which docker &>/dev/null ;then
  yum remove -y docker \
    docker-client \
    docker-client-latest \
    docker-common \
    docker-latest \
    docker-latest-logrotate \
    docker-logrotate \
    docker-engine
fi
if ! which docker &>/dev/null ;then
  yum install -y yum-utils  \
      && yum-config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo  \
      && sed -i 's+https://download.docker.com+https://mirrors.tuna.tsinghua.edu.cn/docker-ce+' /etc/yum.repos.d/docker-ce.repo  \
      && yum install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin  \
      && systemctl restart docker
fi

project_parent_dir=$(dirname "$(dirname "${utils_dir}")")

mkdir -pv "${project_parent_dir}"/xadmin-mariadb/{data,logs}
chown 1001.1001 -R "${project_parent_dir}"/xadmin-mariadb/{data,logs}
chown 1001.1001 -R "${project_parent_dir}"/xadmin-server/* # 为了安全考虑，容器使用非root用户启动服务
