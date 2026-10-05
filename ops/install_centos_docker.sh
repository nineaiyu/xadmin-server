#!/bin/bash

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
