# -*- coding: utf-8 -*-
"""外接 MCP 工具动作面单测：快照 schema 有界化 / key 规范化 / 动态目录过滤 / 审批与校验。

背景：外接 MCP 工具经「同步快照 + allowed_tools 白名单 + expose_to_ai 开关」进入
AI 动作目录（动态 spec，不入静态注册表）。本文件覆盖：

- ``bound_input_schema``：白名单关键字、危险关键字剔除（$ref/oneOf/allOf 等）、
  键数/深度/enum 上限、尺寸三级降级与 ``schema_truncated`` 标记；
- key 规范化与同服务器冲突加序号；
- ``mcp_action_specs`` 过滤口径（fail-closed：disabled / 未暴露 / 白名单外 /
  快照缺 input_schema / 无权限）；
- ``McpActionSpec``：只读审批分类、轻量参数校验、执行期 TOCTOU fail-closed、
  超时钳制。
"""

import json

import pytest
from django.utils.translation import gettext as _

from ai.models.mcp import McpServer
from ai.utils import ai_mcp_actions as mcp_actions
from ai.utils import mcp_client as mcp
from ai.utils.ai_mcp_actions import (
    MAX_ARGUMENTS_BYTES,
    McpActionSpec,
    mcp_action_specs,
    normalize_tool_key,
    unique_tool_key,
)
from system.models import OperationLog

pytestmark = [pytest.mark.django_db]

#: 快照条目样例（与桩 MCP server 的 tools/list 形态一致，另含新增字段）
SNAPSHOT = [
    {
        "name": "echo",
        "description": "Echo text",
        "read_only": True,
        "params": ["text"],
        "required": ["text"],
        "input_schema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
    },
    {
        "name": "danger",
        "description": "Dangerous op",
        "read_only": False,
        "params": [],
        "input_schema": {"type": "object", "properties": {}},
    },
]


def _server(**extra) -> McpServer:
    defaults = {
        "name": "样例桩",
        "url": "https://mcp.example.com/mcp",
        "allowed_tools": ["echo", "danger"],
        "tools_snapshot": json.loads(json.dumps(SNAPSHOT, ensure_ascii=False)),
        "expose_to_ai": True,
    }
    defaults.update(extra)
    return McpServer.objects.create(**defaults)


