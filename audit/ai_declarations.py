#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""审计域的 AI 动作声明（操作日志 / 登录日志，只读）。

由 ``ai.services.actions.ai_registry`` 自动发现；执行复用既有日志查询接口，数据域与脱敏
口径随调用者权限链，AI 侧不额外放宽。
"""

from django.utils.translation import gettext_lazy as _

from ai.services.actions.ai_api_actions import IN_QUERY, api_action

AI_ACTIONS = {
    "log.operation": api_action(
        key="log.operation",
        label=_("Search operation logs"),
        description=_("Audit trail of operations (module, operator, status, time); newest first"),
        method="GET",
        path="/api/audit/logs/operation",
        params={
            "module": {"type": "string", "required": False, "in": IN_QUERY, "description": "Module keyword"},
            "creator": {
                "type": "string",
                "required": False,
                "in": IN_QUERY,
                "target": "creator_id",
                "description": "Operator username or id",
            },
        },
    ),
    "log.login": api_action(
        key="log.login",
        label=_("Search login logs"),
        description=_("Login history (user, IP, user agent, success/failure); newest first"),
        method="GET",
        path="/api/audit/logs/login",
        params={
            "username": {
                "type": "string",
                "required": False,
                "in": IN_QUERY,
                "description": "Username keyword (fuzzy)",
            }
        },
    ),
    "log.operation_detail": api_action(
        key="log.operation_detail",
        label=_("Show an operation log entry"),
        description=_("One operation log entry with request payload, response and change details"),
        method="GET",
        path="/api/audit/logs/operation/<pk>",
        params={"pk": {"type": "pk", "required": True, "in": "path", "description": "Log id (from log.operation)"}},
    ),
    "log.mine": api_action(
        key="log.mine",
        label=_("Show my login history"),
        description=_("Login history of the current user (time, IP, device, result)"),
        method="GET",
        path="/api/audit/user/log",
        params={},
    ),
}
