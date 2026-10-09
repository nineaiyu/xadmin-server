# -*- coding: utf-8 -*-
"""示例二开插件（``examples/plugins/xadmin-demo-plugin``）守卫：三条接入通道 + 模块裁剪。

口径：插件本体是仓库内的**示例分发包**（默认不进宿主 INSTALLED_APPS，也不装进环境），
本文件按「已安装」语境驱动它——

- ``sys.path`` 注入等价 ``pip install -e`` 的 import 面（包代码可导入）；
- entry point 装配用插件 ``pyproject.toml`` 的**真实声明**构造 ``EntryPoint``，
  驱动真实装配函数 ``common.contracts.load_contract_entry_points``（声明与装配同源，
  改 pyproject 不更新这里就会红）；
- app 侧声明（modules.py / config.py / tasks.py）经 app registry 注入走真实发现链路。

真机安装（``pip install`` + ``XADMIN_APPS`` 注册）后的端到端验证步骤见插件 README
「验证清单」与 ``docs/guide/plugin-development.md``。

清理纪律：注入类用例一律在 finally 里 ``unregister_contract``；动态注册的 app 配置
用 monkeypatch 回退，并清 ``get_app_template_dirs`` 进程级缓存（同
``test_module_trim_drill.py`` 的既有教训）。
"""

import importlib
import importlib.metadata
import sys
import tomllib
from pathlib import Path

import pytest
from django.apps import apps as django_apps
from django.test import Client

