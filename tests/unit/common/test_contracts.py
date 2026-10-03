# -*- coding: utf-8 -*-
"""common/contracts.py 契约面守护。

contracts 是框架层消费业务 app 的唯一显式出口：白名单即接口声明。这里的
守护保证声明不漂移——白名单里的每个名字都能从提供方解析出**同一对象**
（防笔误 / 提供方改名后静默失效），未声明名字不可达，Protocol 消费面
（SystemConfig / Menu 属性访问式两处）在提供方上真实存在。
"""

import pytest

import common.contracts as contracts


@pytest.mark.django_db
class TestContractWhitelist:
    def test_all_names_resolve_and_round_trip_identity(self):
        """白名单 ↔ 提供方往返恒等：契约名经 contracts 解析与直接从提供方
        模块解析必须是同一对象（同仓提供方改名/删名在此立即红）。"""
        from importlib import import_module

        for name, (provider, _reason) in contracts._CONTRACT_PROVIDERS.items():
            value = getattr(contracts, name)
            assert value is getattr(import_module(provider), name), name
            # 解析结果缓存进模块 globals：二次访问不重复触发 import 链
            assert contracts.__dict__[name] is value

    def test_all_matches_providers(self):
        assert set(contracts.__all__) == set(contracts._CONTRACT_PROVIDERS)

    def test_undeclared_name_unreachable(self):
        with pytest.raises(AttributeError):
            contracts.NotAContract  # noqa: B018 故意触发的属性访问

    def test_no_business_import_at_module_level(self):
        """contracts 模块级零业务 import（import 轻量是契约面前提）。"""
        import re
        from pathlib import Path

        text = Path(contracts.__file__).read_text(encoding="utf-8")
        # 顶格行才算模块级（缩进的是 __getattr__ 内的惰性解析，属白名单机制本身）
        top_level = [ln for ln in text.splitlines() if ln[:1] not in ("", " ", "\t", "#")]
        business = [
            ln for ln in top_level if re.match(r"(?:from|import) (system|approval|ai|settings|notifications)\b", ln)
        ]
        assert business == []


@pytest.mark.django_db
class TestProtocolSurface:
    """属性访问式消费面的 Protocol 消费面在提供方上真实存在（结构漂移守护）。"""

    def test_system_config_surface(self):
        SystemConfig = contracts.SystemConfig
        assert hasattr(SystemConfig, "objects")

    def test_menu_surface(self):
        Menu = contracts.Menu
        assert hasattr(Menu, "objects")
        assert hasattr(Menu.MenuChoices, "PERMISSION")


@pytest.mark.django_db
class TestProviderInjection:
    """注入制生命周期：覆盖即时生效、越界拒绝、fail-fast、回落。"""

    @pytest.fixture(autouse=True)
    def _clean_injections(self):
        """用例注册的覆盖与缓存残留全部撤除，恢复白名单默认初态。"""
        yield
        for name in list(contracts._CONTRACT_OVERRIDES):
            contracts.unregister_contract(name)
        for name in contracts._CONTRACT_PROVIDERS:
            contracts.__dict__.pop(name, None)

    def test_registered_provider_overrides_default(self):
        sentinel = object()
        contracts.register_contract("Setting", sentinel)
        assert contracts.Setting is sentinel
        # from-import 绑定：注册后再 import 的消费方拿到覆盖
        from common.contracts import Setting as imported

        assert imported is sentinel

    def test_unregister_falls_back_to_default(self):
        from importlib import import_module

        sentinel = object()
        contracts.register_contract("Setting", sentinel)
        contracts.unregister_contract("Setting")
        default = import_module("settings.services").Setting
        assert contracts.Setting is default
        assert contracts.__dict__["Setting"] is default  # 回落后恢复默认缓存语义

    def test_outside_whitelist_rejected_and_still_unreachable(self):
        with pytest.raises(ValueError, match="契约面外不可注入"):
            contracts.register_contract("NotAContract", object())
        with pytest.raises(AttributeError):
            contracts.NotAContract  # noqa: B018 缝面不因注入扩大

    def test_duplicate_registration_fails_fast(self):
        sentinel = object()
        contracts.register_contract("Setting", sentinel)
        with pytest.raises(ValueError, match="重复注册"):
            contracts.register_contract("Setting", object())
        assert contracts.Setting is sentinel  # 原覆盖未被静默替换
        contracts.unregister_contract("Setting")
        replacement = object()
        contracts.register_contract("Setting", replacement)
        assert contracts.Setting is replacement  # 显式 unregister 后可替换

    def test_unregister_validates_and_idempotent(self):
        contracts.unregister_contract("Setting")  # 未注册过：幂等
        assert contracts.Setting is not None
        with pytest.raises(ValueError, match="契约面外"):
            contracts.unregister_contract("Evil")

    def test_entry_point_load_registers_providers(self, monkeypatch):
        import importlib.metadata

        sentinel = object()
        seen_groups = []

        class _FakeEP:
            def __init__(self, name, obj=None, load_error=None):
                self.name = name
                self._obj = obj
                self._load_error = load_error

            def load(self):
                if self._load_error is not None:
                    raise self._load_error
                return self._obj

        def _fake_entry_points(**kwargs):
            seen_groups.append(kwargs.get("group"))
            return [_FakeEP("Setting", sentinel), _FakeEP("emit_webhook_event", lambda *a, **k: None)]

        monkeypatch.setattr(importlib.metadata, "entry_points", _fake_entry_points)
        loaded = contracts.load_contract_entry_points()
        assert loaded == ["Setting", "emit_webhook_event"]
        assert seen_groups == [contracts.ENTRY_POINT_GROUP]
        assert contracts.Setting is sentinel
        # 函数契约同样可注入（渠道注册表键 / 委托函数面）
        assert callable(contracts.emit_webhook_event)

    def test_entry_point_failures_fail_fast(self, monkeypatch):
        import importlib.metadata

        class _FakeEP:
            def __init__(self, name, obj=None, load_error=None):
                self.name = name
                self._obj = obj
                self._load_error = load_error

            def load(self):
                if self._load_error is not None:
                    raise self._load_error
                return self._obj

        # 白名单外名字：拒绝注入（缝面不扩大），异常向上传播
        monkeypatch.setattr(importlib.metadata, "entry_points", lambda **kw: [_FakeEP("Evil", object())])
        with pytest.raises(ValueError, match="契约面外不可注入"):
            contracts.load_contract_entry_points()
        # 提供方模块损坏（load 抛错）：启动期 fail-fast，不静默降级
        monkeypatch.setattr(
            importlib.metadata,
            "entry_points",
            lambda **kw: [_FakeEP("Setting", load_error=ImportError("broken distribution"))],
        )
        with pytest.raises(ImportError, match="broken distribution"):
            contracts.load_contract_entry_points()
        # 两个分发声明同名契约：重复注册 fail-fast
        monkeypatch.setattr(
            importlib.metadata,
            "entry_points",
            lambda **kw: [_FakeEP("Setting", object()), _FakeEP("Setting", object())],
        )
        with pytest.raises(ValueError, match="重复注册"):
            contracts.load_contract_entry_points()