class TestBoundInputSchema:
    def test_keeps_whitelisted_keywords_and_closes_object(self):
        schema, truncated = mcp.bound_input_schema(
            {
                "type": "object",
                "properties": {"text": {"type": "string", "description": "文本", "minLength": 1}},
                "required": ["text"],
                "additionalProperties": True,
            }
        )
        assert truncated is False
        assert schema == {
            "type": "object",
            "properties": {"text": {"type": "string", "description": "文本"}},
            "required": ["text"],
            "additionalProperties": False,
        }

    @pytest.mark.parametrize(
        "dangerous",
        [
            {"$ref": "#/definitions/x"},
            {"definitions": {"x": {"type": "string"}}},
            {"oneOf": [{"type": "string"}]},
            {"allOf": [{"type": "string"}]},
            {"anyOf": [{"type": "string"}]},
            {"not": {}},
            {"if": {}, "then": {}, "else": {}},
            {"patternProperties": {"^x": {}}},
        ],
    )
    def test_strips_dangerous_keywords(self, dangerous):
        """$ref/oneOf/allOf 等对 LLM 不友好或引入递归的关键字一律剔除。"""
        schema, __ = mcp.bound_input_schema({"type": "object", "properties": {"p": dangerous}})
        assert schema["properties"]["p"] == {}

    def test_properties_capped_at_limit(self):
        raw = {"type": "object", "properties": {f"p{i}": {"type": "string"} for i in range(40)}}
        schema, truncated = mcp.bound_input_schema(raw)
        assert truncated is True
        assert len(schema["properties"]) == mcp.MAX_SCHEMA_PROPERTIES

    def test_deep_nesting_capped(self):
        node: dict = {"type": "string"}
        for __ in range(12):
            node = {"type": "object", "properties": {"nested": node}}
        schema, truncated = mcp.bound_input_schema(node)
        assert truncated is True
        # 逐层剥到深度上限后回落为不透明 object（depth > MAX_SCHEMA_DEPTH 即截断）
        current: dict = schema
        for __ in range(mcp.MAX_SCHEMA_DEPTH + 1):
            current = current["properties"]["nested"]
        assert current == {"type": "object"}

    def test_enum_kept_and_capped(self):
        schema, __ = mcp.bound_input_schema({"type": "string", "enum": [f"v{i}" for i in range(60)]})
        assert len(schema["enum"]) == mcp.MAX_SCHEMA_ENUM

    def test_required_intersected_with_properties(self):
        """required 指向目录外参数（截断/坏快照）时剔除并标记截断。"""
        schema, truncated = mcp.bound_input_schema(
            {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a", "ghost"]}
        )
        assert schema["required"] == ["a"]
        assert truncated is True

    def test_oversized_schema_degrades_descriptions_first(self, monkeypatch):
        big = {"type": "object"}
        big["properties"] = {f"p{i}": {"type": "string", "description": "x" * 400} for i in range(32)}
        # 单条 description 在白名单化时已截 200 字符，32 键极限形态恰在 8KB 内；
        # 这里调低上限以确定性地触发第一级降级（剥 description，保结构）
        monkeypatch.setattr(mcp, "MAX_SCHEMA_BYTES", 2000)
        schema, truncated = mcp.bound_input_schema(big)
        assert truncated is True
        assert mcp._schema_bytes(schema) <= 2000
        assert "description" not in json.dumps(schema)
        # 结构保留：参数名与类型仍可用于模型选参
        assert schema["properties"]["p0"] == {"type": "string"}

    def test_oversized_schema_falls_back_to_flat(self, monkeypatch):
        big = {"type": "object"}
        big["properties"] = {f"p{i}": {"type": "string", "description": "x" * 400} for i in range(32)}
        monkeypatch.setattr(mcp, "MAX_SCHEMA_BYTES", 300)  # 连剥 description 都装不下 → 拍平
        schema, truncated = mcp.bound_input_schema(big)
        assert truncated is True
        assert schema["type"] == "object"
        # 拍平形态：顶层参数名 + 类型（保底形态，不再保证 ≤ 上限，但已是最小结构）
        assert all(set(value) == {"type"} for value in schema["properties"].values())
        assert schema["properties"]["p31"] == {"type": "string"}
        assert "description" not in json.dumps(schema)

    @pytest.mark.parametrize("raw", [None, "x", 42, {}])
    def test_non_dict_or_empty_schema(self, raw):
        assert mcp.bound_input_schema(raw) == ({}, False)


class TestKeyNormalization:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("echo", "echo"),
            ("Filesystem.Read", "filesystem_read"),
            ("a--b__c.d", "a_b_c_d"),
            ("搜索 tool", "tool"),  # 中文全部非法 → 回落保底名
            ("", "tool"),
            ("::", "tool"),
            ("v2_call-OK", "v2_call_ok"),
        ],
    )
    def test_normalize(self, raw, expected):
        assert normalize_tool_key(raw) == expected

    def test_conflict_gets_sequence_suffix(self):
        used: set = set()
        assert unique_tool_key("echo", used) == "echo"
        assert unique_tool_key("echo", used) == "echo_2"
        assert unique_tool_key("echo", used) == "echo_3"


