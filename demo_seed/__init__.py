#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""演示种子 app（seed_demo_* 命令家族，自 system/approval 迁出）。

无模型、无迁移、无菜单——只承载演示数据的管理命令。注册条件见
server/settings/apps.py：生产环境（非 DEBUG 且未启用 demo app）不注册，
命令从 ``manage.py --help`` 消失；开发 / 测试 / 公开演示部署（XADMIN_APPS
含 demo）注册，命令名与行为与迁出前完全一致。
"""
