#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""
settings app 对外服务契约层。

其他 app 需要使用 settings 的密码策略与安全防护能力时，只允许从本模块导入，
禁止直接 import settings.utils 等内部实现，避免 app 间横向依赖扩散。
"""

from settings.models import Setting
from settings.utils.password import (
    PASSWORD_EXPIRED_MESSAGE,
    check_history_password,
    check_leak_password,
    check_password_rules,
    get_password_check_rules,
    is_password_expired,
    record_password_hash,
)
from settings.utils.security import (
    LoginBlockUtil,
    LoginIpBlockUtil,
    MFABlockUtils,
    RegisterBlockUtil,
    ResetBlockUtil,
    SendVerifyCodeBlockUtil,
)

__all__ = [
    # 模型契约（启动自检等迁移就绪探测场景）
    "Setting",
    "check_password_rules",
    "check_leak_password",
    "check_history_password",
    "record_password_hash",
    "is_password_expired",
    "PASSWORD_EXPIRED_MESSAGE",
    "get_password_check_rules",
    "LoginBlockUtil",
    "LoginIpBlockUtil",
    "MFABlockUtils",
    "RegisterBlockUtil",
    "ResetBlockUtil",
    "SendVerifyCodeBlockUtil",
]


def __getattr__(name):
    # 惰性再导出（视图模块导入较重，启动期不需要）
    _lazy = {
        "AiAssistantSettingSerializer": "settings.serializers.ai",
        "BaseSettingViewSet": "settings.views.settings",
    }
    if name in _lazy:
        from importlib import import_module

        value = getattr(import_module(_lazy[name]), name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
