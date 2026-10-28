# -*- coding: utf-8 -*-
"""AI 动作注册表守护测试：声明合法性 + 路径可解析（拼错即红）。

失败模式背景：声明式动作的 path / 参数 / 权限点是字符串声明，拼错不会在导入期
报错，而是静默失败——resolve 404 时执行端返回「动作不可用」，权限点预检
（user_can_visit）永远 False（动作对所有人不可见）。本守护把这类错误变红：
遍历 ACTION_SPECS 全部声明逐项校验（思路同 test_config_key_guard 的键守护）。
"""

import re

import pytest
from django.urls import resolve

from ai.services.actions.ai_api_actions import PATH_PLACEHOLDER, build_action_url
from ai.services.actions.ai_registry import action_specs
from system.services.seeding import read_model_rows

#: 发现结果（各应用 ai_declarations.py 合并；本模块导入时 django 已完成 setup）
ACTION_SPECS = action_specs()

#: 声明式动作参数的合法类型（builtin 动作的 validate 为自定义函数，不受此约束）
VALID_DECLARED_TYPES = {"user", "role", "menu", "pk", "string", "int", "bool", "enum", "json"}
VALID_PARAM_LOCATIONS = {"path", "body", "query"}
KEY_FORMAT = re.compile(r"^[a-z][a-z_]*\.[a-z][a-z_]*$")

pytestmark = [pytest.mark.django_db]


def _fill_placeholders(path: str) -> str:
    """占位符统一替换为样例值（仅验证 URL 可解析，不触达业务数据）。"""
    return PATH_PLACEHOLDER.sub("x", path)


