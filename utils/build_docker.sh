#!/bin/bash


utils_dir=$(pwd)

version=$1
if [ -z "$version" ]; then
  echo "Usage: sh build version"
  exit
fi

cd "$(dirname "${utils_dir}")" && docker build -t "xadmin-server:${version}" .
