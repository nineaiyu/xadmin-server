#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""开发者工具域（代码生成器，自 common 迁出）。

common 是框架层基座，不应携带研发脚手架；``generate_crud`` 代码生成命令
（含 _generate_crud 渲染器包）住本 app。无模型、无迁移、无菜单。system
平台面的代码生成视图（codegen_gui）复用其 Command 入口——生成器与生成物
的消费端同源演进。
"""