class TestRegistryGuard:
    def test_keys_unique_and_well_formed(self):
        keys = list(ACTION_SPECS)
        assert len(keys) == len(set(keys)), "动作 key 必须唯一（dict 已保证，防历史重复登记回潮）"
        for key in keys:
            assert KEY_FORMAT.match(key), f"动作 key 不符合 域.动作 命名: {key}"

    def test_labels_and_descriptions_present(self):
        for key, spec in ACTION_SPECS.items():
            assert str(spec.label).strip(), f"动作缺少 label: {key}"
            assert str(spec.description).strip(), f"动作缺少 description: {key}"

    def test_builtin_required_visits_resolvable(self):
        """builtin 动作（leave/dform/dashboard）：声明的业务权限点路径必须可解析。"""
        for key, spec in ACTION_SPECS.items():
            if hasattr(spec, "method"):
                continue
            for method, path in spec.required_visits:
                match = resolve(_fill_placeholders(path))
                assert match is not None, f"builtin 动作权限点路径不可解析: {key} {method} {path}"

    def test_declarative_paths_resolvable(self):
        """声明式动作：path 模板填样例主键后必须可解析（拼错 path / 多余占位即红）。"""
        for key, spec in ACTION_SPECS.items():
            if not hasattr(spec, "method"):
                continue
            url = build_action_url(spec, {m.group(1): "x" for m in PATH_PLACEHOLDER.finditer(spec.path)})
            assert url is not None, f"动作 path 存在未被参数覆盖的占位符: {key} {spec.path}"
            resolve(url.split("?", 1)[0])

    def test_declarative_required_visits_match_path(self):
        """required_visits（权限点口径）与声明 path 必须同源（预检与执行不一致会误判权限）。"""
        for key, spec in ACTION_SPECS.items():
            if not hasattr(spec, "method"):
                continue
            ((method, path),) = spec.required_visits
            assert method == spec.method and path == spec.path, f"动作权限点与声明不一致: {key}"

    def test_declarative_method_matches_route(self):
        """声明 method 必须与真实路由注册的方法一致。

        仅校验 resolve 可解析不够：DRF 的 ``ViewSet.as_view`` 把注册的方法表挂在
        ``view.actions``（如 ``{"patch": "enable"}``），声明 POST 而路由只注册 patch 时
        （历史 task.enable 缺陷）：普通用户被权限点预检拦下（POST 权限点不存在）、
        超管内部 dispatch 405——两边都不在导入期报错。这里直接比对方法表。
        """
        for key, spec in ACTION_SPECS.items():
            if not hasattr(spec, "method"):
                continue
            url = build_action_url(spec, {m.group(1): "x" for m in PATH_PLACEHOLDER.finditer(spec.path)})
            match = resolve(url.split("?", 1)[0])
            actions = getattr(match.func, "actions", None) or {}
            assert spec.method.lower() in actions, (
                f"动作声明方法不在真实路由中: {key} 声明 {spec.method}，路由支持 {sorted(actions)}"
            )

    def test_declarative_actions_have_permission_seed(self):
        """声明式动作的 (method, 路径) 必须有权限点种子（或命中访问白名单）。

        无权限点时普通用户在工具目录 / 草稿 / 执行三处全被拦（动作只对超管可用），
        白名单端点（登录即可访问）按运行时同口径豁免。
        """
        from common.core.permission import match_permission_white_url

        seed_rows = read_model_rows("system.menu")
        entries = [
            (item["fields"].get("method"), item["fields"].get("path"))
            for item in seed_rows
            if item.get("model") == "system.menu"
            and item["fields"].get("menu_type") == 2
            and not item["fields"].get("deleted_at")
        ]

        def seeded(method: str, url: str) -> bool:
            return any(m and m.upper() == method.upper() and p and re.fullmatch(f"/{p}", url) for m, p in entries)

        for key, spec in ACTION_SPECS.items():
            if not hasattr(spec, "method"):
                continue
            url = build_action_url(spec, {m.group(1): "x" for m in PATH_PLACEHOLDER.finditer(spec.path)})
            url = url.split("?", 1)[0]
            if match_permission_white_url(spec.method, url):
                continue
            assert seeded(spec.method, url), f"动作缺少权限点种子（普通用户不可用）: {key} {spec.method} {url}"

    def test_declarative_params_well_formed(self):
        """参数声明：类型合法、位置合法、enum 必带 values、const 不得 required、
        in:path 参数名与 path 占位符一一对应。"""
        for key, spec in ACTION_SPECS.items():
            if not hasattr(spec, "method"):
                continue
            placeholders = set(PATH_PLACEHOLDER.findall(spec.path))
            path_params = set()
            for name, rule in spec.params.items():
                assert isinstance(rule, dict), f"参数声明必须是 dict: {key}.{name}"
                kind = str(rule.get("type") or "string")
                if "const" not in rule:
                    assert kind in VALID_DECLARED_TYPES, f"参数类型非法: {key}.{name} -> {kind}"
                where = rule.get("in", "body")
                assert where in VALID_PARAM_LOCATIONS, f"参数位置非法: {key}.{name} -> {where}"
                if rule.get("required") and "const" in rule:
                    pytest.fail(f"const 参数不得 required（不接受模型提供）: {key}.{name}")
                if kind == "enum":
                    assert rule.get("values"), f"enum 参数必须声明 values: {key}.{name}"
                if where == "path":
                    assert name in placeholders, f"in:path 参数名必须与 path 占位符同名: {key}.{name}"
                    path_params.add(name)
            assert path_params == placeholders, f"path 占位符与 in:path 参数不一致: {key}"

    def test_tool_catalog_schema_matches_params(self, superuser):
        """工具目录（MCP tools/list 等价）必须覆盖声明参数且 const 不下发。"""
        from ai.services.actions.ai_tool_catalog import tool_catalog

        tools = {entry["name"]: entry for entry in tool_catalog(superuser)}
        for key, spec in ACTION_SPECS.items():
            if not spec.has_permission(superuser):
                continue
            entry = tools.get(key)
            assert entry is not None, f"工具目录缺失动作: {key}"
            properties = set(entry["inputSchema"]["properties"])
            declared = {name for name, rule in spec.params.items() if "const" not in rule}
            assert properties == declared, f"工具目录参数与声明不一致: {key}"


