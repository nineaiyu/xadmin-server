#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""MCP 调用审计报表序列化器：OperationLog(module=AI:mcp:client) 的读侧翻译。

工具名 / 服务器名 / 通道 / 结果详情从 changes JSON 提取（写入侧
``audit_mcp_call`` 的载荷形态），列表即报表——不建第二张表、不改动审计写入。
"""

import json
from typing import Any

from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

# 跨 app 读侧走 services 转发（契约层过渡口径，不直连 audit.models）
from audit.services import OperationLog
from common.core.response import API_SUCCESS_CODE
from common.core.serializers import BaseModelSerializer


def _changes_dict(row: Any) -> dict[str, Any]:
    """changes JSON → dict（损坏/空值按空 dict，报表不因单行脏数据崩页）。"""
    raw = getattr(row, "changes", None)
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


class McpCallLogSerializer(BaseModelSerializer):
    """MCP 调用审计行：服务器 / 工具 / 通道 / 结果从 changes 提取。"""

    server_name = serializers.SerializerMethodField(label=_("Server"))
    tool = serializers.SerializerMethodField(label=_("Tool"))
    channel = serializers.SerializerMethodField(label=_("Channel"))
    detail = serializers.SerializerMethodField(label=_("Detail"))
    ok = serializers.SerializerMethodField(label=_("Success"))

    class Meta:
        model = OperationLog
        fields = [
            "pk",
            "server_name",
            "tool",
            "channel",
            "creator",
            "ok",
            "detail",
            "exec_time",
            "changes",
            "created_time",
        ]
        table_fields = [
            "created_time",
            "server_name",
            "tool",
            "creator",
            "channel",
            "ok",
            "detail",
            "exec_time",
        ]
        read_only_fields = ["pk"] + [field.name for field in OperationLog._meta.fields]
        extra_kwargs = {"creator": {"attrs": ["pk", "username"], "read_only": True, "format": "{username}"}}

    def get_server_name(self, obj: Any) -> str:
        return str(_changes_dict(obj).get("server") or "")

    def get_tool(self, obj: Any) -> str:
        return str(_changes_dict(obj).get("tool") or "")

    def get_channel(self, obj: Any) -> str:
        # 管理面直调无 channel（空串展示「管理面」由前端兜底），AI 动作链路为 mcp_tool
        return str(_changes_dict(obj).get("channel") or "")

    def get_detail(self, obj: Any) -> str:
        return str(_changes_dict(obj).get("detail") or "")[:200]

    def get_ok(self, obj: Any) -> bool:
        return getattr(obj, "status_code", None) == API_SUCCESS_CODE
