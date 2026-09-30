#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""AI 平台路由：独立前缀挂载（server/urls.py ``^api/ai/``）。

URL 前缀与 app 对齐（ADR-059）：``/api/ai/...``（原注册串的 ``ai/`` 冗余
前导已随之去除）；Menu.path 权限点、前端 API 层、模块裁剪 ModuleSpec 的
routes 正则已同步平移。
"""

from django.urls import path
from rest_framework.routers import SimpleRouter

from ai.views.assistant import AiAssistantSettingViewSet, AiAssistantViewSet
from ai.views.knowledge import AiKnowledgeDocumentViewSet
from ai.views.mcp import McpEndpointAPIView
from ai.views.mcp_client import McpServerViewSet
from ai.views.profiles import AiProfileViewSet
from common.core.routers import NoDetailRouter

app_name = "ai"

router = SimpleRouter(False)
no_detail_router = NoDetailRouter(False)
no_detail_router.register("assistant/config", AiAssistantSettingViewSet, basename="ai-assistant-config")
no_detail_router.register("assistant", AiAssistantViewSet, basename="ai-assistant")
router.register("knowledge-documents", AiKnowledgeDocumentViewSet, basename="ai-knowledge-document")
router.register("profiles", AiProfileViewSet, basename="ai-profile")
router.register("mcp-servers", McpServerViewSet, basename="ai-mcp-server")

urlpatterns = no_detail_router.urls + router.urls
urlpatterns += [path("mcp", McpEndpointAPIView.as_view())]
