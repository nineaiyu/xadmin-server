#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""
settings app 对外服务契约层。

其他 app 需要使用 settings 的密码策略与安全防护能力时，只允许从本模块导入，
禁止直接 import settings.utils 等内部实现，避免 app 间横向依赖扩散。
"""
from settings.utils.password import check_password_rules, get_password_check_rules
from settings.utils.security import (
    LoginBlockUtil,
    LoginIpBlockUtil,
    MFABlockUtils,
    RegisterBlockUtil,
    ResetBlockUtil,
    SendVerifyCodeBlockUtil,
)

__all__ = [
    "check_password_rules",
    "get_password_check_rules",
    "LoginBlockUtil",
    "LoginIpBlockUtil",
    "MFABlockUtils",
    "RegisterBlockUtil",
    "ResetBlockUtil",
    "SendVerifyCodeBlockUtil",
]
