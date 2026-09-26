# -*- coding: utf-8 -*-
"""MFA 验证后端单元测试：sms / email / otp / passkey / 抽象基类 / 注册表策略。

P2.8 盲区收口：mfa app 此前未纳入覆盖率测量（.coveragerc source 缺失），
本文件补齐各后端 challenge / check_code / 策略分支的缺口。
"""

import json
from types import SimpleNamespace

import pyotp
import pytest
from django.utils.translation import gettext_lazy as _
from rest_framework.test import APIRequestFactory

from common.sdk.sms.exceptions import CodeError, CodeExpired, CodeSendOverRate
from mfa.backends import get_backend, get_user_mfa_policy
from mfa.backends.base import BaseMFA
from mfa.backends.email import EmailBackend
from mfa.backends.otp import OtpBackend
from mfa.backends.passkey import PasskeyBackend
from mfa.backends.sms import SmsBackend
from system.models import UserPasskey

pytestmark = pytest.mark.django_db


def make_util_cls(send_error=None, verify_error=None):
    """构造 SendAndVerifyCodeUtil 替身：记录构造入参，按预设抛错。"""

    class _Util:
        calls = []

        def __init__(self, target, code=None, backend=None, subject=None, message=None):
            self.calls.append({"target": target, "backend": backend, "code": code})

        def gen_and_send_async(self):
            if send_error is not None:
                raise send_error

        def verify(self, code):
            if verify_error is not None:
                raise verify_error

    return _Util


class TestSmsBackend:
    def test_global_enabled_requires_backend_and_sms_enabled(self, settings):
        settings.SECURITY_MFA_CONFIRM_BACKENDS = ["sms"]
        settings.SMS_ENABLED = True
        assert SmsBackend.global_enabled() is True
        settings.SMS_ENABLED = False
        assert SmsBackend.global_enabled() is False

    def test_is_active_requires_phone(self):
        assert SmsBackend(SimpleNamespace(phone="+8613800138000")).is_active() is True
        assert SmsBackend(SimpleNamespace(phone="")).is_active() is False

    def test_send_challenge_success(self, monkeypatch):
        util = make_util_cls()
        monkeypatch.setattr("mfa.backends.sms.SendAndVerifyCodeUtil", util)
        ok, err = SmsBackend(SimpleNamespace(phone="+8613800138000")).send_challenge()
        assert (ok, err) == (True, "")
        assert util.calls[0]["target"] == "+8613800138000"
        assert util.calls[0]["backend"] == "sms"

    def test_send_challenge_over_rate(self, monkeypatch):
        util = make_util_cls(send_error=CodeSendOverRate(60))
        monkeypatch.setattr("mfa.backends.sms.SendAndVerifyCodeUtil", util)
        ok, err = SmsBackend(SimpleNamespace(phone="+8613800138000")).send_challenge()
        assert ok is False
        assert "60" in err

    def test_check_code_paths(self, monkeypatch):
        user = SimpleNamespace(phone="+8613800138000")
        util = make_util_cls()
        monkeypatch.setattr("mfa.backends.sms.SendAndVerifyCodeUtil", util)
        assert SmsBackend(user).check_code("123456") == (True, "")

        util = make_util_cls(verify_error=CodeExpired())
        monkeypatch.setattr("mfa.backends.sms.SendAndVerifyCodeUtil", util)
        ok, err = SmsBackend(user).check_code("123456")
        assert (ok, err) == (False, str(CodeExpired.default_detail))

        util = make_util_cls(verify_error=CodeError())
        monkeypatch.setattr("mfa.backends.sms.SendAndVerifyCodeUtil", util)
        ok, err = SmsBackend(user).check_code("123456")
        assert (ok, err) == (False, str(CodeError.default_detail))


class TestEmailBackend:
    def test_global_enabled_requires_backend_and_email_enabled(self, settings):
        settings.SECURITY_MFA_CONFIRM_BACKENDS = ["email"]
        settings.EMAIL_ENABLED = True
        assert EmailBackend.global_enabled() is True
        settings.EMAIL_ENABLED = False
        assert EmailBackend.global_enabled() is False

    def test_is_active_requires_email(self):
        assert EmailBackend(SimpleNamespace(email="u@test.local")).is_active() is True
        assert EmailBackend(SimpleNamespace(email="")).is_active() is False

    def test_send_challenge_success_and_over_rate(self, monkeypatch):
        user = SimpleNamespace(email="u@test.local", username="u")
        util = make_util_cls()
        monkeypatch.setattr("mfa.backends.email.SendAndVerifyCodeUtil", util)
        assert EmailBackend(user).send_challenge() == (True, "")
        assert util.calls[0]["target"] == "u@test.local"

        util = make_util_cls(send_error=CodeSendOverRate(30))
        monkeypatch.setattr("mfa.backends.email.SendAndVerifyCodeUtil", util)
        ok, err = EmailBackend(user).send_challenge()
        assert ok is False
        assert "30" in err

    def test_check_code_paths(self, monkeypatch):
        user = SimpleNamespace(email="u@test.local")
        util = make_util_cls()
        monkeypatch.setattr("mfa.backends.email.SendAndVerifyCodeUtil", util)
        assert EmailBackend(user).check_code("123456") == (True, "")

        util = make_util_cls(verify_error=CodeExpired())
        monkeypatch.setattr("mfa.backends.email.SendAndVerifyCodeUtil", util)
        assert EmailBackend(user).check_code("123456") == (False, str(CodeExpired.default_detail))

        util = make_util_cls(verify_error=CodeError())
        monkeypatch.setattr("mfa.backends.email.SendAndVerifyCodeUtil", util)
        assert EmailBackend(user).check_code("123456") == (False, str(CodeError.default_detail))


