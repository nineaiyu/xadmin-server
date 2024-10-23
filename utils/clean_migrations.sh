#!/bin/bash


utils_dir=$(pwd)
cd "$(dirname "${utils_dir}")" || exit 1

for d in *;do
    if [ -d "$d" ] && [ -d "$d"/migrations ];then
        rm -f "$d"/migrations/00*
    fi
done

