#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""AI 助手视图：配置（Setting 体系）+ 状态 + 问答 + 知识库 + 多档案。

- 配置视图与邮件/LDAP 同构：POST create = 连接测试（真实 ping LLM）；
- ask/status 经菜单权限点门控（未授权 403）；问答链路不触生产数据。

本包按职责拆分（assistant / knowledge / profiles），对外 API 由本文件统一再导出，
导入路径保持 ``system.views.ai`` 不变。
"""

from .assistant import AiAssistantSettingViewSet, AiAssistantViewSet
from .knowledge import AiKnowledgeDocumentFilter, AiKnowledgeDocumentViewSet
from .profiles import AiProfileFilter, AiProfileViewSet

__all__ = [
    "AiAssistantSettingViewSet",
    "AiAssistantViewSet",
    "AiKnowledgeDocumentFilter",
    "AiKnowledgeDocumentViewSet",
    "AiProfileFilter",
    "AiProfileViewSet",
]
