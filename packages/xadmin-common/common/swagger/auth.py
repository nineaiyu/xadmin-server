#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""文档站 session 认证：docs 登录建立的 session 只信任 /api-docs/* 请求。

`ApiLogin` 用 Django ``login()`` 建立的是普通 session；若不限制，它可以像主站
session 一样通过 DRF 的 ``SessionAuthentication`` 调用任意业务 API，把「文档站
登录」变成绕过 MFA / 登录策略的通用登录面。这里在认证层按 session 标记收窄：
只允许访问 ``/api-docs/`` 前缀，其它路径返回 None 交给后续认证类（JWT / PAT）。
"""

from typing import Any

from rest_framework.authentication import SessionAuthentication

API_DOCS_SESSION_FLAG = "api_docs_only"
API_DOCS_PATH_PREFIX = "/api-docs/"


class ApiDocsSessionAuthentication(SessionAuthentication):
    """带 docs 标记的 session 仅放行文档站路径。"""

    def authenticate(self, request: Any) -> Any:
        result = super().authenticate(request)
        if result is None:
            return None
        session = getattr(request, "session", None)
        if session is not None and session.get(API_DOCS_SESSION_FLAG):
            if not request.path.startswith(API_DOCS_PATH_PREFIX):
                return None
        return result