class TestOtpBackend:
    def test_unbound_user_rejected(self, normal_user):
        backend = OtpBackend(normal_user)
        assert backend.is_active() is False
        ok, err = backend.check_code("000000")
        assert ok is False
        assert err == str(_("OTP is not bound"))

    def test_check_code_success_then_replay_rejected(self, normal_user):
        normal_user.otp_secret_key = pyotp.random_base32()
        normal_user.save(update_fields=["otp_secret_key"])
        backend = OtpBackend(normal_user)
        assert backend.is_active() is True

        code = pyotp.TOTP(normal_user.otp_secret_key).now()
        assert backend.check_code(code) == (True, "")
        # 同一动态码在防重放窗口内只允许使用一次
        ok, err = backend.check_code(code)
        assert ok is False
        assert err == str(_("The verification code has already been used"))

    def test_check_code_wrong_code(self, normal_user):
        normal_user.otp_secret_key = pyotp.random_base32()
        normal_user.save(update_fields=["otp_secret_key"])
        current = pyotp.TOTP(normal_user.otp_secret_key).now()
        wrong = next(c for c in ("000000", "111111", "222222") if c != current)
        ok, err = OtpBackend(normal_user).check_code(wrong)
        assert ok is False
        assert err == str(_("The OTP verification code is incorrect"))


class TestPasskeyBackend:
    @staticmethod
    def _request():
        return APIRequestFactory().get("/")

    def test_is_active_guards(self, normal_user):
        assert PasskeyBackend(normal_user).is_active() is False

        class _NoPk:
            pk = None

        assert PasskeyBackend(_NoPk()).is_active() is False

        class _Broken:
            pk = 1

            @property
            def passkeys(self):
                raise RuntimeError("boom")

        assert PasskeyBackend(_Broken()).is_active() is False

    def test_check_code_invalid_payloads(self, normal_user):
        backend = PasskeyBackend(normal_user, request=self._request())
        assert backend.check_code("{not-json") == (False, str(_("Invalid passkey data")))
        assert backend.check_code({"foo": "bar"}) == (False, str(_("Invalid passkey data")))
        assert backend.check_code(json.dumps({"credential_id": "missing"})) == (
            False,
            str(_("This passkey is not bound to the current account")),
        )

    def test_check_code_verify_value_error(self, normal_user, monkeypatch):
        UserPasskey.objects.create(user=normal_user, creator=normal_user, credential_id="cred-1", public_key=b"\x00")

        def raise_value_error(**kwargs):
            raise ValueError("bad signature")

        monkeypatch.setattr("system.utils.webauthn.verify_assertion", raise_value_error)
        ok, err = PasskeyBackend(normal_user, request=self._request()).check_code(
            json.dumps({"credential_id": "cred-1"})
        )
        assert (ok, err) == (False, "bad signature")

    def test_check_code_unexpected_error_normalized(self, normal_user, monkeypatch):
        UserPasskey.objects.create(user=normal_user, creator=normal_user, credential_id="cred-2", public_key=b"\x00")

        def raise_runtime_error(**kwargs):
            raise RuntimeError("kaboom")

        monkeypatch.setattr("system.utils.webauthn.verify_assertion", raise_runtime_error)
        ok, err = PasskeyBackend(normal_user, request=self._request()).check_code(
            json.dumps({"credential_id": "cred-2"})
        )
        assert ok is False
        assert err == str(_("Passkey verification failed"))


class TestBaseMFADefaults:
    def test_defaults(self):
        class _Minimal(BaseMFA):
            name = "minimal"

            def check_code(self, code):
                return True, ""

        assert _Minimal.global_enabled() is True
        backend = _Minimal(SimpleNamespace())
        assert backend.is_active() is True
        ok, err = backend.send_challenge()
        assert ok is False
        assert err


class TestBackendRegistry:
    def test_policy_for_none_user(self):
        assert get_user_mfa_policy(None) == {"methods": None, "mfa_required": False}

    def test_policy_survives_role_query_failure(self):
        class _BrokenRoles:
            pk = 1

            @property
            def roles(self):
                raise RuntimeError("boom")

        policy = get_user_mfa_policy(_BrokenRoles())
        assert policy["mfa_required"] is False

    def test_get_backend_blocked_by_method_policy(self, normal_user, settings):
        settings.SECURITY_MFA_CONFIRM_BACKENDS = ["otp", "email"]
        settings.SECURITY_MFA_METHODS = ["email"]
        settings.EMAIL_ENABLED = True
        # 全局方式白名单收窄为 email，otp 即使在启用后端名单里也不可用
        assert get_backend(normal_user, "otp") is None
