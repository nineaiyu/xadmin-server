#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""MCP 协议端点（Model Context Protocol，Streamable HTTP 无状态模式）。

把系统「统一功能接口」（``tool_catalog`` 动作目录 + ``execute_action`` 执行收口）
以标准 JSON-RPC 2.0 暴露给外部 MCP 客户端（Claude / Cursor / Cherry Studio 等），
与 AI 助手 ``tools`` 端点（HTTP/JSON 形态的 tools/list 等价物）共用同一份白名单
注册表——不存在第二份能力清单，新增能力 = 注册表加一条声明，MCP 侧自动可见。

协议要点（MCP 2025-03-26 起的 Streamable HTTP transport）：
- 单端点 POST JSON-RPC 2.0：``initialize`` / ``tools/list`` / ``tools/call`` / ``ping``；
- 通知（无 id 的 ``notifications/*``）返回 202 空体；不返回 ``Mcp-Session-Id``
  （无状态模式，协议允许）；
- 认证走既有 DRF 认证链（PAT ``Authorization: Pat <token>`` / JWT / Session），
  以令牌属主身份执行，权限双门与 Web 控制台同口径（视图权限点 + 动作权限点）；
- ``tools/call`` 高危动作（requires_approval）直接拒绝：MCP 无 412 审批通道，
  提示改走 Web 控制台的 AI 助手（确认卡片 + 审批流）；
- 全量审计：``tools/call`` 每次落 ``OperationLog(module=AI:action, auth_type=ai)``。

安全红线不变：白名单外一律拒绝、LLM/客户端参数按不可信输入逐项校验、
执行前重校验权限，PAT 可用 scope/IP 白名单进一步收敛。
"""

import json

from django.http import JsonResponse
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema
from rest_framework.views import APIView

from common.core.permission import IsAuthenticated
from common.utils import get_logger

logger = get_logger(__name__)

#: 支持的 MCP 协议版本（客户端请求不在列时回落到服务端最新支持版）
MCP_SUPPORTED_VERSIONS = ("2025-03-26", "2025-06-18")
MCP_DEFAULT_VERSION = "2025-03-26"

#: JSON-RPC 2.0 标准错误码
JSONRPC_PARSE_ERROR = -32700
JSONRPC_INVALID_REQUEST = -32600
JSONRPC_METHOD_NOT_FOUND = -32601
JSONRPC_INVALID_PARAMS = -32602
JSONRPC_SERVER_ERROR = -32000


def _rpc_result(msg_id, result) -> JsonResponse:
    return JsonResponse({"jsonrpc": "2.0", "id": msg_id, "result": result}, json_dumps_params={"ensure_ascii": False})


def _rpc_error(msg_id, code: int, message: str, data: dict = None) -> JsonResponse:
    error = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return JsonResponse({"jsonrpc": "2.0", "id": msg_id, "error": error}, json_dumps_params={"ensure_ascii": False})


def _tool_read_only(spec) -> bool:
    """全部声明路径均为 GET 的动作视为只读（供 MCP annotations.readOnlyHint）。"""
    return bool(spec.required_visits) and all(method.upper() == "GET" for method, __ in spec.required_visits)


def _tool_requires_approval(spec, user) -> bool:
    """高危动作提示值（requires_approval 谓词按空参数求值，仅供参考性 _meta）。"""
    try:
        return bool(spec.requires_approval(user, {}))
    except Exception:  # noqa: BLE001 提示值求值失败不影响目录下发
        return False


class McpEndpointAPIView(APIView):
    """MCP Streamable HTTP 端点（无状态，JSON 响应，无 SSE 流）"""

    permission_classes = [IsAuthenticated]

    def _check_enabled(self, msg_id):
        """灰度门禁（与 action/execute 同口径）：动作开关 + 助手配置。"""
        from system.utils.ai import is_enabled
        from system.utils.ai_actions import ai_action_enabled

        if not ai_action_enabled():
            return _rpc_error(msg_id, JSONRPC_SERVER_ERROR, str(_("AI actions are not enabled")))
        if not is_enabled():
            return _rpc_error(msg_id, JSONRPC_SERVER_ERROR, str(_("AI assistant is not enabled or configured")))
        return None

    def _handle_initialize(self, request, msg_id, params):
        requested = str((params or {}).get("protocolVersion") or "")
        version = requested if requested in MCP_SUPPORTED_VERSIONS else MCP_DEFAULT_VERSION
        return _rpc_result(
            msg_id,
            {
                "protocolVersion": version,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "xadmin", "version": "1.0.0"},
            },
        )

    def _handle_tools_list(self, request, msg_id):
        from system.utils.ai_actions import get_action
        from system.utils.ai_tool_catalog import tool_catalog

        user = request.user
        tools = []
        for entry in tool_catalog(user):
            spec = get_action(entry["name"])
            if spec is None:  # 目录与注册表同源，理论不可达（防御式跳过）
                continue
            entry["annotations"] = {"readOnlyHint": _tool_read_only(spec)}
            entry["_meta"] = {"x-requires-approval": _tool_requires_approval(spec, user)}
            tools.append(entry)
        return _rpc_result(msg_id, {"tools": tools})

    def _handle_tools_call(self, request, msg_id, params):
        from system.utils.ai_actions import audit_ai_action, execute_action, get_action

        params = params if isinstance(params, dict) else {}
        name = str(params.get("name") or "").strip()
        arguments = params.get("arguments")
        arguments = arguments if isinstance(arguments, dict) else {}
        user = request.user

        gate = self._check_enabled(msg_id)
        if gate is not None:
            return gate
        if not name:
            return _rpc_error(msg_id, JSONRPC_INVALID_PARAMS, str(_("The request cannot be empty")))

        spec = get_action(name)
        if spec is None:
            audit_ai_action(user, name, arguments, False, str(_("Unknown action")), {"channel": "mcp"})
            return _rpc_result(
                msg_id,
                {
                    "content": [{"type": "text", "text": str(_("Unknown action"))}],
                    "isError": True,
                },
            )

        def call_result(ok: bool, detail: str, data: dict) -> JsonResponse:
            payload = {"ok": ok, "detail": detail, "data": data}
            return _rpc_result(
                msg_id,
                {
                    "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, default=str)}],
                    "isError": not ok,
                },
            )

        if not spec.available(user):
            detail = str(_("The action is not available: {}").format(str(spec.label)))
            audit_ai_action(user, name, arguments, False, detail, {"channel": "mcp"})
            return call_result(False, detail, {})
        if not spec.has_permission(user):
            detail = str(_("You do not have permission to perform the action: {}").format(str(spec.label)))
            audit_ai_action(user, name, arguments, False, detail, {"channel": "mcp"})
            return call_result(False, detail, {})
        if spec.requires_approval(user, arguments):
            # MCP 通道没有 412 审批协议（一次性令牌重放由 Web 前端拦截器驱动），
            # 高危动作一律拒绝执行，引导走 Web 控制台的 AI 助手（确认卡片 + 审批流）
            detail = str(
                _(
                    "This action requires approval; use the AI assistant in the web console "
                    "(the approval flow is driven by the web frontend)"
                )
            )
            audit_ai_action(user, name, arguments, False, detail, {"channel": "mcp"})
            return call_result(False, detail, {})

        clean, error = spec.validate(user, arguments)
        if error:
            audit_ai_action(user, name, arguments, False, error, {"channel": "mcp"})
            return call_result(False, error, {})

        # 幂等：MCP 通道与 Web 通道同口径（用户 + 动作 + 参数哈希，TTL 内去重）
        from system.utils.ai_idempotency import execute_idempotent

        result = execute_idempotent(user, name, clean, execute_action)
        ok = bool(result.get("ok"))
        audit_ai_action(
            user,
            name,
            clean,
            ok,
            str(result.get("detail") or ""),
            {
                "channel": "mcp",
                "result": result.get("data") or {},
                "draft_id": result.get("draft_id"),
                "deduplicated": bool(result.get("deduplicated")),
            },
        )
        return call_result(ok, str(result.get("detail") or ""), result.get("data") or {})

    @extend_schema(request=None, responses=None)
    def post(self, request, *args, **kwargs):
        """JSON-RPC 2.0 单对象请求（batch 请求不支持，返回 Invalid Request）。"""
        from rest_framework.exceptions import ParseError

        try:
            payload = request.data
        except ParseError:
            return _rpc_error(None, JSONRPC_PARSE_ERROR, "Parse error")
        if not isinstance(payload, dict):
            return _rpc_error(None, JSONRPC_INVALID_REQUEST, str(_("Malformed JSON-RPC request")))
        # 无 id 的通知：按协议返回 202 空体（如 notifications/initialized）
        msg_id = payload.get("id")
        method = str(payload.get("method") or "").strip()
        if not method:
            return _rpc_error(msg_id, JSONRPC_INVALID_REQUEST, str(_("Malformed JSON-RPC request")))
        if msg_id is None:
            from django.http import HttpResponse

            return HttpResponse(status=202)
        params = payload.get("params")
        params = params if isinstance(params, dict) else {}

        if method == "initialize":
            return self._handle_initialize(request, msg_id, params)
        if method == "ping":
            return _rpc_result(msg_id, {})
        if method == "tools/list":
            gate = self._check_enabled(msg_id)
            return gate if gate is not None else self._handle_tools_list(request, msg_id)
        if method == "tools/call":
            return self._handle_tools_call(request, msg_id, params)
        return _rpc_error(msg_id, JSONRPC_METHOD_NOT_FOUND, str(_("Unknown method: {}").format(method[:64])))
