# -*- coding: utf-8 -*-
"""验证码发送 + 校验集成测试。

验证码发送走 username/basic 通道（dryrun），响应直接回显 verify_code，
避免依赖邮件/短信网关（默认 EMAIL_ENABLED/SMS_ENABLED 均为 False）。
"""
import pytest
from django.core.cache import cache

from common.sdk.sms.exceptions import CodeError, CodeExpired
from common.utils.verify_code import SendAndVerifyCodeUtil

pytestmark = pytest.mark.django_db

SEND_VERIFY_URL = "/api/system/auth/verify"
VERIFY_CODE_KEY_TPL = "auth_verify_code_{}"


@pytest.fixture
def register_free(settings):
    """关闭发送验证码辅助安全项（图片验证码 / 临时 token / 加密），便于直达下发逻辑。"""
    settings.SECURITY_REGISTER_CAPTCHA_ENABLED = False
    settings.SECURITY_REGISTER_TEMP_TOKEN_ENABLED = False
    settings.SECURITY_REGISTER_ENCRYPTED_ENABLED = False
    settings.SECURITY_REGISTER_BY_BASIC_ENABLED = True


class TestSendVerifyCode:
    def test_send_verify_code_success(self, api_client, register_free):
        resp = api_client.post(
            SEND_VERIFY_URL + "?category=register",
            {"form_type": "username", "target": "newuser"},
            format="json",
        )
        assert resp.status_code == 200, resp.data
        assert resp.data["code"] == 1000, resp.data
        verify_token = resp.data["data"]["verify_token"]
        verify_code = resp.data["data"]["verify_code"]
        assert verify_token
        assert verify_code
        # username 通道 dryrun，验证码真实写入缓存且与回显一致
        assert cache.get(VERIFY_CODE_KEY_TPL.format("newuser")) == verify_code

    def test_send_verify_code_invalid_form_type(self, api_client, register_free):
        resp = api_client.post(
            SEND_VERIFY_URL + "?category=register",
            {"form_type": "chat", "target": "newuser"},
            format="json",
        )
        assert resp.status_code == 200, resp.data
        assert resp.data["code"] == 1004

    def test_send_verify_code_access_disabled(self, api_client, register_free, settings):
        settings.SECURITY_REGISTER_ACCESS_ENABLED = False
        resp = api_client.post(
            SEND_VERIFY_URL + "?category=register",
            {"form_type": "username", "target": "newuser"},
            format="json",
        )
        assert resp.status_code == 200, resp.data
        assert resp.data["code"] == 1001
        assert cache.get(VERIFY_CODE_KEY_TPL.format("newuser")) is None


class TestVerifyCodeCheck:
    def _send(self, api_client, target):
        resp = api_client.post(
            SEND_VERIFY_URL + "?category=register",
            {"form_type": "username", "target": target},
            format="json",
        )
        assert resp.status_code == 200 and resp.data["code"] == 1000, resp.data
        return resp.data["data"]["verify_code"]

    def test_verify_correct_code(self, api_client, register_free):
        code = self._send(api_client, "verify_ok")
        assert SendAndVerifyCodeUtil("verify_ok").verify(code) is True

    def test_verify_wrong_code(self, api_client, register_free):
        code = self._send(api_client, "verify_wrong")
        assert code != "999999"
        with pytest.raises(CodeError):
            SendAndVerifyCodeUtil("verify_wrong").verify("999999")

    def test_verify_code_is_one_time(self, api_client, register_free):
        code = self._send(api_client, "verify_once")
        assert SendAndVerifyCodeUtil("verify_once").verify(code) is True
        # 一次性性质：成功校验后验证码即被清除，重复使用失败
        with pytest.raises(CodeExpired):
            SendAndVerifyCodeUtil("verify_once").verify(code)