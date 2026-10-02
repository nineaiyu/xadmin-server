# -*- coding: utf-8 -*-
"""common/contracts.py 契约面守护（ADR-079）。

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
