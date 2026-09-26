# -*- coding: utf-8 -*-
"""AI 统一工具层扩展集成测试：扩容后的声明式动作 + 审批穿透 + 多草稿串联。

覆盖：
- 只读动作（monitor.overview）：dispatch 复用监控接口 + 落审计行；
- 权限双门：无业务权限点的用户不得执行（含读类动作）；
- 写入动作（config.set）：生效 + 审计（超管验证功能链路）；
- dashboard.overview 聚合：一次调用合并 6 个统计端点；
- 高危动作全链路（role.delete）：412 建单 → 他审通过 → 携令牌重发 → 执行成功；
  且在业务层 APPROVAL_REQUIRED_PATHS 命中同一路径时不再重复建单（审批穿透）；
- 多草稿串联：parse_draft 兼容新契约（actions 数组）与旧契约（单对象），
  超限/未知动作拒绝。
"""

import json

import pytest
from django.urls import resolve

from approval.models.approval import ApprovalRequest
from approval.utils.approval import approve_request
from common.core.config import SysConfig
from system.models import OperationLog, SystemConfig, UserRole

pytestmark = pytest.mark.django_db

EXECUTE_URL = "/api/system/ai/assistant/action/execute"
MCP_URL = "/api/system/ai/mcp"

AI_EXECUTE_PERM = (("actionExecute:AiAssistant", "api/system/ai/assistant/action/(interpret/stream|execute)$", "POST"),)
MONITOR_PERMS = (("list:SystemMonitor", r"api/system/monitor/overview$", "GET"),)
MONITOR_EVENTS_PERMS = (("events:SystemMonitor", r"api/system/monitor/events$", "GET"),)
DELETE_ROLE_PERMS = (("destroy:SystemRole", r"api/system/role/(?P<pk>[^/.]+)$", "DELETE"),)
UPDATE_CONFIG_PERMS = (("partialUpdate:SystemConfig", r"api/system/config/system/(?P<pk>[^/.]+)$", "PATCH"),)


def grant_perms(role, menu_factory, perms):
    from system.models import Menu

    for name, path, method in perms:
        perm = Menu.objects.filter(name=name).first() or menu_factory(name, path=path, method=method)
        role.menu.add(perm)


@pytest.fixture
def ai_action_settings(settings):
    settings.AI_ASSISTANT_ENABLED = True
    settings.AI_BASE_URL = "https://ai.example.com/v1"
    settings.AI_API_KEY = "sk-test"
    settings.AI_MODEL = "test-model"
    settings.AI_ACTION_ENABLED = True
    return settings


@pytest.fixture
def actor(db, role, menu_factory):
    """持有 AI 执行端点权限的普通用户（业务权限按用例追加）。"""
    from system.models import UserInfo

    user = UserInfo.objects.create_user(username="ai_ext_actor", password="Test@123456", nickname="扩展执行人")
    user.roles.add(role)
    grant_perms(role, menu_factory, AI_EXECUTE_PERM)
    return user


@pytest.fixture
def actor_client(api_client, actor):
    api_client.force_authenticate(user=actor)
    return api_client


class TestReadActions:
    """只读动作：监控总览（dispatch 复用监控接口，结果以调用者数据权限口径返回）。"""

    def test_monitor_overview_as_superuser(self, auth_client, ai_action_settings):
        response = auth_client.post(EXECUTE_URL, {"action": "monitor.overview", "params": {}}, format="json")
        assert response.data["code"] == 1000, response.data
        audit = OperationLog.objects.get(module="AI:action")
        assert audit.status_code == 1000
        assert audit.auth_type == OperationLog.AuthType.AI

    def test_requires_business_permission(self, actor_client, ai_action_settings):
        """权限双门：仅有 AI 执行端点权限、无监控菜单权限点的用户不得执行。"""
        response = actor_client.post(EXECUTE_URL, {"action": "monitor.overview", "params": {}}, format="json")
        assert response.data["code"] == 1001
        assert not OperationLog.objects.filter(module="AI:action", status_code=1000).exists()

    def test_monitor_overview_with_business_perm(self, actor_client, ai_action_settings, actor, menu_factory):
        grant_perms(actor.roles.first(), menu_factory, MONITOR_PERMS)
        response = actor_client.post(EXECUTE_URL, {"action": "monitor.overview", "params": {}}, format="json")
        assert response.data["code"] == 1000, response.data


