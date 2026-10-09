#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""DB 表名前缀信号（自 server/utils.py 归位）。

``class_prepared`` 在每个模型类装配时触发，按 settings.DB_PREFIX 给 managed 模型
叠加表名前缀（字符串全局前缀 / dict 按 label·app_label 精确或 app 兜底）。

连接时机即本模块首次被 import 的时机——生产 web 入口（server.asgi）在
django.setup() 前经 server.utils 兼容层 import 到本模块，与迁移前行为一致；
前缀部署下新增入口时须保证本模块先于模型装载被 import。
"""

from typing import Any

from django.db import connection
from django.db.backends.utils import truncate_name
from django.db.models.signals import class_prepared

from common.settings_contract import kernel_setting


def add_db_prefix(sender: Any, **kwargs: Any) -> None:
    prefix = kernel_setting("DB_PREFIX")
    meta = sender._meta
    if not meta.managed:
        return
    if isinstance(prefix, dict):
        app_label = meta.app_label.lower()
        if meta.label_lower in prefix:
            prefix = prefix[meta.label_lower]
        elif meta.label in prefix:
            prefix = prefix[meta.label]
        elif app_label in prefix:
            prefix = prefix[app_label]
        else:
            prefix = prefix.get("", None)
    if prefix and not meta.db_table.startswith(prefix):
        meta.db_table = truncate_name(f"{prefix}{meta.db_table}", connection.ops.max_name_length())


class_prepared.connect(add_db_prefix)
