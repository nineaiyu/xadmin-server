# -*- coding: utf-8 -*-
"""动态目录专属守护测试：key 唯一 / 目录可见 ⇔ get_action 可解析 / 总量上限。

背景：外接 MCP 工具动作不入静态注册表（import 期守护 test_ai_api_registry_guard
按静态字典对账，动态条目并进去会失真），因此动态侧需要自己的守护——尤其防
「目录可见但执行解析 404」这类静默失配（动态目录与 get_action 是两条代码路径，
过滤口径漂移时两边会不一致，用户在确认卡片点执行才发现 404）。
"""

import re

import pytest

from ai.models.mcp import McpServer
from ai.utils import ai_mcp_actions as mcp_actions
from ai.utils.ai_actions import ACTION_SPECS, get_action
from ai.utils.ai_mcp_actions import McpActionSpec, mcp_action_specs
from ai.utils.ai_tool_catalog import openai_tools, tool_catalog

pytestmark = [pytest.mark.django_db]

TAIL_FORMAT = re.compile(r"^[a-z0-9_]+$")


def _make_server(name: str, tools: list, allowed: list | None = None, **extra) -> McpServer:
    """直接落库造快照（不经 HTTP 同步：守护关注目录推导，不关注同步链路）。"""
    snapshot = [
        {
            "name": tool,
            "description": f"工具 {tool}",
            "read_only": True,
            "input_schema": {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
        }
        for tool in tools
    ]
    defaults = {
        "name": name,
        "url": "https://mcp.example.com/mcp",
        "allowed_tools": tools if allowed is None else allowed,
        "tools_snapshot": snapshot,
        "expose_to_ai": True,
    }
    defaults.update(extra)
    return McpServer.objects.create(**defaults)


class TestDynamicKeyGuard:
    def test_keys_unique_and_well_formed(self, superuser):
        """规范化冲突（fs.read / fs-read）与跨服务器同名都不得产生重复 key。"""
        _make_server("s-a", ["fs.read", "fs-read", "fs_read"])
        _make_server("s-b", ["echo"])
        keys = list(mcp_action_specs(superuser))
        assert len(keys) == len(set(keys)), "动态 key 必须唯一"
        for key in keys:
            assert key.startswith("mcp.")
            head, tail = key.rsplit(".", 1)
            assert TAIL_FORMAT.match(tail), f"key 尾段必须已规范化: {key}"
            # 中段为服务器 UUID（含连字符），尾段独立于 head，不解析 key 定位
            assert head.count(".") == 1

    def test_conflict_gets_sequence_suffix(self, superuser):
        server = _make_server("s", ["fs.read", "fs-read", "fs_read"])
        specs = mcp_action_specs(superuser)
        tails = sorted(key.rsplit(".", 1)[-1] for key in specs)
        assert tails == ["fs_read", "fs_read_2", "fs_read_3"]
        # 同序工具名与 key 对账：spec 持有原始 tool_name（不解析 key）
        by_tail = {key.rsplit(".", 1)[-1]: spec.tool_name for key, spec in specs.items()}
        assert by_tail["fs_read"] == "fs.read"
        assert str(server.pk) in list(specs)[0]

    def test_static_registry_not_polluted_by_dynamic(self, superuser):
        """动态 spec 不得并入静态注册表（守护失真 = import 期对账失效）。"""
        _make_server("s", ["echo"])
        specs = mcp_action_specs(superuser)
        assert specs
        assert not [key for key in ACTION_SPECS if str(key).startswith("mcp.")]

    def test_dynamic_key_never_matches_static_key_format_shadowing(self, superuser):
        """动态解析未命中回落静态查找：静态 mcp.* 域不被动态前缀遮蔽。"""
        _make_server("s", ["echo"])
        assert get_action("mcp.no-such.ghost", superuser) is None


class TestCatalogResolvableGuard:
    def test_catalog_visible_iff_get_action_resolves(self, superuser):
        """目录可见 ⇔ get_action 可解析（防「确认卡片可见、点执行 404」）。"""
        _make_server("s-a", ["echo", "fs.read"])
        _make_server("s-b", ["echo"], allowed=[])  # 白名单为空：整台不可见
        catalog_keys = [entry["name"] for entry in tool_catalog(superuser) if str(entry["name"]).startswith("mcp.")]
        specs = mcp_action_specs(superuser)
        assert set(catalog_keys) == set(specs), "tool_catalog 与动态 spec 集合必须一致"
        for key in catalog_keys:
            spec = get_action(key, superuser)
            assert spec is not None, f"目录可见但解析不到（执行会 404）: {key}"
            assert spec.key == key
            # 与 execute_action 同链路的两个前置也必须通过（超管全权限）
            assert spec.available(superuser) is True
            assert spec.has_permission(superuser) is True

    def test_openai_tools_same_source(self, superuser):
        """原生 function calling 轨道与目录同源：可下发的工具必须全部可解析。"""
        _make_server("s", ["echo"])
        for entry in openai_tools(superuser):
            name = entry["function"]["name"]
            if not str(name).startswith("mcp."):
                continue
            assert get_action(name, superuser) is not None

    def test_without_user_dynamic_keys_unresolvable(self, superuser):
        """外部 MCP 端点（不传 user）fail-closed：动态 key 一律 Unknown action。"""
        _make_server("s", ["echo"])
        key = next(iter(mcp_action_specs(superuser)))
        assert get_action(key) is None
        assert get_action(key, None) is None


class TestDynamicCapGuard:
    def test_total_spec_cap(self, superuser, monkeypatch):
        """全目录动态 spec 总量上限：超限截断（先到先得，服务器按 name 稳定序）。"""
        monkeypatch.setattr(mcp_actions, "MAX_DYNAMIC_SPECS", 3)
        s1 = _make_server("s1", ["t1", "t2"])
        s2 = _make_server("s2", ["t3", "t4", "t5"])
        specs = mcp_action_specs(superuser)
        assert len(specs) == 3
        from_s1 = [key for key in specs if key.startswith(f"mcp.{s1.pk}.")]
        from_s2 = [key for key in specs if key.startswith(f"mcp.{s2.pk}.")]
        assert len(from_s1) == 2 and len(from_s2) == 1  # 按 name 序先取 s1，s2 被截断

    def test_per_server_tool_cap(self, superuser, monkeypatch):
        """单服务器工具上限（序列化器已限 100，此处防御手工数据）。"""
        monkeypatch.setattr(mcp_actions, "MAX_TOOLS_PER_SERVER", 2)
        _make_server("s", ["t1", "t2", "t3"])
        assert len(mcp_action_specs(superuser)) == 2


class TestSpecProtocolGuard:
    """动态 spec 与 ActionSpec 协议同形（混装目录的最低要求）。"""

    def test_protocol_surface(self, superuser):
        from ai.utils.ai_actions import ActionSpec

        _make_server("s", ["echo"])
        spec: McpActionSpec = next(iter(mcp_action_specs(superuser).values()))
        for attr in ("key", "label", "description", "params", "required_visits", "validate", "execute"):
            assert hasattr(spec, attr), f"动态 spec 缺协议字段: {attr}"
        # 行为契约：validate 返回 (clean, error) 二元组（execute 契约在
        # test_ai_mcp_actions.TestExecuteFailClosed 用桩客户端覆盖，此处不触网）
        assert isinstance(spec.params, dict)
        clean, error = spec.validate(superuser, {"text": "x"})
        assert error is None and clean == {"text": "x"}
        # 协议字段与 ActionSpec 的 dataclass 字段集一致（新增字段须同步审视协议）
        assert {f.name for f in ActionSpec.__dataclass_fields__.values()} <= {
            "key",
            "label",
            "description",
            "params",
            "required_visits",
            "validate",
            "execute",
            "requires_approval",
            "available",
        }
