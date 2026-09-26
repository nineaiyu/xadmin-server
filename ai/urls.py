#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""AI 平台路由：经 system/urls.py 以 ``path("", include("ai.urls"))`` 挂载。

URL 前缀保持 ``/api/system/ai/...``（Menu.path 权限点、前端路由、模块裁剪
ModuleSpec 的 routes 正则均以此为键，拆分不改路径）；不设 app_name，
视图名继续落在 system 命名空间下，与拆分前完全一致。
"""

from django.urls import path
from rest_framework.routers import SimpleRouter

from ai.views.assistant import AiAssistantSettingViewSet, AiAssistantViewSet
from ai.views.knowledge import AiKnowledgeDocumentViewSet
from ai.views.mcp import McpEndpointAPIView
from ai.views.profiles import AiProfileViewSet
from common.core.routers import NoDetailRouter

router = SimpleRouter(False)
no_detail_router = NoDetailRouter(False)
no_detail_router.register("ai/assistant/config", AiAssistantSettingViewSet, basename="ai-assistant-config")
no_detail_router.register("ai/assistant", AiAssistantViewSet, basename="ai-assistant")
router.register("ai/knowledge-documents", AiKnowledgeDocumentViewSet, basename="ai-knowledge-document")
router.register("ai/profiles", AiProfileViewSet, basename="ai-profile")

urlpatterns = no_detail_router.urls + router.urls
urlpatterns += [path("ai/mcp", McpEndpointAPIView.as_view())]
