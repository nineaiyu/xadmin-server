# -*- coding: utf-8 -*-
"""模块路由前缀推导单测：``{app}/config.py::URLPATTERNS`` → 裁剪拦截前缀。

守护目标：模块声明不再重复书写路由前缀，前缀只从真实路由推导——改了 config.py
的路径而忘记同步声明，不会再出现「停用模块旧前缀仍可访问」或「新前缀漏拦」的静默漏网。
"""

import sys
import types

import pytest
from django.apps import apps as django_apps
from django.urls import include, path, re_path

from common.core.modules import (
    OPTIONAL,
    ModuleSpec,
    derive_route_prefixes,
    module_index,
    reset_module_state,
)
from common.core.modules.routes import static_prefix

pytestmark = pytest.mark.django_db

PROBE_APP = "probe_app"


@pytest.fixture(autouse=True)
def _clean_module_state():
    yield
    reset_module_state()


def _install_fake_urlconf(monkeypatch, name=PROBE_APP):
    """注册可导入的假 urlconf，让 ``include("<name>.urls")`` 可构造（include 会立即导入）。"""

    package = types.ModuleType(name)
    package.__path__ = []
    urls = types.ModuleType(f"{name}.urls")
    urls.urlpatterns = []
    monkeypatch.setitem(sys.modules, name, package)
    monkeypatch.setitem(sys.modules, f"{name}.urls", urls)


class TestStaticPrefix:
    """URLPATTERNS 条目 → 静态路径前缀。"""

    def test_path_include_route(self):
        assert static_prefix("api/demo/") == "/api/demo/"

    def test_leading_anchor_stripped(self):
        assert static_prefix("^api/demo/") == "/api/demo/"

    def test_regex_dynamic_segment_cut(self):
        assert static_prefix(r"^api/x/(?P<pk>[0-9]+)/$") == "/api/x/"

    def test_path_converter_cut(self):
        assert static_prefix("api/items/<int:pk>/detail/") == "/api/items/"

    def test_trailing_slash_kept_as_boundary(self):
        # 保留尾斜杠：`^/api/demo/` 不会误匹配 `/api/demolition/`
        assert static_prefix("api/demo/") == "/api/demo/"

    def test_no_static_part(self):
        assert static_prefix("") == ""
        assert static_prefix("^") == ""
        assert static_prefix("<int:pk>/") == ""
        assert static_prefix(r"^\d+/") == ""


class TestDeriveRoutePrefixes:
    """推导入口：只接受可导入的 app 配置，故障时退化为空（不拖垮启动）。"""

    @staticmethod
    def _install(monkeypatch, name, patterns):
        config = types.ModuleType(f"{name}.config")
        config.URLPATTERNS = patterns
        monkeypatch.setitem(sys.modules, f"{name}.config", config)
        return name

    def test_derives_from_include_and_re_path(self, monkeypatch):
        _install_fake_urlconf(monkeypatch)
        name = self._install(
            monkeypatch,
            PROBE_APP,
            [
                path("api/probe/", include(f"{PROBE_APP}.urls")),
                re_path(r"^api/probe-admin/(?P<pk>\d+)$", lambda request: None),
                path("api/probe/", include(f"{PROBE_APP}.urls")),  # 重复项去重
            ],
        )
        assert derive_route_prefixes(name) == ("^/api/probe/", "^/api/probe-admin/")

    def test_missing_config_returns_empty(self, monkeypatch):
        monkeypatch.delitem(sys.modules, "ghost_app_absent.config", raising=False)
        assert derive_route_prefixes("ghost_app_absent") == ()

    def test_empty_urlpatterns_returns_empty(self, monkeypatch):
        name = self._install(monkeypatch, "empty_app", [])
        assert derive_route_prefixes(name) == ()

    def test_blank_label_returns_empty(self):
        assert derive_route_prefixes("") == ()


class TestDiscoveryAppliesDerivation:
    """声明与真实路由的单一事实源：未声明 routes 时按 URLPATTERNS 推导。"""

    @staticmethod
    def _install_app(monkeypatch, spec, patterns):
        """注册一个 app 的 config.py（URLPATTERNS）与 modules.py（模块声明）。"""

        config = types.ModuleType(f"{PROBE_APP}.config")
        config.URLPATTERNS = patterns
        monkeypatch.setitem(sys.modules, f"{PROBE_APP}.config", config)
        declaration = types.ModuleType(f"{PROBE_APP}.modules")
        declaration.MODULES = (spec,)
        monkeypatch.setitem(sys.modules, f"{PROBE_APP}.modules", declaration)

        class _AppConfig:
            name = PROBE_APP

        monkeypatch.setattr(django_apps, "get_app_configs", lambda: [_AppConfig()])

    def _urlpatterns(self, monkeypatch):
        _install_fake_urlconf(monkeypatch)
        return [path("api/probe/", include(f"{PROBE_APP}.urls"))]

    def test_declaration_without_routes_gets_derived(self, monkeypatch):
        spec = ModuleSpec("probe_biz", "探针业务", OPTIONAL)
        self._install_app(monkeypatch, spec, self._urlpatterns(monkeypatch))
        reset_module_state()
        assert module_index()["probe_biz"].routes == ("^/api/probe/",)

    def test_explicit_routes_win(self, monkeypatch):
        spec = ModuleSpec("probe_biz", "探针业务", OPTIONAL, routes=(r"^/api/probe/",))
        self._install_app(monkeypatch, spec, self._urlpatterns(monkeypatch))
        reset_module_state()
        assert module_index()["probe_biz"].routes == (r"^/api/probe/",)

    def test_drift_between_declaration_and_urlpatterns_warns(self, monkeypatch, caplog):
        """声明的前缀不在 URLPATTERNS 里（改了 config.py 忘记同步声明）→ 告警。"""

        spec = ModuleSpec("probe_biz", "探针业务", OPTIONAL, routes=(r"^/api/legacy/",))
        self._install_app(monkeypatch, spec, self._urlpatterns(monkeypatch))
        reset_module_state()
        with caplog.at_level("WARNING"):
            module_index()
        assert any("声明与真实路由可能漂移" in record.message for record in caplog.records)

    def test_no_config_keeps_declaration_and_warns(self, monkeypatch, caplog):
        """无 config.py（内置 app 形态）：保持原声明，并提示路由不会被拦截。"""

        name = "probe_builtin"
        declaration = types.ModuleType(f"{name}.modules")
        declaration.MODULES = (ModuleSpec("probe_builtin_biz", "内置探针", OPTIONAL),)
        monkeypatch.setitem(sys.modules, f"{name}.modules", declaration)
        monkeypatch.delitem(sys.modules, f"{name}.config", raising=False)

        class _AppConfig:
            name = "probe_builtin"

        monkeypatch.setattr(django_apps, "get_app_configs", lambda: [_AppConfig()])
        reset_module_state()
        with caplog.at_level("WARNING"):
            spec = module_index()["probe_builtin_biz"]
        assert spec.routes == ()
        assert any("停用后其接口不会被拦截" in record.message for record in caplog.records)
