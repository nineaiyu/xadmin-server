#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""AI 外接 MCP 工具 → 受限动作动态目录（ActionSpec 动态目录）。

为什么是「动态」、为什么不并入 ``ai_actions.ACTION_SPECS``（重要）：

- 外接工具随同步快照漂移（服务器增删 / 白名单调整 / 重新 sync），import 期静态
  字典无法表达；且注册表守护测试（``test_ai_api_registry_guard``）在 import 期
  遍历静态字典逐项对账——动态条目并进去会让守护失真（每次进程启动集合都不同）；
- 因此动态 spec 不落静态注册表：目录（``available_actions`` / ``tool_catalog``）
  与解析（``get_action(key, user)``）按当前用户现查现拼，key 形如
  ``mcp.<server_pk>.<规范化工具名>``；spec 持有 server_pk + tool_name 定位
  （不解析 key，key 仅作为对账用的不透明标识）；
- 权限复用既有权限点 ``call:AiMcpServers``（POST /api/ai/mcp-servers/<pk>/call），
  零新权限种子；服务器侧另有 ``enabled`` + ``expose_to_ai`` 两道开关（默认关，
  fail-closed：接入 ≠ 暴露给 AI）；
- 审批口径：快照 ``readOnlyHint=True`` 的工具视为只读免审批；其余一律
  ``requires_approval_high_risk``（非超管走 412 审批协议，超管豁免同既有动作）；
- 审计双行：本模块 ``execute`` 落 AI:mcp:client（channel=mcp_tool），调用方
  （action/execute / 412 消费后）落 AI:action——同一执行两条审计可对账。