from common.celery.decorator import get_register_period_tasks
from common.core.modules import (
    MENU_TYPE_DIRECTORY,
    MENU_TYPE_PERMISSION,
    ModuleTrimWebsocketMiddleware,
    all_module_specs,
    compute_hidden_menu_pks,
    disabled_permission_prefixes,
    is_module_enabled,
    is_ws_path_trimmed,
    match_disabled_module,
    module_index,
    permission_prefixes_of,
    preview_modules,
    reset_module_state,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
PLUGIN_DIR = REPO_ROOT / "examples" / "plugins" / "xadmin-demo-plugin"
PLUGIN_PACKAGE = "xadmin_demo_plugin"
MODULE_ID = "demo_plugin"
SAMPLE_REST = "/api/plugin-demo/note"
SAMPLE_WS = "/ws/plugin-demo/"

pytestmark = pytest.mark.django_db


@pytest.fixture(scope="module", autouse=True)
def plugin_importable():
    """插件包按路径可导入（等价 ``pip install -e`` 后的 import 面）。"""

    entry = str(PLUGIN_DIR)
    sys.path.insert(0, entry)
    try:
        yield entry
    finally:
        if entry in sys.path:
            sys.path.remove(entry)
        for name in [key for key in sys.modules if key == PLUGIN_PACKAGE or key.startswith(f"{PLUGIN_PACKAGE}.")]:
            sys.modules.pop(name, None)


@pytest.fixture
def plugin_installed_as_app(monkeypatch):
    """把插件 app 注入 app registry（等价 XADMIN_APPS 注册后的发现语境）。

    用真实 ``AppConfig`` 写入 ``apps.app_configs``：插件的 models.py 靠
    ``get_containing_app_config`` 认领——仅 mock ``get_app_configs`` 不足以让插件包内的
    模型可导入（config.py → urls.py → views.py → models.py 链路会断）。

    退出时清理 ``all_models`` 注册并清缓存：模型表注册是全局副作用，残留会让
    「按模型列举期望表」的 schema 判据在同 worker 后续用例误报（既有教训）。
    """

    from xadmin_demo_plugin.apps import XadminDemoPluginConfig

    config = XadminDemoPluginConfig(PLUGIN_PACKAGE, importlib.import_module(PLUGIN_PACKAGE))
    # 手工构造的 AppConfig 需补 registry 反向引用与模型表（框架 Apps.populate 同款两步）；
    # 顺序：先进 app_configs（模型认领靠 get_containing_app_config 查该表）再 import_models
    monkeypatch.setitem(django_apps.app_configs, PLUGIN_PACKAGE, config)
    config.apps = django_apps
    config.import_models()
    django_apps.clear_cache()
    yield config
    django_apps.all_models.pop(PLUGIN_PACKAGE, None)
    django_apps.clear_cache()
    # mock 期间若发起 HTTP 请求（404 页渲染），app 模板目录扫描会被进程级缓存污染
    from django.template.utils import get_app_template_dirs

    get_app_template_dirs.cache_clear()


def _manifest_entry_points() -> list:
    """插件 pyproject 声明的 entry point（真实声明 → 装配输入，避免测试与清单漂移）。"""

    manifest = tomllib.loads((PLUGIN_DIR / "pyproject.toml").read_text(encoding="utf-8"))
    declared = manifest["project"]["entry-points"]["xadmin.contracts"]
    return [
        importlib.metadata.EntryPoint(name=name, value=value, group="xadmin.contracts")
        for name, value in declared.items()
    ]


def _run_ws_middleware(path: str):
    """执行 WS 准入中间件：返回（下发帧, 内层 app 收到的路径）。"""

    from asgiref.sync import async_to_sync

    sent, inner_paths = [], []

    async def inner(scope, receive, send):
        inner_paths.append(scope["path"])

    async def send(message):
        sent.append(message)

    async def receive():  # pragma: no cover - 准入不读取入站帧
        return {"type": "websocket.connect"}

    async_to_sync(ModuleTrimWebsocketMiddleware(inner))({"type": "websocket", "path": path}, receive, send)
    return sent, inner_paths


class TestEntryPointChannel:
    """通道一：entry point 声明 → common.ready() 装配 → 契约面解析到插件实现。"""

    def test_manifest_declares_injectable_contract(self):
        import common.contracts as contracts

        declared = _manifest_entry_points()
        assert [ep.name for ep in declared] == ["get_active_superuser_queryset"]
        # 白名单外的名字会在装配期被拒：清单必须与契约面同步（二开硬约束）
        assert all(ep.name in contracts.__all__ for ep in declared)

    def test_entry_point_assembly_injects_plugin_provider(self, monkeypatch):
        import common.contracts as contracts

        entries = _manifest_entry_points()
        monkeypatch.setattr(importlib.metadata, "entry_points", lambda **kwargs: entries)
        try:
            assert contracts.load_contract_entry_points() == ["get_active_superuser_queryset"]
            # 契约面解析到插件提供方（`__module__` 即注入生效证据）
            assert contracts.get_active_superuser_queryset.__module__ == "xadmin_demo_plugin.providers"
        finally:
            contracts.unregister_contract("get_active_superuser_queryset")
        assert contracts.get_active_superuser_queryset.__module__ == "identity.services"

    def test_provider_behaviour_follows_plugin_setting(self, settings, superuser):
        from xadmin_demo_plugin.providers import get_active_superuser_queryset

        import common.contracts as contracts

        contracts.register_contract("get_active_superuser_queryset", get_active_superuser_queryset)
        try:
            # 未配置白名单：与内核默认实现一致（不过滤）
            settings.DEMO_PLUGIN_ALERT_ALLOWLIST = []
            assert set(contracts.get_active_superuser_queryset().values_list("pk", flat=True)) == set(
                superuser.__class__.objects.filter(is_superuser=True, is_active=True).values_list("pk", flat=True)
            )
            # 配置白名单：只在用超管中被收敛（覆盖语义是「换实现」，不是「换接口」）
            settings.DEMO_PLUGIN_ALERT_ALLOWLIST = [superuser.username]
            filtered = contracts.get_active_superuser_queryset().values_list("username", flat=True)
            assert set(filtered) == {superuser.username}
        finally:
            contracts.unregister_contract("get_active_superuser_queryset")


class TestAppsReadyChannel:
    """通道二：``AppConfig.ready()`` 注入（需要精确时序或注入前读配置时用这条）。"""

    @staticmethod
    def _app_config():
        from xadmin_demo_plugin.apps import XadminDemoPluginConfig

        return XadminDemoPluginConfig(PLUGIN_PACKAGE, importlib.import_module(PLUGIN_PACKAGE))

    def test_ready_injects_plugin_model_into_guard(self, plugin_installed_as_app):
        import common.contracts as contracts

        assert contracts.guarded_models() == set()  # 默认：未登记任何模型
        app = self._app_config()
        app.ready()
        try:
            assert "xadmin_demo_plugin.pluginnote" in contracts.guarded_models()
        finally:
            contracts.unregister_contract("guarded_models")
        assert "xadmin_demo_plugin.pluginnote" not in contracts.guarded_models()

    def test_duplicate_registration_fails_fast(self):
        """同一契约双路径注入（entry point + ready）会被拒——二开包互踩防护。"""

        app = self._app_config()
        app.ready()
        try:
            with pytest.raises(ValueError, match="already has an injected provider"):
                app.ready()
        finally:
            import common.contracts as contracts

            contracts.unregister_contract("guarded_models")


class TestModuleDeclaration:
    """通道三：``modules.py`` 声明进入清单，与内置模块同口径参与预设与六层裁剪。"""

    def test_declaration_joins_registry_with_preset_semantics(self, plugin_installed_as_app, module_config):
        module_config()
        assert MODULE_ID in {spec.id for spec in all_module_specs()}
        assert is_module_enabled(MODULE_ID) is True  # optional 随 full 预设默认开启

        module_config(preset="standard")
        assert is_module_enabled(MODULE_ID) is False  # optional 不随 standard 开启

        module_config(preset="core")
        assert is_module_enabled(MODULE_ID) is False

        module_config(preset="standard", enable=[MODULE_ID])
        assert is_module_enabled(MODULE_ID) is True  # MODULE_ENABLE 显式覆盖

        module_config(disable=[MODULE_ID])
        assert is_module_enabled(MODULE_ID) is False  # MODULE_DISABLE 与内置同口径

    def test_declared_routes_match_real_urlpatterns(self, plugin_installed_as_app, module_config):
        """声明的 routes 与 config.py::URLPATTERNS 推导结果一致（单一事实源，不漂移）。"""

        from common.core.modules.routes import derive_route_prefixes

        module_config()
        spec = module_index()[MODULE_ID]
        assert spec.routes == derive_route_prefixes(PLUGIN_PACKAGE) == ("^/api/plugin-demo/",)

    def test_rest_ws_and_permission_layers_gate_when_disabled(self, plugin_installed_as_app, module_config):
        module_config(disable=[MODULE_ID])
        # ① 路由层：网关 404（业务码 + 模块标识）
        response = Client().get(SAMPLE_REST)
        assert response.status_code == 404
        assert response.json()["code"] == 1001
        assert response.json()["module"] == MODULE_ID
        # ② WS 层：准入拒绝（4404），进不到内层 app
        sent, inner = _run_ws_middleware(SAMPLE_WS)
        assert sent == [{"type": "websocket.close", "code": 4404}]
        assert inner == []
        # ③ 菜单/权限点层：声明菜单子树进隐藏集合，权限点前缀落停用集合
        rows = [
            (1, None, MENU_TYPE_DIRECTORY, "DemoPlugin", "/plugin-demo"),
            (2, 1, MENU_TYPE_PERMISSION, "PluginNote", "/api/plugin-demo/note"),
        ]
        spec = module_index()[MODULE_ID]
        hidden = compute_hidden_menu_pks(rows, names=spec.menus, prefixes=permission_prefixes_of([spec]))
        assert hidden == {1, 2}
        assert any(prefix.startswith("api/plugin-demo") for prefix in disabled_permission_prefixes())
        assert match_disabled_module(SAMPLE_REST) == MODULE_ID
        assert is_ws_path_trimmed(SAMPLE_WS) is True

    def test_enabled_module_leaves_layers_untouched(self, plugin_installed_as_app, module_config):
        module_config()  # full 预设默认开启
        assert match_disabled_module(SAMPLE_REST) == ""
        assert is_ws_path_trimmed(SAMPLE_WS) is False
        assert not disabled_permission_prefixes()

    def test_release_preset_report_and_snippet_include_plugin(self, plugin_installed_as_app):
        """发行预设产物（模块清单报表 / config.yml 片段）覆盖插件声明。"""

        from common.core.modules import config_snippet, modules_report

        resolution = preview_modules(preset="standard", enable=[MODULE_ID])
        report = {item["id"]: item for item in modules_report(resolution)}
        assert report[MODULE_ID]["enabled"] is True
        assert report[MODULE_ID]["level"] == "optional"
        snippet = config_snippet(resolution)
        assert "MODULE_PRESET: standard" in snippet
        assert MODULE_ID in snippet

    def test_periodic_task_declares_module_ownership(self, plugin_installed_as_app, module_config):
        """周期任务声明归属模块：停用时由注册链路跳过（此处校验声明面与注册表）。"""

        registry = get_register_period_tasks()
        try:
            importlib.import_module(f"{PLUGIN_PACKAGE}.tasks")
            declared = {
                name: detail
                for entry in registry
                for name, detail in entry.items()
                if detail.get("module") == MODULE_ID
            }
            assert declared, "插件 tasks.py 须声明 module='demo_plugin'（否则停用后仍会注册）"
            assert all(detail.get("module") == MODULE_ID for detail in declared.values())

            module_config(disable=[MODULE_ID])
            assert is_module_enabled(MODULE_ID) is False
        finally:
            # 注册表是模块级全局：整体摘除本次导入的插件条目（就地表原地更新 list 身份），
            # 收起后留下的空字典会让 `next(iter(entry))` 形态的下游断言 StopIteration
            kept = []
            for entry in registry:
                for name in [key for key, detail in entry.items() if detail.get("module") == MODULE_ID]:
                    entry.pop(name, None)
                if entry:
                    kept.append(entry)
            registry[:] = kept
            reset_module_state()