class TestMcpActionSpecFilters:
    def test_happy_path_superuser(self, superuser):
        server = _server()
        specs = mcp_action_specs(superuser)
        assert set(specs) == {f"mcp.{server.pk}.echo", f"mcp.{server.pk}.danger"}
        spec = specs[f"mcp.{server.pk}.echo"]
        assert spec.tool_name == "echo"
        assert spec.server_pk == str(server.pk)
        assert spec.read_only is True
        assert spec.label == f"{server.name} · echo"
        assert spec.input_schema["properties"]["text"]["type"] == "string"
        assert spec.required_visits == (("POST", f"/api/ai/mcp-servers/{server.pk}/call"),)
        assert spec.has_permission(superuser) is True

    def test_disabled_server_skipped(self, superuser):
        _server(enabled=False)
        assert mcp_action_specs(superuser) == {}

    def test_not_exposed_skipped(self, superuser):
        """expose_to_ai 默认 False：接入 ≠ 暴露给 AI（fail-closed）。"""
        _server(expose_to_ai=False)
        assert mcp_action_specs(superuser) == {}

    def test_whitelist_filters_tools(self, superuser):
        server = _server(allowed_tools=["danger"])
        specs = mcp_action_specs(superuser)
        assert set(specs) == {f"mcp.{server.pk}.danger"}

    def test_snapshot_missing_schema_skipped(self, superuser, caplog):
        """旧快照缺 input_schema：fail-closed 跳过并告警提示重新 sync。"""
        _server(tools_snapshot=[{"name": "echo", "description": "旧快照", "read_only": True}])
        with caplog.at_level("WARNING", logger="ai.utils.ai_mcp_actions"):
            assert mcp_action_specs(superuser) == {}
        assert "re-sync required" in caplog.text

    def test_snapshot_empty_schema_skipped(self, superuser):
        _server(tools_snapshot=[{"name": "echo", "read_only": True, "input_schema": {}}])
        assert mcp_action_specs(superuser) == {}

    def test_user_without_call_permission_skipped(self, normal_user):
        """有 expose 无权限点：普通用户目录不可见（复用 call:AiMcpServers 权限点）。"""
        _server()
        assert mcp_action_specs(normal_user) == {}

    def test_anonymous_and_missing_user(self, db):
        _server()
        assert mcp_action_specs(None) == {}
        assert mcp_action_specs(type("Anon", (), {"is_authenticated": False, "pk": None})()) == {}

    def test_prompt_catalog_and_openai_tools_include_dynamic(self, superuser):
        """三条消费轨同源：prompt 目录与 openai_tools 都含动态动作（params 为 schema 原文）。"""
        from ai.utils.ai_actions import build_catalog
        from ai.utils.ai_tool_catalog import openai_tools

        server = _server()
        key = f"mcp.{server.pk}.echo"
        entries = {item["action"]: item for item in build_catalog(superuser)["actions"]}
        assert key in entries
        assert entries[key]["params"]["text"] == {"type": "string", "required": True}
        functions = {item["function"]["name"]: item["function"] for item in openai_tools(superuser)}
        assert key in functions
        assert functions[key]["parameters"]["type"] == "object"
        assert functions[key]["parameters"]["properties"]["text"]["type"] == "string"


