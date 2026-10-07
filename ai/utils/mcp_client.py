#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""MCP 客户端（Streamable HTTP）：外接 MCP 服务器的 initialize / tools.list / tools.call。

协议要点（MCP Streamable HTTP transport）：

- 客户端向端点 POST JSON-RPC 2.0 消息，``Accept: application/json, text/event-stream``；
- 服务端可用 JSON 或 SSE（``text/event-stream``，``data: {...}`` 行）承载响应；
- 服务端可在 initialize 响应携带 ``Mcp-Session-Id`` 头，后续请求须回带；
- initialize 后发送 ``notifications/initialized`` 通知（失败不阻断）；
- 每次会话（同步/调用）先 initialize（协议要求），方法级错误归一为 McpClientError。

安全（与出站 Webhook 同一守卫 ``common/utils/outbound.py``）：

- 写入侧（保存配置）https 强制；http 仅允许 loopback 或经 ``OUTBOUND_ALLOWED_HOSTS``
  白名单登记的目标（白名单 = 显式授权的内网自建服务）；
- 发送侧走 ``pinned_request`` 固定解析结果连接（消除 DNS rebinding）；
- 响应体读取设上限；SSE 行按 bytes 自行 utf-8 解码（响应头缺 charset 时
  requests 会按 ISO-8859-1 推断导致乱码——历史教训）。
