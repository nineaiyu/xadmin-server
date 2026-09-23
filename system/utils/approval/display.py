#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""审批处理人显示名快照工具（U-1）。"""


def user_display(user) -> str:
    """用户显示名（昵称优先，缺失回落用户名）：写入审批痕迹快照的唯一口径。"""
    if user is None:
        return ""
    nickname = str(getattr(user, "nickname", "") or "").strip()
    username = str(getattr(user, "username", "") or "").strip()
    return nickname or username
