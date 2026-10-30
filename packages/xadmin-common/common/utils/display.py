#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""用户显示名单源（昵称优先、缺失回落用户名）。

此前审批快照 / 通知模板 / 超时升级各写一份 ``nickname or username`` 内联实现，
口径漂移（有人 strip、有人不判 None）。本模块是唯一取值口径；纯鸭子类型，
不 import 任何业务 app（common 是基座）。
"""

from typing import Any


def user_label(user: Any, *, default: str = "") -> str:
    """用户显示名：昵称优先，缺失回落用户名；无用户 / 均为空返回 ``default``。

    :param user: 任意带 ``nickname`` / ``username`` 属性的对象（UserInfo、
        轻量快照对象均适用）；``None`` 安全。
    """
    if user is None:
        return default
    nickname = str(getattr(user, "nickname", "") or "").strip()
    username = str(getattr(user, "username", "") or "").strip()
    return nickname or username or default