"""

import json
from typing import Any
from urllib.parse import urlparse

import requests
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from common.core.config import SysConfig
from common.core.response import API_SUCCESS_CODE
from common.utils.outbound import OutboundBlocked, parse_allowed_hosts, pinned_request, validate_outbound_url

MCP_PROTOCOL_VERSION = "2025-06-18"
CLIENT_NAME = "xadmin"
CLIENT_VERSION = "1.0"
#: 单次响应体读取上限（防超大响应拖垮工作进程）
MAX_BODY_BYTES = 2 * 1024 * 1024
#: 调用结果回传前文本截断长度
RESULT_TEXT_LIMIT = 4000
#: 工具调用参数 JSON 体积上限（AI 动作链路与 mcp-servers/call 端点同一口径）
MAX_ARGUMENTS_BYTES = 32 * 1024
LOOPBACK_HOSTS = ("127.0.0.1", "localhost")

# ---------------------------------------------------------------------------
# 快照 input_schema 有界化（快照要喂给 LLM 工具目录，必须收敛体积与形态）
# ---------------------------------------------------------------------------

#: 每工具 input_schema 序列化尺寸上限
MAX_SCHEMA_BYTES = 8 * 1024
#: 单个 object 节点 properties 键数上限
MAX_SCHEMA_PROPERTIES = 32
#: enum 取值个数上限
MAX_SCHEMA_ENUM = 50
#: 嵌套深度上限（超深回落为不透明 object；$ref 已剔除，深度是最后一道递归防线）
MAX_SCHEMA_DEPTH = 4
#: 单条 description 截断长度（描述是 schema 体积的主要来源）
MAX_SCHEMA_DESCRIPTION = 200
#: 白名单关键字：只保留对 LLM 有用且无递归风险的字段；
#: $ref/definitions/oneOf/allOf/anyOf/not/if/patternProperties 等一律剔除
SCHEMA_KEEP_KEYS = ("type", "description", "enum", "properties", "required")


class McpClientError(Exception):
    """MCP 交互错误（消息可直接作为 API detail 下发）。"""

    def __init__(self, message, *, code: int = 1001):
        self.message = str(message)
        self.code = code
        super().__init__(self.message)


def outbound_allowed_hosts() -> tuple:
    """出站白名单（与 Webhook 同源：``OUTBOUND_ALLOWED_HOSTS``）。"""
    return parse_allowed_hosts(SysConfig.OUTBOUND_ALLOWED_HOSTS)


def validate_server_url(url: str) -> str:
    """写入侧 URL 校验（与 Webhook 同口径 + 白名单可放行 http 内网目标）。

    - https：允许（域名写入侧不解析，发送侧严格校验）；
    - http：仅允许 loopback（联调）或白名单登记的主机（内网自建 MCP 服务）；
    - 其余协议与 IP 字面量私网目标按 outbound 守卫拒绝（元数据地址等任何模式都拒绝）。
    """
    url = str(url or "").strip()
    host = str(urlparse(url).hostname or "").lower()
    allowed = url.startswith("https://")
    if not allowed and url.startswith("http://"):
        allowed = host in LOOPBACK_HOSTS or host in outbound_allowed_hosts()
    if not allowed:
        raise ValidationError(
            _("MCP server url must use https (http is allowed for loopback or OUTBOUND_ALLOWED_HOSTS targets)")
        )
    validate_outbound_url(
        url,
        allow_private=False,
        allow_loopback=True,
        allowed_hosts=outbound_allowed_hosts(),
        strict_resolve=False,
    )
    return url


def _error_text(exc) -> str:
    """OutboundBlocked/ValidationError → 可读消息。"""
    messages = getattr(exc, "messages", None)
    if messages:
        return str(messages[0])
    return str(exc)


def _schema_bytes(schema: dict) -> int:
    return len(json.dumps(schema, ensure_ascii=False, default=str).encode("utf-8"))


def _sanitize_schema_node(node, depth: int, flags: dict):
    """递归白名单化单个 schema 节点：只留 LLM 友好关键字，additionalProperties 一律 False。

    为什么不做完整 JSON Schema 透传：第三方 inputSchema 可能携带 $ref/definitions
    （递归引用，喂给 LLM 既费 token 又可能让模型生成不可解析输出）、oneOf/allOf
    （分支组合语义对 function calling 无意义）以及超深嵌套——白名单化是最保守且
    可预期的收敛方式；被剔除的关键字只影响「描述精度」，不影响「能调用」。
    """
    truncated = False
    if depth > MAX_SCHEMA_DEPTH:
        flags["truncated"] = True
        return {"type": "object"}
    if not isinstance(node, dict):
        return {}
    out: dict = {}
    kind = node.get("type")
    if isinstance(kind, str) and kind:
        out["type"] = kind
    elif isinstance(kind, list) and kind:
        out["type"] = [str(item) for item in kind if isinstance(item, str)]
    description = node.get("description")
    if isinstance(description, str) and description.strip():
        out["description"] = description.strip()[:MAX_SCHEMA_DESCRIPTION]
    enum_values = node.get("enum")
    if isinstance(enum_values, list) and enum_values:
        scalars = [item for item in enum_values if isinstance(item, (str, int, float, bool))]
        if len(scalars) > MAX_SCHEMA_ENUM:
            scalars = scalars[:MAX_SCHEMA_ENUM]
            truncated = True
        out["enum"] = scalars
    raw_properties = node.get("properties")
    if isinstance(raw_properties, dict) and raw_properties:
        properties = {}
        for index, (name, rule) in enumerate(raw_properties.items()):
            if index >= MAX_SCHEMA_PROPERTIES:
                truncated = True
                break
            properties[str(name)] = _sanitize_schema_node(rule, depth + 1, flags)
        out["properties"] = properties
        raw_required = node.get("required")
        if isinstance(raw_required, list) and raw_required:
            # required 与（截断后的）properties 对齐：模型无法提供目录里没有的参数
            names = {str(item) for item in raw_required if isinstance(item, str)}
            required = [str(item) for item in raw_required if isinstance(item, str) and item in properties]
            if len(names) > len(required):
                truncated = True
            out["required"] = required[:MAX_SCHEMA_PROPERTIES]
    # object 节点一律收口为封闭 schema：明确告诉模型「只接受列出的参数」
    if out.get("type") == "object" or "properties" in out:
        out["additionalProperties"] = False
    if truncated:
        flags["truncated"] = True
    return out


def _strip_schema_descriptions(node):
    """尺寸降级第一步：递归剥 description（保留结构与类型）。"""
    if isinstance(node, dict):
        return {key: _strip_schema_descriptions(value) for key, value in node.items() if key != "description"}
    if isinstance(node, list):
        return [_strip_schema_descriptions(item) for item in node]
    return node


def _flatten_schema(schema: dict) -> dict:
    """尺寸降级第二步（保底）：只留顶层参数名 + 类型（32 键上限下必然 ≤ 8KB）。"""
    properties = schema.get("properties")
    flat_properties = {}
    if isinstance(properties, dict):
        for name, rule in properties.items():
            flat_properties[str(name)] = (
                {"type": rule.get("type")} if isinstance(rule, dict) and rule.get("type") else {}
            )
    flat: dict = {"type": "object", "properties": flat_properties, "additionalProperties": False}
    if schema.get("required"):
        flat["required"] = schema["required"]
    return flat


def bound_input_schema(raw) -> tuple:
    """第三方 inputSchema → 有界白名单 schema。返回 ``(schema, truncated)``。

    三级收敛：白名单化（含 32 键 / 深度 / enum 上限）→ 超尺寸剥 description →
    仍超尺寸拍平为「参数名 + 类型」。任一降级发生即置 ``truncated``，快照据此
    标记 ``schema_truncated``，管理页可识别不完整 schema。
    """
    flags: dict = {"truncated": False}
    schema = _sanitize_schema_node(raw if isinstance(raw, dict) else {}, 0, flags)
    if _schema_bytes(schema) > MAX_SCHEMA_BYTES:
        flags["truncated"] = True
        schema = _strip_schema_descriptions(schema)
    if _schema_bytes(schema) > MAX_SCHEMA_BYTES:
        schema = _flatten_schema(schema)
    return schema, flags["truncated"]


class McpClient:
    """单会话 MCP 客户端：initialize → tools/list / tools/call（同步阻塞，调用方控制线程）。"""

    def __init__(self, url: str, *, auth_header: str = "", auth_token: str = "", timeout: int = 30):
        self.url = str(url or "").strip()
        self.auth_header = str(auth_header or "").strip()
        self.auth_token = str(auth_token or "")
        self.timeout = max(5, min(int(timeout or 30), 120))
        self.session_id = ""
        self._seq = 0

    # -- 传输 ---------------------------------------------------------------

    def _headers(self) -> dict:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self.auth_header and self.auth_token:
            headers[self.auth_header] = self.auth_token
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        return headers

    def _read_limited(self, response) -> str:
        chunks, total = [], 0
        for chunk in response.iter_content(chunk_size=65536):
            total += len(chunk)
            if total > MAX_BODY_BYTES:
                raise McpClientError(_("MCP server response exceeds the size limit"))
            chunks.append(chunk)
        return b"".join(chunks).decode("utf-8", errors="replace")

    def _read_sse(self, response, request_id) -> dict:
        """SSE 响应：逐行取 ``data:`` 负载，返回 id 匹配的 JSON-RPC message。"""
        for raw_line in response.iter_lines(decode_unicode=False):
            if not raw_line:
                continue
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if not data:
                continue
            try:
                message = json.loads(data)
            except json.JSONDecodeError:
                continue
            if isinstance(message, dict) and (request_id is None or message.get("id") == request_id):
                return message
        return {}

    @staticmethod
    def _parse_message(text: str) -> dict:
        if not text.strip():
            return {}
        try:
            message = json.loads(text)
        except json.JSONDecodeError as exc:
            raise McpClientError(_("MCP server returned invalid JSON")) from exc
        if isinstance(message, list):  # batch 响应：取首个含 result/error 的条目
            for item in message:
                if isinstance(item, dict) and ("result" in item or "error" in item):
                    return item
            return {}
        return message if isinstance(message, dict) else {}

    def _request(self, payload: dict) -> dict:
        """发送 JSON-RPC 消息并返回响应 message（通知类返回 {}）。"""
        try:
            response = pinned_request(
                "POST",
                self.url,
                allow_private=False,
                allow_loopback=True,
                allowed_hosts=outbound_allowed_hosts(),
                headers=self._headers(),
                json=payload,
                timeout=self.timeout,
                stream=True,
            )
        except OutboundBlocked as exc:
            raise McpClientError(_("MCP server url is blocked: {}").format(_error_text(exc))) from exc
        except requests.RequestException as exc:
            raise McpClientError(_("MCP server connection failed: {}").format(exc.__class__.__name__)) from exc
        try:
            with response:
                if response.headers.get("Mcp-Session-Id"):
                    self.session_id = response.headers["Mcp-Session-Id"]
                if response.status_code >= 400:
                    raise McpClientError(_("MCP server returned HTTP {}").format(response.status_code))
                content_type = str(response.headers.get("Content-Type") or "").lower()
                if "text/event-stream" in content_type:
                    return self._read_sse(response, payload.get("id"))
                return self._parse_message(self._read_limited(response))
        except requests.RequestException as exc:
            raise McpClientError(_("MCP server response read failed: {}").format(exc.__class__.__name__)) from exc

    # -- 协议 ---------------------------------------------------------------

    def _call(self, method: str, params: dict | None = None) -> Any:
        self._seq += 1
        rpc_id = self._seq
        message = self._request({"jsonrpc": "2.0", "id": rpc_id, "method": method, "params": params or {}})
        if not message:
            raise McpClientError(_("MCP server did not return a response for {}").format(method))
        if message.get("id") != rpc_id:
            raise McpClientError(_("MCP server returned an unexpected response"))
        error = message.get("error")
        if error:
            detail = error.get("message") if isinstance(error, dict) else error
            raise McpClientError(_("MCP server error: {}").format(detail))
        return message.get("result")

    def _notify(self, method: str, params: dict | None = None) -> None:
        self._request({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def initialize(self) -> dict:
        result = self._call(
            "initialize",
            {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": CLIENT_NAME, "version": CLIENT_VERSION},
            },
        )
        try:
            self._notify("notifications/initialized")
        except McpClientError:
            pass  # 通知失败不阻断（部分服务端对通知返回空体/405）
        return result if isinstance(result, dict) else {}

    def list_tools(self) -> list:
        """拉取工具清单（initialize → tools/list），返回快照条目列表。"""
        self.initialize()
        result = self._call("tools/list", {})
        tools = (result or {}).get("tools") if isinstance(result, dict) else None
        return [self._tool_summary(item) for item in tools or [] if isinstance(item, dict)]

    def call_tool(self, name: str, arguments: dict | None = None) -> dict:
        """调用工具（initialize → tools/call），返回原始 result。"""
        self.initialize()
        result = self._call("tools/call", {"name": name, "arguments": arguments or {}})
        return result if isinstance(result, dict) else {}

    @staticmethod
    def _tool_summary(item: dict) -> dict:
        """工具快照条目：展示字段 + 有界 ``input_schema``（AI 动作目录的数据源）。

        完整 inputSchema 不直接落快照（$ref/oneOf 等对 LLM 不友好且可能递归/超大），
        经 ``bound_input_schema`` 白名单化 + 三级尺寸收敛；``schema_truncated`` 标记
        发生过截断。历史快照（本字段出现前同步的）没有 ``input_schema`` 键——动作
        目录侧对其 fail-closed 跳过，重新 sync 后恢复可见。
        """
        raw_schema = item.get("inputSchema")
        schema = raw_schema if isinstance(raw_schema, dict) else {}
        raw_properties = schema.get("properties")
        properties = raw_properties if isinstance(raw_properties, dict) else {}
        raw_annotations = item.get("annotations")
        annotations = raw_annotations if isinstance(raw_annotations, dict) else {}
        raw_required = schema.get("required")
        required_values = raw_required if isinstance(raw_required, list) else []
        required = [str(entry) for entry in required_values if isinstance(entry, (str, int))]
        input_schema, schema_truncated = bound_input_schema(schema)
        return {
            "name": str(item.get("name") or ""),
            "description": str(item.get("description") or "")[:500],
            "read_only": bool(annotations.get("readOnlyHint")),
            "params": [str(key) for key in properties][:50],
            "required": required[:50],
            "input_schema": input_schema,
            "schema_truncated": schema_truncated,
        }


def client_for(server, *, timeout=None) -> McpClient:
    """按 McpServer 配置构建客户端（令牌解密在此发生）。

    ``timeout`` 覆盖 ``server.timeout``：AI 动作链路用它把同步等待钳到更短上限
    （执行 HTTP 请求在等结果，不能吃满管理面配置的长超时）。
    """
    return McpClient(
        server.url,
        auth_header=server.auth_header,
        auth_token=server.auth_token_plain,
        timeout=server.timeout if timeout is None else timeout,
    )


def summarize_tool_result(result: dict, limit: int = RESULT_TEXT_LIMIT) -> dict:
    """调用结果摘要（回传管理页展示 + 审计）：文本内容拼接截断 + isError。"""
    texts = []
    content = result.get("content")
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                texts.append(str(item.get("text") or ""))
    text = "\n".join(texts)
    if len(text) > limit:
        text = text[:limit] + "..."
    return {"is_error": bool(result.get("isError")), "text": text}


def tools_with_callable(server, tools) -> list:
    """工具快照逐条补 callable 标记（列表序列化时计算，不落库）。

    准入规则与 call 端点同口径：服务器启用 + 工具在白名单内
    （``allowed_tools`` 为空 = 全部禁止，fail-closed）。规则变更后
    标记随下一次列表读取即时生效；快照原条目原样保留，仅追加布尔键。
    """
    allowed = set(server.tool_names)
    enabled = bool(server.enabled)
    rows = []
    for tool in tools or []:
        if isinstance(tool, dict):
            row = dict(tool)
            row["callable"] = enabled and str(row.get("name") or "") in allowed
            rows.append(row)
        else:
            rows.append(tool)
    return rows


def audit_mcp_call(
    user, server, tool: str, ok: bool, detail: str = "", arguments: dict | None = None, extra: dict | None = None
) -> None:
    """MCP 调用语义审计：落 OperationLog(module=AI:mcp:client)。

    ``extra``：补充对账维度——AI 动作链路传 ``channel=mcp_tool``，与
    同请求落下的 AI:action 审计行按调用参数对上双模块记录。
    """
    import logging

    from audit.services import OperationLog

    logger = logging.getLogger(__name__)
    try:
        OperationLog.objects.create(
            module="AI:mcp:client",
            object_pk=str(getattr(server, "pk", "")),
            auth_type=OperationLog.AuthType.AI,
            status_code=API_SUCCESS_CODE if ok else 1001,
            response_code=API_SUCCESS_CODE if ok else 1001,
            changes=json.dumps(
                {
                    "server": server.name,
                    "tool": tool,
                    "arguments": arguments or {},
                    "status": "ok" if ok else "failed",
                    "detail": detail,
                    **(extra or {}),
                },
                ensure_ascii=False,
                default=str,
            )[:4096],
        )
    except Exception:  # noqa: BLE001 审计失败不影响业务
        logger.warning("write MCP client audit failed", exc_info=True)
