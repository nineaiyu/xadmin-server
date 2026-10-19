#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""文件域的 AI 动作声明（文件清单 / 存储统计，只读）。

由 ``ai.utils.ai_registry`` 自动发现；上传与删除不纳入 AI 动作面（含上传策略与
回收站语义，走页面链路）。
"""

from django.utils.translation import gettext_lazy as _

from ai.utils.ai_api_actions import IN_QUERY, api_action

AI_ACTIONS = {
    "file.list": api_action(
        key="file.list",
        label=_("List uploaded files"),
        description=_("List uploaded files (name, size, uploader)"),
        method="GET",
        path="/api/file/file",
        params={"name": {"type": "string", "required": False, "in": IN_QUERY, "description": "File name keyword"}},
    ),
    "file.stats": api_action(
        key="file.stats",
        label=_("Show file storage stats"),
        description=_("File storage statistics (counts, sizes by type)"),
        method="GET",
        path="/api/file/file/stats",
        params={},
    ),
    "file.detail": api_action(
        key="file.detail",
        label=_("Show an uploaded file"),
        description=_("One uploaded file with size, type, uploader and reference state"),
        method="GET",
        path="/api/file/file/<pk>",
        params={"pk": {"type": "pk", "required": True, "in": "path", "description": "File id (from file.list)"}},
    ),
    "file.access_logs": api_action(
        key="file.access_logs",
        label=_("Show file access logs"),
        description=_("Access audit of one file (download, preview, delete, uploader)"),
        method="GET",
        path="/api/file/file/<pk>/access-logs",
        params={"pk": {"type": "pk", "required": True, "in": "path", "description": "File id (from file.list)"}},
    ),
}