过滤口径（``mcp_action_specs``，全部 fail-closed）：
服务器 disabled / 未 expose_to_ai 整台跳过；调用者无 call 权限点整台跳过；
工具不在 allowed_tools 白名单跳过（白名单是显式授权，快照只是展示）；
快照条目缺 ``input_schema``（旧快照）跳过并告警提示重新 sync。
"""

import json
import re
from dataclasses import dataclass

from django.utils.translation import gettext_lazy as _

from ai.utils.ai_actions import MCP_ACTION_PREFIX, user_can_visit
from ai.utils.ai_api_actions import requires_approval_high_risk
from ai.utils.mcp_client import (
    MAX_ARGUMENTS_BYTES,
    McpClientError,
    audit_mcp_call,
    client_for,
    summarize_tool_result,
)
from common.utils import get_logger

logger = get_logger(__name__)

#: 全目录动态 spec 总量上限（防快照膨胀拖垮 prompt / tools 目录下发；超限截断并告警）
MAX_DYNAMIC_SPECS = 200
#: 单服务器动态工具上限（序列化器已限 allowed_tools ≤ 100，这里防御手工改库的旧数据）
MAX_TOOLS_PER_SERVER = 100
#: AI 动作链路调用外部工具的超时上限（秒）：执行 HTTP 请求同步在等结果，
#: 管理面配置的长超时（≤120s）不允许传导到 AI 确认/执行链路
AI_CALL_TIMEOUT_CAP = 30

_TOOL_KEY_ILLEGAL = re.compile(r"[^a-z0-9_]+")


def normalize_tool_key(name: str) -> str:
    """工具名 → key 尾段（``[a-z0-9_]``）：MCP 工具名常带 ``.`` ``-`` ``:`` 等分隔符。

    空名/全非法字符回落 ``tool``（保底可生成可用 key，不因第三方命名炸目录）。
    """
    cleaned = _TOOL_KEY_ILLEGAL.sub("_", str(name or "").strip().lower())
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    return cleaned or "tool"


def unique_tool_key(normalized: str, used: set) -> str:
    """同服务器内规范化名冲突加序号（首个占用原名，后续 ``_2`` ``_3``...）。"""
    tail, seq = normalized, 1
    while tail in used:
        seq += 1
        tail = f"{normalized}_{seq}"
    used.add(tail)
    return tail


@dataclass(frozen=True)
class McpActionSpec:
    """外接 MCP 工具动作（协议对齐 ``ai_actions.ActionSpec``，可混装动作目录）。

    与 ``ApiActionSpec`` 同思路：不继承、按协议同形状实现，调用方无需区分。
    """

    key: str
    #: 持有定位信息（不解析 key）：执行期按 server_pk 现查库内最新状态
    server_pk: str
    #: 快照里的原始工具名（调用外部服务必须用它，规范化名只用于 key）
    tool_name: str
    label: object
    description: object
    #: 同步快照的白名单化有界 schema（见 mcp_client.bound_input_schema）
    input_schema: dict
    #: 快照 readOnlyHint：只读工具免审批
    read_only: bool

    @property
    def required_visits(self) -> tuple:
        """复用既有权限点 call:AiMcpServers（零新权限种子）。"""
        return (("POST", f"/api/ai/mcp-servers/{self.server_pk}/call"),)

    @property
    def params(self) -> dict:
        """prompt 轨目录的参数视图：properties 展开为 name → 规则（补 required 标记）。

        ``tool_catalog``（function calling / MCP tools/list 轨）不走这里——本 spec
        的参数本就是 JSON Schema 形态，目录侧直接采用 ``input_schema`` 原文
        （见 ai_tool_catalog 的 raw schema 通道），避免二次映射丢失 enum/嵌套信息。
        """
        schema = self.input_schema if isinstance(self.input_schema, dict) else {}
        properties = schema.get("properties")
        if not isinstance(properties, dict):
            return {}
        required = {str(name) for name in schema.get("required") or [] if isinstance(name, str)}
        rules: dict = {}
        for name, rule in properties.items():
            merged = dict(rule) if isinstance(rule, dict) else {}
            merged["required"] = str(name) in required
            rules[str(name)] = merged
        return rules

    def has_permission(self, user) -> bool:
        """与 ActionSpec 同口径双门：业务权限点 + 可用性。"""
        return all(user_can_visit(user, method, path) for method, path in self.required_visits) and bool(
            self.available(user)
        )

    def available(self, user) -> bool:
        """服务器当前仍 enabled 且 expose_to_ai（现查库，不用构建目录时的旧状态）。"""
        from ai.models.mcp import McpServer

        return McpServer.objects.filter(pk=self.server_pk, enabled=True, expose_to_ai=True).exists()

    def validate(self, user, params):
        """轻量 JSON 校验（输入按不可信处理）：required 名单 + 32KB 体积上限。

        第三方 inputSchema 千奇百怪，这里不做完整 JSON Schema 校验（也不信任快照
        schema 本身足够安全）——工具端会再校验自己的入参；AI 侧只挡「缺必填」与
        「超大载荷」，与 mcp-servers/call 端点同一体积口径。
        """
        params = params if isinstance(params, dict) else {}
        schema = self.input_schema if isinstance(self.input_schema, dict) else {}
        for name in schema.get("required") or []:
            if not isinstance(name, str):
                continue
            if name not in params or params[name] in (None, ""):
                return {}, str(_("Missing required parameter: {}").format(name))
        try:
            size = len(json.dumps(params, ensure_ascii=False, default=str))
        except (TypeError, ValueError):
            return {}, str(_("Tool arguments are not serializable"))
        if size > MAX_ARGUMENTS_BYTES:
            return {}, str(_("Tool arguments exceed the size limit"))
        return params, None

    def requires_approval(self, user, params) -> bool:
        """只读工具直执行；写类/未知只读性的工具按高危动作处理（非超管 412）。"""
        if self.read_only:
            return False
        return requires_approval_high_risk(user, params)

    def execute(self, user, params) -> dict:
        """执行外部工具调用：执行期再校验（TOCTOU）→ call_tool → 摘要 → 审计。"""
        from ai.models.mcp import McpServer

        server = McpServer.objects.filter(pk=self.server_pk).first()
        if server is None or not server.enabled or not server.expose_to_ai:
            # 目录可见 ≠ 此刻可执行：服务器可能刚被禁用/撤出 AI 目录（fail-closed）
            detail = str(_("The MCP server is not available for AI actions"))
            audit_mcp_call(user, server, self.tool_name, False, detail, params, extra={"channel": "mcp_tool"})
            return {"ok": False, "detail": detail, "data": {}}
        if self.tool_name not in server.tool_names:
            # 白名单可能已收紧：以库内最新白名单为准（与 mcp-servers/call 端点同口径）
            detail = str(_("Tool {} is not in the allowed list").format(self.tool_name))
            audit_mcp_call(user, server, self.tool_name, False, detail, params, extra={"channel": "mcp_tool"})
            return {"ok": False, "detail": detail, "data": {}}
        timeout = min(int(server.timeout or 30), AI_CALL_TIMEOUT_CAP)
        try:
            result = client_for(server, timeout=timeout).call_tool(self.tool_name, params)
        except McpClientError as exc:
            audit_mcp_call(user, server, self.tool_name, False, exc.message, params, extra={"channel": "mcp_tool"})
            return {"ok": False, "detail": exc.message, "data": {}}
        summary = summarize_tool_result(result)
        ok = not summary["is_error"]
        audit_mcp_call(user, server, self.tool_name, ok, summary["text"][:200], params, extra={"channel": "mcp_tool"})
        detail = summary["text"][:200] or (str(_("Operation successful")) if ok else str(_("Tool call failed")))
        return {"ok": ok, "detail": detail, "data": {"tool": self.tool_name, **summary}}


def _spec_for(server, entry: dict, used: set) -> McpActionSpec:
    """快照条目 → 动态 spec（key 同服务器内冲突加序号，跨服务器由 pk 天然隔离）。"""
    tool_name = str(entry.get("name") or "").strip()
    tail = unique_tool_key(normalize_tool_key(tool_name), used)
    label = f"{server.name} · {tool_name}"
    description = str(entry.get("description") or "").strip() or label
    raw_schema = entry.get("input_schema")
    return McpActionSpec(
        key=f"{MCP_ACTION_PREFIX}{server.pk}.{tail}",
        server_pk=str(server.pk),
        tool_name=tool_name,
        label=label,
        description=description,
        input_schema=raw_schema if isinstance(raw_schema, dict) else {},
        read_only=bool(entry.get("read_only")),
    )


def mcp_action_specs(user) -> dict[str, McpActionSpec]:
    """当前用户可用的外接 MCP 工具动作（enabled + expose_to_ai + 权限点 + 白名单 + 快照齐全）。

    目录与执行解析共用本函数（``get_action(key, user)`` 按完整 key 命中），
    保证「目录可见 ⇔ 可解析」不会出现两套过滤口径。返回 dict 保持稳定顺序
    （服务器按 name、工具按白名单声明序），总量超 ``MAX_DYNAMIC_SPECS`` 截断并告警。
    """
    if not getattr(user, "is_authenticated", False) or not getattr(user, "pk", None):
        return {}
    from ai.models.mcp import McpServer

    specs: dict[str, McpActionSpec] = {}
    truncated = False
    servers = (
        McpServer.objects.filter(enabled=True, expose_to_ai=True)
        .only("pk", "name", "allowed_tools", "tools_snapshot")
        .order_by("name")
    )
    for server in servers:
        if len(specs) >= MAX_DYNAMIC_SPECS:
            truncated = True
            break
        # 权限点整台预检（与 has_permission 同口径）：无 call:AiMcpServers 的用户
        # 连目录都不该看到（批量预检避免目录构建期逐 spec 再查库）
        if not user_can_visit(user, "POST", f"/api/ai/mcp-servers/{server.pk}/call"):
            continue
        snapshot: dict = {}
        for entry in server.tools_snapshot or []:
            if isinstance(entry, dict) and str(entry.get("name") or "").strip():
                snapshot[str(entry["name"]).strip()] = entry
        missing_schema = False
        used: set = set()
        for tool_name in server.tool_names[:MAX_TOOLS_PER_SERVER]:
            if len(specs) >= MAX_DYNAMIC_SPECS:
                truncated = True
                break
            entry = snapshot.get(tool_name)
            if not isinstance(entry, dict):
                missing_schema = True
                continue
            schema = entry.get("input_schema")
            if not isinstance(schema, dict) or not schema:
                # 旧快照（input_schema 字段出现前同步的）fail-closed 跳过，重新 sync 恢复
                missing_schema = True
                continue
            spec = _spec_for(server, entry, used)
            specs[spec.key] = spec
        if missing_schema:
            logger.warning(
                "mcp server %s(%s) tools_snapshot lacks input_schema; tools skipped from AI action "
                "catalog (re-sync required)",
                server.pk,
                server.name,
            )
    if truncated:
        logger.warning("ai mcp dynamic action catalog capped at %s entries (some tools hidden)", MAX_DYNAMIC_SPECS)
    return specs