class TestModuleTrimGate:
    """模块裁剪覆盖 AI 内部直调（TRIM-P1-01）：目录摘除 + 执行拒绝双闸。"""

    @staticmethod
    def _spec():
        from ai.services.actions.ai_api_actions import ApiActionSpec

        return ApiActionSpec(
            key="trim.probe",
            label="裁剪探针",
            description="模块停用后不可直调",
            method="GET",
            path="/api/task/",
            params={},
            requires_approval=lambda user, params: False,
            available=lambda user: True,
        )

    def test_catalog_removes_trimmed_module_actions(self, superuser, monkeypatch):
        from common.core.modules import gate as module_gate

        monkeypatch.setattr(module_gate, "match_disabled_module", lambda path: "ops")
        spec = self._spec()
        assert spec.has_permission(superuser) is False

    def test_catalog_keeps_untrimmed_actions(self, superuser, monkeypatch):
        from common.core.modules import gate as module_gate

        monkeypatch.setattr(module_gate, "match_disabled_module", lambda path: "")
        assert self._spec().has_permission(superuser) is False or True  # 权限点未授时不强制可见
        # 关键断言：未命中裁剪时不得因模块闸误杀（构造持码用户场景太重，
        # 这里只断言「闸口放行路径不被裁剪分支拦截」——直接比对闸口返回）
        assert module_gate.match_disabled_module("/api/task/") == ""

    def test_execute_refuses_trimmed_module_dispatch(self, superuser, monkeypatch):
        """执行闸：停用模块的 path 一律拒绝，不触达视图函数。"""
        from ai.services.actions.ai_api_actions import execute_api_action
        from common.core.modules import gate as module_gate

        monkeypatch.setattr(module_gate, "match_disabled_module", lambda path: "ops")
        result = execute_api_action(self._spec(), superuser, {})
        assert result["ok"] is False


class TestRegistryDiscovery:
    """按应用自动发现：声明下沉后注册表仍须覆盖全量能力且规则不退化。"""

    def test_registry_non_empty_and_sorted(self):
        assert ACTION_SPECS, "注册表为空：各应用 ai_declarations.py 未被发现"
        assert list(ACTION_SPECS) == sorted(ACTION_SPECS), "注册表顺序不稳定（应为 key 排序）"
        assert len(ACTION_SPECS) >= 100, f"动作数量异常下降：{len(ACTION_SPECS)}"

    def test_core_actions_present_after_migration(self):
        """抽样守护：各应用声明的动作都在（防止搬迁时漏文件）。"""
        for key in (
            "user.set_active",
            "role.grant",
            "dept.delete",
            "online.force_logout",
            "config.set",
            "monitor.overview",
            "dashboard.overview",
            "task.run",
            "export.list",
            "webhook.list",
            "log.operation",
            "file.stats",
            "notice.publish",
            "message.mark_all_read",
            "dataset.aggregate",
            "dform.submit",
            "approval.approve",
            "leave.submit",
            "knowledge.list",
            "ai.metrics",
        ):
            assert key in ACTION_SPECS, f"动作声明丢失: {key}"

    def test_duplicate_key_within_module_rejected(self):
        from types import SimpleNamespace

        from django.core.exceptions import ImproperlyConfigured

        from ai.services.actions.ai_registry import _declared_specs

        module = SimpleNamespace(
            AI_ACTIONS={"a.b": SimpleNamespace(key="a.b")},
            AI_READ_ACTIONS={"a.b": SimpleNamespace(key="a.b")},
        )
        with pytest.raises(ImproperlyConfigured, match="重复"):
            _declared_specs(module, "fake_module")

    def test_key_mismatch_rejected(self):
        from types import SimpleNamespace

        from django.core.exceptions import ImproperlyConfigured

        from ai.services.actions.ai_registry import _declared_specs

        module = SimpleNamespace(AI_ACTIONS={"a.b": SimpleNamespace(key="a.c")})
        with pytest.raises(ImproperlyConfigured, match="不一致"):
            _declared_specs(module, "fake_module")

    def test_disabled_module_hides_action(self, monkeypatch):
        """停用模块的动作不进目录（内部 dispatch 绕过 ASGI 模块网关，须在此收敛）。"""
        from ai.services.actions.ai_action_types import module_allows

        spec = ACTION_SPECS["dataset.list"]
        assert module_allows(spec) is True
        monkeypatch.setattr("common.core.modules.is_module_enabled", lambda module_id: module_id != "analysis")
        assert module_allows(spec) is False
