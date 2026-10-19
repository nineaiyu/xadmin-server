#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""AI 自身能力的动作声明（知识库目录 / 用量自观测，只读）。

由 ``ai.utils.ai_registry`` 自动发现；标注 ``modules=("ai",)``，停用 AI 模块后
目录与执行一起收敛（AI 模块本身可裁剪）。
"""

from django.utils.translation import gettext_lazy as _

from ai.utils.ai_api_actions import IN_QUERY, api_action

AI_ACTIONS = {
    "knowledge.list": api_action(
        key="knowledge.list",
        label=_("List knowledge base documents"),
        description=_("Documents in the AI knowledge base (title, enabled state, source)"),
        method="GET",
        path="/api/ai/knowledge-documents",
        params={"title": {"type": "string", "required": False, "in": IN_QUERY, "description": "Title keyword"}},
        modules=("ai",),
    ),
    "ai.metrics": api_action(
        key="ai.metrics",
        label=_("Show AI assistant usage metrics"),
        description=_("AI assistant usage of recent days (requests, success rate, top users, token usage)"),
        method="GET",
        path="/api/ai/assistant/metrics",
        params={},
        modules=("ai",),
    ),
    "ai_profile.list": api_action(
        key="ai_profile.list",
        label=_("List AI model profiles"),
        description=_("Configured AI model profiles (name, purpose, model, active state)"),
        method="GET",
        path="/api/ai/profiles",
        params={},
        modules=("ai",),
    ),
    "mcp_server.list": api_action(
        key="mcp_server.list",
        label=_("List external MCP servers"),
        description=_("External MCP servers connected to the assistant (name, endpoint, enabled, last sync)"),
        method="GET",
        path="/api/ai/mcp-servers",
        params={},
        modules=("ai",),
    ),
    "knowledge.detail": api_action(
        key="knowledge.detail",
        label=_("Show a knowledge base document"),
        description=_("One knowledge document with content summary, chunk count and source"),
        method="GET",
        path="/api/ai/knowledge-documents/<pk>",
        params={
            "pk": {"type": "pk", "required": True, "in": "path", "description": "Document id (from knowledge.list)"}
        },
        modules=("ai",),
    ),
}
