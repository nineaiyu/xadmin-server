# -*- coding: utf-8 -*-
"""common.injection 注入器契约与 server/const 装配接线（T03-04）。

契约：common 侧只经 get_server_config / get_server_version 读 server 装配产物；
server/const.py 在 settings 导入链最前端登记。未登记即读 = 装配顺序破坏，
必须抛 ImproperlyConfigured 暴露（不许静默回退默认值）。
"""

import pytest
from django.core.exceptions import ImproperlyConfigured

import common.injection as injection
from common.injection import get_server_config, get_server_version


class TestWiring:
    def test_injected_object_is_server_const_config(self):
        """server/const.py 装配后自动登记：注入对象与 const 模块持有的是同一实例。"""
        from server.const import CONFIG, VERSION

        assert get_server_config() is CONFIG
        assert get_server_version() == VERSION


class TestUnregisteredContract:
    def test_config_missing_raises(self, monkeypatch):
        monkeypatch.setattr(injection, "_config", None)
        with pytest.raises(ImproperlyConfigured):
            get_server_config()

    def test_version_missing_raises(self, monkeypatch):
        monkeypatch.setattr(injection, "_version", None)
        with pytest.raises(ImproperlyConfigured):
            get_server_version()


class TestRegister:
    def test_register_overwrites_idempotently(self, monkeypatch):
        monkeypatch.setattr(injection, "_config", None)
        monkeypatch.setattr(injection, "_version", None)
        injection.register_server_injection(config={"fake": 1}, version="0.0.0")
        assert get_server_config() == {"fake": 1}
        assert get_server_version() == "0.0.0"