class TestConfigSetAction:
    """系统配置修改（高危：强制审批在 TestHighRiskApproval 覆盖，这里验证写入链路）。"""

    def test_update_value(self, auth_client, ai_action_settings):
        row = SystemConfig.objects.create(key="TEST_AI_KEY", value='{"a": 1}', description="AI 测试配置")
        response = auth_client.post(
            EXECUTE_URL,
            {"action": "config.set", "params": {"pk": str(row.pk), "value": '{"a": 2}'}},
            format="json",
        )
        assert response.data["code"] == 1000, response.data
        row.refresh_from_db()
        assert json.loads(row.value) == {"a": 2}
        assert OperationLog.objects.filter(module="AI:action", status_code=1000).exists()


class TestDashboardOverview:
    def test_aggregates_six_metrics(self, auth_client, ai_action_settings):
        response = auth_client.post(EXECUTE_URL, {"action": "dashboard.overview", "params": {}}, format="json")
        assert response.data["code"] == 1000, response.data
        metrics = response.data["data"]["metrics"]
        assert len(metrics) == 6
        assert set(metrics) == {
            "user_login_total",
            "user_total",
            "user_registered_trend",
            "user_login_trend",
            "today_operate_total",
            "user_active",
        }

    def test_result_renders_as_table(self, auth_client, ai_action_settings):
        """结果表形状（columns/rows/total）：前端 AiResultTable 直接渲染。

        回归背景：metrics 原始对象无表格形状 → 前端只显示 detail 文案看不到数据；
        且 dashboard 端点把 results/percent/count 放响应顶层（非 data），提取必须兼容。
        """
        response = auth_client.post(EXECUTE_URL, {"action": "dashboard.overview", "params": {}}, format="json")
        data = response.data["data"]
        assert len(data["columns"]) == 4
        assert data["total"] == len(data["rows"]) == 6
        # 顶层 kwargs 口径（results/percent/count）必须提取到，不能是空 {}
        user_total = data["metrics"]["user_total"]
        assert user_total.get("count") is not None or isinstance(user_total.get("results"), list)
        rows = data["rows"]
        cols = data["columns"]
        assert all(set(row) == set(cols) for row in rows)  # 契约：columns 即行键
        assert any(row[cols[1]] for row in rows)
        # 「用户数量」指标行存在且数值非空（用户数查询场景）
        from django.utils.translation import gettext_lazy as _

        user_total_label = str(_("Total users"))
        assert user_total_label in [row[cols[0]] for row in rows]

    def test_repeat_execute_hits_response_cache(self, auth_client, ai_action_settings):
        """缓存命中路径（回归）：dashboard 端点挂 60s cache_response，缓存命中返回
        渲染后的 HttpResponse（无 .data）——重复执行必须仍能取到指标数据。"""
        first = auth_client.post(EXECUTE_URL, {"action": "dashboard.overview", "params": {}}, format="json")
        assert first.data["code"] == 1000
        second = auth_client.post(EXECUTE_URL, {"action": "dashboard.overview", "params": {}}, format="json")
        assert second.data["code"] == 1000, second.data
        assert len(second.data["data"]["metrics"]) == 6
        assert second.data["data"]["rows"]

    def test_available_to_normal_user_with_execute_permission(self, actor_client, ai_action_settings):
        """白名单统计端点：普通用户（仅持 AI 执行端点权限）同样可用。

        回归背景：dashboard 端点在访问白名单（登录即可访问、无菜单权限点），
        业务权限预检若只认菜单权限点，普通用户会在工具目录 / 草稿 / 执行三处全被拦，
        动作只对超管可见——与实际 HTTP 访问控制（所有登录用户可访问）不一致。
        """
        response = actor_client.post(EXECUTE_URL, {"action": "dashboard.overview", "params": {}}, format="json")
        assert response.data["code"] == 1000, response.data
        assert len(response.data["data"]["metrics"]) == 6

    def test_visible_in_tool_catalog_for_normal_user(self, actor_client, actor, role, menu_factory, ai_action_settings):
        """白名单动作必须出现在普通用户的工具目录（LLM 目录与执行口径一致）。"""
        grant_perms(
            role,
            menu_factory,
            (("status:AiAssistant", r"api/system/ai/assistant/(status|metrics|history|tools)$", "GET"),),
        )
        response = actor_client.get("/api/system/ai/assistant/tools")
        assert response.data["code"] == 1000, response.data
        names = {entry["name"] for entry in response.data["data"]["tools"]}
        assert "dashboard.overview" in names
        assert "task.enable" not in names  # 未授予 SystemTask 权限，不得出现


