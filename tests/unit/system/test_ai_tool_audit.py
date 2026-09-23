# -*- coding: utf-8 -*-
"""AI 工具面巡检（AI-3）单测：路由面 × 声明面比对 + OpenAPI x-ai-* 元数据。

- 巡检命令三态：已声明 / triage 豁免（含理由）/ 缺口（待注册或未登记新资源域）；
- **缺口清零守护**（入 CI）：全量路由无未注册候选 —— 新资源域出现即红，强制做一次
  「注册动作 or 登记不 AI 化」的决策（`system/utils/ai_tool_triage.py`）；
- --fail-on-gap：缺口非空时退出码 1；
- 豁免清单：AI 自身端点（api/system/ai/*）不计入候选；
- OpenAPI：声明式动作按 (method, path) 命中即注入 x-ai-*，视图 ai_meta 可覆盖。
"""

from io import StringIO

import pytest
from django.core.management import call_command

from common.swagger.ai_meta import ai_operation_meta, normalize_path, operation_extensions

pytestmark = pytest.mark.django_db


def run_audit(**options):
    out = StringIO()
    call_command("ai_tool_audit", stdout=out, **options)
    return out.getvalue()


class TestAuditCommand:
    def test_report_only_does_not_fail(self):
        output = run_audit()
        assert "未注册候选" in output and "失效声明" in output

    def test_json_output_shape(self):
        import json

        payload = json.loads(run_audit(**{"json": True}))
        assert payload["routes"] > 100 and payload["declared"] > 10
        assert isinstance(payload["candidates"], list) and isinstance(payload["stale"], list)

    def test_no_unconfirmed_gap(self):
        """缺口清零守护（CI）：未注册候选必须为空（triage 收敛后 --fail-on-gap 可入 CI）。"""
        import json

        payload = json.loads(run_audit(**{"json": True}))
        assert payload["candidates"] == [], f"存在未登记缺口：{payload['candidates'][:5]}"

    def test_ai_self_endpoints_exempted(self):
        import json

        payload = json.loads(run_audit(**{"json": True, "show_exempted": True}))
        assert all(not row["url"].startswith("api/system/ai/") for row in payload["candidates"])
        assert any(row["url"].startswith("api/system/ai/") for row in payload["exempted"])
        # 声明的动作必须命中路由（失效声明为空 = 注册表与路由面一致）
        assert payload["stale"] == []

    def test_triage_exempt_carries_reason(self):
        import json

        payload = json.loads(run_audit(**{"json": True, "show_exempted": True}))
        triaged = [row for row in payload["exempted"] if row.get("url", "").startswith("api/notifications/")]
        assert triaged and all(row.get("reason") for row in triaged)

    def test_fail_on_gap_exit_code(self, monkeypatch):
        """注入一个未登记资源域的路由：--fail-on-gap 退出码 1（新模块出生即被感知）。"""
        from system.utils import permission_sync
        from system.utils.permission_sync.types import RouteInfo

        fake = RouteInfo(
            view="demo.views.FakeViewSet",
            name="fake-new-resource",
            url="api/system/fake-new-resource",
            sample="api/system/fake-new-resource",
            actions={"get": "list"},
            view_cls=None,
            requires_permission=True,
        )
        real = permission_sync.build_route_index()

        def _patched():
            return [*real, fake]

        monkeypatch.setattr(permission_sync, "build_route_index", _patched)
        with pytest.raises(SystemExit):
            run_audit(**{"fail_on_gap": True})


class TestOpenApiMetadata:
    def test_normalize_path(self):
        assert normalize_path("/api/system/user/{pk}/") == "/api/system/user/<pk>"
        assert normalize_path("/api/system/user") == "/api/system/user"

    def test_declared_action_injected(self):
        meta = ai_operation_meta(None, "/api/system/user/{pk}", "patch")
        assert meta["action"] == "user.update"
        assert meta["required-permissions"] == ["PATCH /api/system/user/<pk>"]
        assert meta["requires-approval"] == "conditional"
        assert "guidance" in meta

    def test_undeclared_operation_zero_change(self):
        assert ai_operation_meta(None, "/api/system/user/export-async", "post") == {}

    def test_view_ai_meta_overrides(self):
        class View:
            ai_meta = {"visible": False, "guidance": "内部端点，不对外开放"}

        meta = ai_operation_meta(View(), "/api/system/user/{pk}", "get")
        assert meta["visible"] is False
        assert meta["guidance"] == "内部端点，不对外开放"

    def test_extension_prefix(self):
        extensions = operation_extensions({"action": "x.y", "visible": True})
        assert extensions == {"x-ai-action": "x.y", "x-ai-visible": True}
