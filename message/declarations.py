#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""message app 的权限同步声明：聊天室（/api/chat/，路由挂本 app）的例外口径。"""

from common.core.declarations import AppDeclarations

DECLARATIONS = AppDeclarations(
    app="message",
    # api/chat/*：不在 PERMISSION_SHOW_PREFIX（框架未纳入菜单生成面），权限点手工维护
    audit_skip_prefixes=("api/chat/",),
    shared_method_paths={
        # chat/room/{pk}/members GET+POST：查看成员 / 增删成员共用 members:ChatRoom 权限码
        "api/chat/room/(?P<pk>[^/.]+)/members$": ("GET", "POST"),
    },
)
