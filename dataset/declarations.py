#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""dataset app 的权限同步声明：表单数据端点父菜单归属与大屏指令共享权限码。"""

from common.core.declarations import AppDeclarations

DECLARATIONS = AppDeclarations(
    app="dataset",
    parent_menu_map={
        "api/dataset/dynamic-form-submissions": "FormMySubmission",
        "api/dataset/form-data": "FormData",
    },
    shared_method_paths={
        # screens/{pk}/command GET+POST：查询控制态 / 下发控制指令共用 command:DataScreen 权限码
        "api/dataset/screens/(?P<pk>[^/.]+)/command$": ("GET", "POST"),
    },
)
