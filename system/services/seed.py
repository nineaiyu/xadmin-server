#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : seed
# author : ly_13
# date : 2026/09/17
"""内置种子装配（稳定导入门面）。

实现已迁至 :mod:`system.services.seeding`（按职责分文件：路径、清单、发现、规划、校验、
装载、装配、输出）。本模块保留既有导入面不变——管理命令、测试与二开脚本里的
``from system.services.seed import ...`` 无需改动。
"""

from system.services.seeding.rows import (
    _unique_checks as _unique_checks,
)
from system.services.seeding.rows import (
    backfill_null_timestamps as backfill_null_timestamps,
)
from system.services.seeding.rows import (
    build_fixtures as build_fixtures,
)
from system.services.seeding.rows import (
    build_seed_fixtures as build_seed_fixtures,
)
from system.services.seeding.rows import (
    filter_conflicting_rows as filter_conflicting_rows,
)
from system.services.seeding.rows import (
    read_seed_rows as read_seed_rows,
)
from system.services.seeding.rows import (
    read_specs as read_specs,
)
from system.services.seeding.rows import (
    write_seed_rows as write_seed_rows,
)

__all__ = [
    "_unique_checks",
    "backfill_null_timestamps",
    "build_fixtures",
    "build_seed_fixtures",
    "filter_conflicting_rows",
    "read_seed_rows",
    "read_specs",
    "write_seed_rows",
]
