#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""AI 声明式动作：基础设施域（系统配置 / 字典 / 日志 / 文件 / 知识库 / Webhook）。

从 ``ai_api_registry.py`` 拆出仅因行数门禁：本文件只做声明，机制（``api_action`` /
参数解析 / dispatch 执行 / 权限双门）全部在 ``ai_api_actions.py``，汇总仍以
``ai_api_registry.API_ACTION_SPECS`` 为唯一白名单入口。

高危动作（requires_approval）：config.set——系统配置影响平台行为，AI 修改必须
走 412 审批协议（审批通过后由审批穿透机制放行业务层拦截器）。
"""

from django.utils.translation import gettext_lazy as _

from ai.utils.ai_api_actions import IN_QUERY, api_action, requires_approval_high_risk

INFRA_ACTION_SPECS = {
    # ---- 系统配置（config.list 只读；config.set 高危强制审批） ----
    "config.list": api_action(
        key="config.list",
        label=_("List system configurations"),
        description=_("List system config entries (key, value, description); read-only view of platform settings"),
        method="GET",
        path="/api/system/config/system",
        params={"key": {"type": "string", "required": False, "in": IN_QUERY, "description": "Config key keyword"}},
    ),
    "config.set": api_action(
        key="config.set",
        label=_("Update a system configuration value"),
        description=_(
            "Change one system config entry's value (requires approval). Pass the value as JSON text "
            "exactly shaped like the current value from config.list; wrong shape may break the feature"
        ),
        method="PATCH",
        path="/api/system/config/system/<pk>",
        params={
            "pk": {"type": "pk", "required": True, "in": "path", "description": "Config row id (from config.list)"},
            "value": {
                "type": "string",
                "required": True,
                "in": "body",
                "description": "New value as JSON text (same shape as the current value)",
            },
        },
        requires_approval=requires_approval_high_risk,
    ),
    # ---- 数据字典（dict.items 走 PERMISSION_WHITE_URL，无菜单权限点 → AI 侧
    # user_can_visit fail-closed，实际仅超管可用，可接受） ----
    "dict.list": api_action(
        key="dict.list",
        label=_("List dictionary types"),
        description=_("List data dictionary types (code, name, item count)"),
        method="GET",
        path="/api/system/dict",
        params={"code": {"type": "string", "required": False, "in": IN_QUERY, "description": "Type code keyword"}},
    ),
    "dict.items": api_action(
        key="dict.items",
        label=_("Show dictionary items"),
        description=_("Enabled items of one dictionary type by its code (labels used across the system)"),
        method="GET",
        path="/api/system/dict/items",
        params={
            "code": {
                "type": "string",
                "required": True,
                "in": IN_QUERY,
                "description": "Dictionary type code (from dict.list)",
            }
        },
    ),
    # ---- 审计日志（只读） ----
    "log.operation": api_action(
        key="log.operation",
        label=_("Search operation logs"),
        description=_("Audit trail of operations (module, operator, status, time); newest first"),
        method="GET",
        path="/api/system/logs/operation",
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
        path="/api/system/logs/login",
        params={
            "username": {
                "type": "string",
                "required": False,
                "in": IN_QUERY,
                "description": "Username keyword (fuzzy)",
            }
        },
    ),
    # ---- 文件与下载中心 ----
    "file.list": api_action(
        key="file.list",
        label=_("List uploaded files"),
        description=_("List uploaded files (name, size, uploader)"),
        method="GET",
        path="/api/system/file",
        params={"name": {"type": "string", "required": False, "in": IN_QUERY, "description": "File name keyword"}},
    ),
    "file.stats": api_action(
        key="file.stats",
        label=_("Show file storage stats"),
        description=_("File storage statistics (counts, sizes by type)"),
        method="GET",
        path="/api/system/file/stats",
        params={},
    ),
    "export.list": api_action(
        key="export.list",
        label=_("List export records"),
        description=_("Async export records in the download center (file, status, creator)"),
        method="GET",
        path="/api/system/exports",
        params={},
    ),
    # ---- AI 自身（知识库目录 + 用量自观测） ----
    "knowledge.list": api_action(
        key="knowledge.list",
        label=_("List knowledge base documents"),
        description=_("Documents in the AI knowledge base (title, enabled state, source)"),
        method="GET",
        path="/api/system/ai/knowledge-documents",
        params={"title": {"type": "string", "required": False, "in": IN_QUERY, "description": "Title keyword"}},
    ),
    "ai.metrics": api_action(
        key="ai.metrics",
        label=_("Show AI assistant usage metrics"),
        description=_("AI assistant usage of recent days (requests, success rate, top users, token usage)"),
        method="GET",
        path="/api/system/ai/assistant/metrics",
        params={},
    ),
    # ---- Webhook（只读） ----
    "webhook.list": api_action(
        key="webhook.list",
        label=_("List webhook subscriptions"),
        description=_("Outbound webhook subscriptions (target URL, subscribed events, enabled state)"),
        method="GET",
        path="/api/system/webhooks/subscriptions",
        params={},
    ),
}
