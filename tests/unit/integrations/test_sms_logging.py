# -*- coding: utf-8 -*-
"""短信发送日志：手机号与模板参数（含验证码）不得明文落 INFO 日志。"""

import logging
from types import SimpleNamespace

from integrations.sdk.sms.alibaba import AlibabaSMS


def test_alibaba_sms_log_masks_phone_and_code(caplog):
    client = object.__new__(AlibabaSMS)
    client.client = SimpleNamespace(send_sms=lambda request: SimpleNamespace(body=SimpleNamespace(code="OK")))

    with caplog.at_level(logging.INFO, logger="xadmin.integrations.sdk.sms.alibaba"):
        client.send_sms(["13800138000"], "sign-name", "SMS_1", {"code": "123456"})

    text = caplog.text
    assert "13800138000" not in text
    assert "123456" not in text
    assert "param_keys" in text
