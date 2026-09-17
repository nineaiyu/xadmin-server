#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""代码生成器命令入口（实现见同目录 ``_generate_crud`` 包）。

定位：**一次性代码生成**（非运行期脚手架）——把已存在的模型生成为
后端四件套 + 前端页面 + 菜单种子 JSON；详细约定与用法见实现包 docstring
与 `python manage.py generate_crud --help`。

说明：Django 的命令发现（``find_commands``）只识别模块、跳过包目录，
故命令入口保留为单文件模块，实现按职责拆入 ``_generate_crud`` 包
（constants / analysis / merging / renderers），本模块仅做再导出。
"""

from common.management.commands._generate_crud import (
    BLOCK_END,
    BLOCK_START,
    PERMISSION_ACTIONS,
    Command,
)

__all__ = ["BLOCK_END", "BLOCK_START", "PERMISSION_ACTIONS", "Command"]
