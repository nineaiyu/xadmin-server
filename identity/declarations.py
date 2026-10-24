#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""identity app 的权限同步声明：组织与安全域、开放平台设置页的父菜单归属。"""

from common.core.declarations import AppDeclarations

DECLARATIONS = AppDeclarations(
    app="identity",
    parent_menu_map={
        # 组织与安全域
        "api/identity/posts": "SystemPost",
        "api/identity/account-risks": "SystemAccountRisk",
        "api/identity/login-policies": "SystemLoginPolicy",
        # 开放平台设置页（第三方登录授权应用）：typed 配置端点无同源权限点，按前缀归属。
        # 值是 (权限码后缀, 父菜单 name)：同一页面下多个视图集需要各自的权限码后缀。
        "api/identity/authorized-applications": ("AuthorizedApplication", "SettingOpenPlatform"),
    },
    shared_method_paths={
        # user/{pk}/im-binding GET+POST：管理员代录 IM 身份（查看绑定 / 创建或更新），
        # 前端统一用 `imBinding:SystemUser` 判定入口可见性
        "api/identity/user/(?P<pk>[^/.]+)/im-binding$": ("GET", "POST"),
    },
)
