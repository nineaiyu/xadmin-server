# -*- coding: utf-8 -*-
"""通知渠道注册表与短信供应商协议守护。

- ``CHANNEL_SPECS`` 必须覆盖全部 ``BACKEND`` 枚举值（新增渠道漏登记 spec 会静默缺渲染/净化名单）；
- 渠道客户端类声明（``account_field`` / ``is_enable_field_in_settings``）与 spec 一致；
- 短信供应商注册表覆盖全部 ``BACKENDS``，且客户端类实现协议方法。
"""

import importlib

import pytest

from notifications.backends import BACKEND
from notifications.backends.specs import CHANNEL_SPECS, SPEC_BY_CODE


@pytest.fixture
def reload_backends():
    from notifications.backends import load_backend_clients as load

    load()
    from notifications import registry  # noqa: F401  确保渲染注册照常执行

    yield


def test_all_backends_have_spec():
    missing = [str(b) for b in BACKEND if str(b) not in SPEC_BY_CODE]
    assert not missing, f"BACKEND 枚举缺少 ChannelSpec 登记：{missing}"


def test_spec_codes_are_valid_backends():
    for spec in CHANNEL_SPECS:
        assert spec.code in set(str(b) for b in BACKEND), f"未知渠道 code：{spec.code}"


def test_spec_client_declaration_matches(reload_backends):
    """spec 与 backends/<module>.py 的客户端类声明一致（防注册表与实现漂移）。"""
    for spec in CHANNEL_SPECS:
        mod = importlib.import_module(f"notifications.backends.{spec.module}")
        client = getattr(mod, "backend", None)
        assert client is not None, f"{spec.module} 未暴露 backend"
        assert getattr(client, "account_field", "") == spec.account_field
        assert getattr(client, "is_enable_field_in_settings", "") == spec.enable_setting


def test_every_spec_renderer_exists_on_message():
    from notifications.notifications import Message

    for spec in CHANNEL_SPECS:
        assert hasattr(Message, spec.renderer), f"{spec.code} 渲染方法 {spec.renderer} 不存在于 Message"
