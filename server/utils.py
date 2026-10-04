#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : utils
# author : ly_13
# date : 10/18/2024
"""兼容 re-export 层（实现已归位，保留一个版本周期）。

实现已迁出：thread-local 请求持有器 → ``common/local.py``；DB 表前缀信号 →
``common/core/db/prefix.py``。common 内已禁止 import server（门禁见
scripts/check_cross_app_imports.py），server 侧既有调用方（middleware / asgi /
system / message / dataset 等）仍经本文件取用；归位模块的 import 副作用
（class_prepared 信号连接）随本文件的 import 链保留，asgi 入口在 django.setup()
前 import 本文件的时机不变。新代码请直接 import 归位模块。
"""

from common.core.db.prefix import add_db_prefix  # noqa: F401
from common.local import get_current_request, set_current_request  # noqa: F401

__all__ = ("add_db_prefix", "get_current_request", "set_current_request")