class TestHighRiskApproval:
    """高危动作（requires_approval）全链路：412 → 审批 → 重发 → 审批穿透。

    PERMISSION_DATA_ENABLED 关闭：聚焦审批协议链路本身（actor 无数据权限授权时
    角色查询集 fail-closed 为 none()，属数据权限模块的独立语义，非本用例被测面）。
    """

    def _make_role(self, name="待删角色"):
        return UserRole.objects.create(name=name, code=name)

    def test_full_approval_loop_without_business_gate(
        self, actor_client, ai_action_settings, actor, superuser, menu_factory, settings
    ):
        settings.PERMISSION_DATA_ENABLED = False
        grant_perms(actor.roles.first(), menu_factory, DELETE_ROLE_PERMS)
        role = self._make_role()
        body = {"action": "role.delete", "params": {"pk": role.name}}

        first = actor_client.post(EXECUTE_URL, body, format="json")
        assert first.status_code == 412, first.data
        assert first.data["code"] == 1002
        assert first.data["type"] == "approval_required"
        approval_id = first.data["data"]["approval_id"]
        approval = ApprovalRequest.objects.get(pk=approval_id)
        assert str(approval.creator.pk) == str(actor.pk)
        assert UserRole.objects.filter(pk=role.pk).exists()  # 未执行

        ok, __ = approve_request(approval, superuser)
        assert ok

        second = actor_client.post(EXECUTE_URL, body, format="json", HTTP_X_APPROVAL_ID=approval_id)
        assert second.data["code"] == 1000, second.data
        assert not UserRole.objects.filter(pk=role.pk).exists()
        # 一次性令牌：角色已删后重放，参数解析在消费令牌前即失败（同样拒绝重放）
        third = actor_client.post(EXECUTE_URL, body, format="json", HTTP_X_APPROVAL_ID=approval_id)
        assert third.data["code"] == 1001
        assert not UserRole.objects.filter(pk=role.pk).exists()

    def test_business_gate_pass_through_no_double_approval(
        self, actor_client, ai_action_settings, actor, superuser, menu_factory, settings
    ):
        """审批穿透：业务层 APPROVAL_REQUIRED_PATHS 命中 role 删除路径时，
        AI 层已审批的操作在内层 dispatch 不再重复建单（否则第二张单不可达）。"""
        settings.PERMISSION_DATA_ENABLED = False
        grant_perms(actor.roles.first(), menu_factory, DELETE_ROLE_PERMS)
        SysConfig.set_value("APPROVAL_REQUIRED_PATHS", [r"api/system/role/"])
        try:
            role = self._make_role("穿透角色")
            body = {"action": "role.delete", "params": {"pk": role.name}}
            first = actor_client.post(EXECUTE_URL, body, format="json")
            assert first.status_code == 412
            approval_id = first.data["data"]["approval_id"]
            assert approve_request(ApprovalRequest.objects.get(pk=approval_id), superuser)[0]

            second = actor_client.post(EXECUTE_URL, body, format="json", HTTP_X_APPROVAL_ID=approval_id)
            assert second.data["code"] == 1000, second.data
            assert not UserRole.objects.filter(pk=role.pk).exists()
            # 只有 AI 层一张审批单（业务层未重复建单；AI 层审批单的 method=POST，
            # 因为审批建在 action/execute 端点上）
            assert ApprovalRequest.objects.filter(creator=actor, method="POST").count() == 1
        finally:
            SysConfig.set_value("APPROVAL_REQUIRED_PATHS", [])

    def test_mcp_rejects_approval_required_action(self, api_client, ai_action_settings, role, menu_factory):
        """MCP 通道无审批协议：requires_approval 动作一律拒绝并引导走 Web 控制台。

        用非超管验证（超管豁免审批，见 requires_approval_high_risk）。
        """
        from system.models import UserInfo

        user = UserInfo.objects.create_user(username="ai_mcp_actor", password="Test@123456", nickname="MCP执行人")
        user.roles.add(role)
        grant_perms(
            role,
            menu_factory,
            (("mcp:AiMcp", r"api/system/ai/mcp$", "POST"),) + DELETE_ROLE_PERMS,
        )
        api_client.force_authenticate(user=user)
        payload = {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {"name": "role.delete", "arguments": {"pk": "x"}},
        }
        response = api_client.post(MCP_URL, payload, format="json")
        result = response.json()["result"]
        assert result["isError"] is True
        assert "web console" in result["content"][0]["text"] or "控制台" in result["content"][0]["text"]


