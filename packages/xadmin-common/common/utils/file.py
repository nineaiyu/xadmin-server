#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : file
# author : ly_13
# date : 8/27/2024
import requests


def download_file(src: str, path: str, timeout: tuple[float, float] = (10, 60)) -> None:
    """流式下载 ``src`` 到本地 ``path``。

    默认 10s 建连 / 60s 读间隔超时：无超时的请求会让 worker 在远端不响应时
    无限挂起（历史缺陷）；超时值由调用方按目标体积覆盖。
    """
    with requests.get(src, stream=True, timeout=timeout) as r:
        r.raise_for_status()
        with open(path, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)
