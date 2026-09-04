#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""
message app 对外服务契约层。

其他 app 需要使用 message 的业务能力时，只允许从本模块导入，
禁止直接 import message.utils 等内部实现，避免 app 间横向依赖扩散。
"""
from message.utils import (
    get_online_info,
    get_online_user_layers,
    get_online_users_layers,
    send_logout_msg,
)

__all__ = ["send_logout_msg", "get_online_user_layers", "get_online_users_layers", "get_online_info"]