class TestMultiDrafts:
    """多动作草稿串联：parse_draft 新契约（actions 数组）+ 旧契约兼容。"""

    def _make_stream(self, monkeypatch, text):
        def fake_stream(self, messages, **kwargs):
            yield {"type": "content", "text": text}

        monkeypatch.setattr("common.sdk.ai.chat.ChatCompletionsClient.chat_stream", fake_stream)

    INTERPRET_URL = "/api/system/ai/assistant/action/interpret/stream"

    def test_single_actions_array(self, actor_client, ai_action_settings, actor, monkeypatch, menu_factory):
        grant_perms(actor.roles.first(), menu_factory, MONITOR_PERMS)
        text = json.dumps(
            {"actions": [{"action": "monitor.overview", "params": {}, "summary": "看监控"}]},
            ensure_ascii=False,
        )
        self._make_stream(monkeypatch, text)
        response = actor_client.post(self.INTERPRET_URL, {"message": "看下监控"}, format="json")
        frames = _parse_sse_frames(response)
        done = frames[-1][1]
        assert done["kind"] == "draft"
        assert len(done["drafts"]) == 1
        assert done["draft"]["action"] == "monitor.overview"

    def test_multiple_drafts_chained(self, actor_client, ai_action_settings, actor, monkeypatch, menu_factory):
        """两步串联：查监控 + 查告警事件，一次产出两张确认卡片。"""
        from system.utils.ai_registry_ops import ACTION_MONITOR_EVENTS, ACTION_MONITOR_OVERVIEW

        grant_perms(actor.roles.first(), menu_factory, MONITOR_PERMS)
        grant_perms(actor.roles.first(), menu_factory, MONITOR_EVENTS_PERMS)
        text = json.dumps(
            {
                "actions": [
                    {"action": ACTION_MONITOR_OVERVIEW, "params": {}, "summary": "看总览"},
                    {"action": ACTION_MONITOR_EVENTS, "params": {}, "summary": "看告警"},
                ]
            },
            ensure_ascii=False,
        )
        self._make_stream(monkeypatch, text)
        response = actor_client.post(self.INTERPRET_URL, {"message": "看下监控和告警"}, format="json")
        frames = _parse_sse_frames(response)
        done = frames[-1][1]
        assert done["kind"] == "draft"
        assert [item["action"] for item in done["drafts"]] == [ACTION_MONITOR_OVERVIEW, ACTION_MONITOR_EVENTS]
        assert done["draft"]["action"] == ACTION_MONITOR_OVERVIEW  # draft 恒为首个（旧渲染兜底）

    def test_over_limit_rejected(self, actor_client, ai_action_settings, actor, monkeypatch, menu_factory):
        grant_perms(actor.roles.first(), menu_factory, MONITOR_PERMS)
        text = json.dumps(
            {"actions": [{"action": "monitor.overview", "params": {}, "summary": "s"}] * 4},
            ensure_ascii=False,
        )
        self._make_stream(monkeypatch, text)
        response = actor_client.post(self.INTERPRET_URL, {"message": "连环查询"}, format="json")
        frames = _parse_sse_frames(response)
        assert frames[-1][0] == "error"
        assert "max" in str(frames[-1][1]["detail"]).lower() or "最多" in str(frames[-1][1]["detail"])

    def test_unknown_action_in_chain_rejected(self, actor_client, ai_action_settings, actor, monkeypatch, menu_factory):
        grant_perms(actor.roles.first(), menu_factory, MONITOR_PERMS)
        text = json.dumps(
            {
                "actions": [
                    {"action": "monitor.overview", "params": {}, "summary": "看总览"},
                    {"action": "user.destroy_all", "params": {}, "summary": "越权"},
                ]
            },
            ensure_ascii=False,
        )
        self._make_stream(monkeypatch, text)
        response = actor_client.post(self.INTERPRET_URL, {"message": "先查后删"}, format="json")
        frames = _parse_sse_frames(response)
        assert frames[-1][0] == "error"

    def test_legacy_single_object_contract(self, actor_client, ai_action_settings, actor, monkeypatch, menu_factory):
        """旧契约（单动作对象）继续可用（兼容既有桩/E2E/二开脚本）。"""
        grant_perms(actor.roles.first(), menu_factory, MONITOR_PERMS)
        self._make_stream(
            monkeypatch,
            json.dumps({"action": "monitor.overview", "params": {}, "summary": "旧契约"}, ensure_ascii=False),
        )
        response = actor_client.post(self.INTERPRET_URL, {"message": "看监控"}, format="json")
        frames = _parse_sse_frames(response)
        done = frames[-1][1]
        assert done["kind"] == "draft"
        assert done["draft"]["action"] == "monitor.overview"
        assert done["drafts"][0]["action"] == "monitor.overview"


def _parse_sse_frames(response):
    content = response.streaming_content
    if getattr(response, "is_async", False):
        from asgiref.sync import async_to_sync

        async def _gather():
            return [chunk async for chunk in content]

        chunks = async_to_sync(_gather)()
    else:
        chunks = content
    frames, buffer = [], b""
    for chunk in chunks:
        buffer += chunk if isinstance(chunk, bytes) else str(chunk).encode()
        while b"\n\n" in buffer:
            raw, buffer = buffer.split(b"\n\n", 1)
            event, data = "message", ""
            for line in raw.decode().splitlines():
                if line.startswith("event:"):
                    event = line[len("event:") :].strip()
                elif line.startswith("data:"):
                    data = line[len("data:") :].strip()
            frames.append((event, json.loads(data) if data else {}))
    return frames


class TestDictItemsResolution:
    """dict.items 路径与 detail 路由同形（<pk> 可匹配 items）：必须解析到 items action。"""

    def test_dict_items_resolves_to_items_action(self):
        match = resolve("/api/system/dict/items")
        # detail 路由的 get action 是 retrieve；命中 items action 才证明路由次序正确
        assert match.func.actions.get("get") == "items"
        assert "DataDictViewSet" in repr(match.func)
