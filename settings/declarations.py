#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""settings app 的权限同步声明：开放平台设置页归属与水印页签权限位。"""

from common.core.declarations import AppDeclarations

DECLARATIONS = AppDeclarations(
    app="settings",
    parent_menu_map={
        # 开放平台设置页（SCIM / 第三方登录）：typed 配置端点无同源权限点，按前缀归属。
        # 值是 (权限码后缀, 父菜单 name)：同一页面下多个视图集需要各自的权限码后缀。
        "api/settings/scim": ("ScimSetting", "SettingOpenPlatform"),
        "api/settings/oauth/providers": ("OAuthProviderSetting", "SettingOpenPlatform"),
        # Webhook 通知渠道（消息设置页新页签）：与 IM 渠道页签同页归属
        "api/settings/notify/webhook": ("WebhookNotifySetting", "SettingMessage"),
    },
    audit_known_duplicates=(
        # 基本设置与水印同端点：水印是独立页签权限位（读写同 `/api/settings/basic`，
        # 后端按 path+method 鉴权为 OR 语义，字段级隔离不做）
        ("api/settings/basic$", "GET"),
        ("api/settings/basic$", "PATCH"),
    ),
)
