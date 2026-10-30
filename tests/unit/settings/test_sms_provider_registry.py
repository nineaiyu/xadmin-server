# -*- coding: utf-8 -*-
"""短信供应商注册表守护：枚举值必须全部登记，且客户端实现协议方法。"""

import importlib

from integrations.sdk.sms.endpoint import BACKENDS, PROVIDER_SPECS, SMS_PROVIDERS


def test_all_sms_backends_registered():
    missing = [str(code) for code in BACKENDS if code not in PROVIDER_SPECS]
    assert not missing, f"短信供应商缺少注册项：{missing}"


def test_provider_client_implements_protocol():
    for spec in SMS_PROVIDERS:
        mod = importlib.import_module(f"integrations.sdk.sms.{spec.module}")
        client_cls = getattr(mod, "client", None)
        assert client_cls is not None, f"{spec.module} 未暴露 client"
        for name in ("new_from_settings", "send_sms", "need_pre_check"):
            assert callable(getattr(client_cls, name, None)), f"{spec.module}.client 缺少 {name}()"
