#!/bin/bash


ops_dir=$(dirname "$(readlink -f "$0")")
cd "$(dirname "${ops_dir}")" || exit 1

for d in *;do
    if [ -d "$d" ] && [ -d "$d"/migrations ];then
        rm -f "$d"/migrations/00*
    fi
done

