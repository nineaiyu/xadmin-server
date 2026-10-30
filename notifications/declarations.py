#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""notifications app 的权限同步声明：消息模板端点的父菜单归属。"""

from common.core.declarations import AppDeclarations

DECLARATIONS = AppDeclarations(
    app="notifications",
    # 消息模板端点无同源权限点，挂「消息设置」页面菜单
    parent_menu_map={"api/notifications/message-templates": "SettingMessage"},
)
