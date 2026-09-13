#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""LDAP/AD 目录同步（ADR-017）。

`client.py` 连接与搜索封装；`auth.py` 认证 backend；`sync.py` 同步服务；
`tasks.py` 周期任务入口。配置经 settings app 的 Setting 体系（category=ldap）
热更新到 django.conf.settings，默认值登记在 server/conf.py。
"""
