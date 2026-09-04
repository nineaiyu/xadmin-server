#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""
system app 对外服务契约层。

其他 app 需要使用 system 的业务能力时，只允许从本模块导入，
禁止直接 import system.views / system.serializers 等内部实现，
避免 app 间横向依赖扩散。
"""
from system.views.auth.login import login_success

__all__ = ["login_success"]
