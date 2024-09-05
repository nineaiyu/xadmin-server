#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : file
# author : ly_13
# date : 8/27/2024
import requests


def download_file(src, path):
    with requests.get(src, stream=True) as r:
        r.raise_for_status()
        with open(path, 'wb') as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)