class TestApprovalAndValidation:
    def _spec(self, read_only: bool) -> McpActionSpec:
        return McpActionSpec(
            key="mcp.x.echo",
            server_pk="x",
            tool_name="echo",
            label="echo",
            description="echo",
            input_schema={"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
            read_only=read_only,
        )

    def test_read_only_never_requires_approval(self, normal_user, superuser):
        assert self._spec(True).requires_approval(normal_user, {}) is False
        assert self._spec(True).requires_approval(superuser, {}) is False

    def test_non_read_only_high_risk(self, normal_user, superuser):
        """写类工具：非超管必 412（复用高危动作口径），超管豁免。"""
        assert self._spec(False).requires_approval(normal_user, {}) is True
        assert self._spec(False).requires_approval(superuser, {}) is False

    def test_validate_required_and_size(self, normal_user):
        spec = self._spec(True)
        clean, error = spec.validate(normal_user, {})
        assert error and "text" in error
        clean, error = spec.validate(normal_user, {"text": None})
        assert error
        clean, error = spec.validate(normal_user, {"text": "你好"})
        assert error is None and clean == {"text": "你好"}

    def test_validate_size_cap(self, normal_user, monkeypatch):
        monkeypatch.setattr(mcp_actions, "MAX_ARGUMENTS_BYTES", 4)
        __, error = self._spec(True).validate(normal_user, {"text": "超长" * 10})
        assert error  # 文案为本地化字符串，断言有错误即可（与端点同一常量口径）
        # 常量与 mcp-servers/call 端点同源（下沉到 utils 后的口径对账）
        assert MAX_ARGUMENTS_BYTES == 32 * 1024

    def test_params_view_for_prompt_catalog(self):
        rules = self._spec(True).params
        assert rules == {"text": {"type": "string", "required": True}}


class TestExecuteFailClosed:
    def test_whitelist_revoked_after_catalog(self, superuser):
        """TOCTOU：目录生成后白名单收紧，执行期以库内最新状态为准（fail-closed）。"""
        server = _server()
        spec = mcp_action_specs(superuser)[f"mcp.{server.pk}.echo"]
        server.allowed_tools = ["other"]
        server.save(update_fields=["allowed_tools"])
        result = spec.execute(superuser, {"text": "x"})
        assert result["ok"] is False
        assert "echo" in result["detail"]  # 文案为本地化字符串，断言工具名在报错里
        log = OperationLog.objects.filter(module="AI:mcp:client").first()
        assert log is not None and log.status_code == 1001
        assert json.loads(log.changes)["channel"] == "mcp_tool"

    def test_server_disabled_after_catalog(self, superuser):
        server = _server()
        spec = mcp_action_specs(superuser)[f"mcp.{server.pk}.echo"]
        server.enabled = False
        server.save(update_fields=["enabled"])
        result = spec.execute(superuser, {"text": "x"})
        assert result["ok"] is False
        # detail 走 gettext（.mo 编译后为中文）：断言同一 msgid 的译文而非语言子串
        assert result["detail"] == str(_("The MCP server is not available for AI actions"))

    def test_server_deleted_after_catalog(self, superuser):
        server = _server()
        spec = mcp_action_specs(superuser)[f"mcp.{server.pk}.echo"]
        server.delete()
        result = spec.execute(superuser, {"text": "x"})
        assert result["ok"] is False

    def test_success_calls_tool_with_capped_timeout(self, superuser, monkeypatch):
        server = _server(timeout=120)
        spec = mcp_action_specs(superuser)[f"mcp.{server.pk}.echo"]
        captured: dict = {}

        class _FakeClient:
            def __init__(self, __srv, timeout=None):
                captured["timeout"] = timeout

            def call_tool(self, name, arguments):
                captured["call"] = (name, arguments)
                return {"content": [{"type": "text", "text": "echo: 你好"}]}

        monkeypatch.setattr(mcp_actions, "client_for", lambda srv, timeout=None: _FakeClient(srv, timeout=timeout))
        result = spec.execute(superuser, {"text": "你好"})
        assert result["ok"] is True
        assert result["data"] == {"tool": "echo", "is_error": False, "text": "echo: 你好"}
        # 管理面配置 120s，AI 链路钳到 30s（执行请求同步在等结果）
        assert captured["timeout"] == mcp_actions.AI_CALL_TIMEOUT_CAP
        assert captured["call"] == ("echo", {"text": "你好"})
        log = OperationLog.objects.filter(module="AI:mcp:client", status_code=1000).first()
        assert log is not None
        assert json.loads(log.changes)["channel"] == "mcp_tool"

    def test_remote_error_audited(self, superuser, monkeypatch):
        server = _server()
        spec = mcp_action_specs(superuser)[f"mcp.{server.pk}.echo"]

        def _boom(__srv, timeout=None):
            raise mcp.McpClientError("MCP server error: boom")

        monkeypatch.setattr(mcp_actions, "client_for", _boom)
        result = spec.execute(superuser, {"text": "x"})
        assert result["ok"] is False and "boom" in result["detail"]
        assert OperationLog.objects.filter(module="AI:mcp:client", status_code=1001).exists()
