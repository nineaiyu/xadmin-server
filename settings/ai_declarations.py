#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""系统设置域的 AI 动作声明（只读）。

只暴露与安全策略/登录限制相关的读取能力，便于对话中解释「为什么登录被拦」「密码
规则是什么」。邮件/短信/LDAP 凭据类配置的读取不纳入（避免密钥面进入对话与审计），
写侧一律走设置页面人工确认。
"""

from django.utils.translation import gettext_lazy as _

from ai.services.actions.ai_api_actions import api_action

AI_ACTIONS = {
    "setting.login_limit": api_action(
        key="setting.login_limit",
        label=_("Show login rate-limit settings"),
        description=_("Login attempt limits and lockout policy (thresholds and windows)"),
        method="GET",
        path="/api/settings/login/limit",
        params={},
    ),
    "setting.password_rule": api_action(
        key="setting.password_rule",
        label=_("Show password rules"),
        description=_("Password strength policy (length, character classes, history and expiry)"),
        method="GET",
        path="/api/settings/password",
        params={},
    ),
    "setting.mfa": api_action(
        key="setting.mfa",
        label=_("Show MFA policy"),
        description=_("Multi-factor authentication policy (enabled factors and required scope)"),
        method="GET",
        path="/api/settings/mfa/auth",
        params={},
    ),
    "setting.ip_block": api_action(
        key="setting.ip_block",
        label=_("List blocked IPs"),
        description=_("IP addresses blocked by the security policy (reason, block time)"),
        method="GET",
        path="/api/settings/ip/block",
        params={},
    ),
    "setting.ldap": api_action(
        key="setting.ldap",
        label=_("Show LDAP directory settings"),
        description=_("LDAP/AD directory sync configuration status (server, base DN, sync state; no credentials)"),
        method="GET",
        path="/api/settings/ldap",
        params={},
        modules=("ldap",),
    ),
}
