# -*- coding: utf-8 -*-
"""MFA / 敏感操作二次验证接口集成测试。"""
import pyotp
import pytest
from django.core.cache import cache
from django.core import mail

from common.base.utils import AESCipherV2

pytestmark = pytest.mark.django_db

BASIC_LOGIN_URL = "/api/system/login/basic"
LOGIN_MFA_VERIFY_URL = "/api/system/login/mfa/verify"
CONFIRM_URL = "/api/mfa/confirm"
SEND_CODE_URL = "/api/mfa/confirm/send-code"
OTP_URL = "/api/mfa/otp"
OTP_START_URL = "/api/mfa/otp/start"
OTP_CONFIRM_URL = "/api/mfa/otp/confirm"
OTP_DISABLE_URL = "/api/mfa/otp/disable"


@pytest.fixture
def login_free(settings):
    """关闭登录辅助安全项：验证码 / 加密 / 临时 token。"""
    settings.SECURITY_LOGIN_CAPTCHA_ENABLED = False
    settings.SECURITY_LOGIN_ENCRYPTED_ENABLED = False
    settings.SECURITY_LOGIN_TEMP_TOKEN_ENABLED = False


@pytest.fixture
def authed_client(api_client, normal_user):
    api_client.force_authenticate(user=normal_user)
    return api_client


@pytest.fixture
def otp_user(authed_client, normal_user):
    """已绑定 OTP 的登录用户，返回 (user, client, secret)。"""
    resp = authed_client.post(OTP_START_URL)
    assert resp.data["code"] == 1000, resp.data
    secret = resp.data["data"]["secret"]
    resp = authed_client.post(OTP_CONFIRM_URL, {"code": pyotp.TOTP(secret).now()})
    assert resp.data["code"] == 1000, resp.data
    return normal_user, authed_client, secret


class TestOTPBind:
    def test_bind_flow(self, otp_user, authed_client):
        user, client, secret = otp_user
        resp = client.get(OTP_URL)
        assert resp.data["data"]["enabled"] is True

    def test_start_returns_otpauth_uri(self, authed_client):
        resp = authed_client.post(OTP_START_URL)
        assert resp.data["code"] == 1000
        assert resp.data["data"]["uri"].startswith("otpauth://totp/")
        assert resp.data["data"]["secret"]

    def test_confirm_wrong_code(self, authed_client):
        authed_client.post(OTP_START_URL)
        resp = authed_client.post(OTP_CONFIRM_URL, {"code": "000000"})
        assert resp.data["code"] == 1002

    def test_confirm_without_start(self, authed_client):
        resp = authed_client.post(OTP_CONFIRM_URL, {"code": "123456"})
        assert resp.data["code"] == 1001

    def test_bind_twice_rejected(self, otp_user, authed_client):
        _, client, _ = otp_user
        resp = client.post(OTP_START_URL)
        assert resp.data["code"] == 1001


class TestUserConfirm:
    def test_methods_by_confirm_type(self, authed_client):
        """未绑定 OTP、无手机邮箱的用户：mfa 级别无可用方式，password 级别仅密码可用。"""
        resp = authed_client.get(CONFIRM_URL, {"confirm_type": "mfa"})
        assert resp.data["data"]["methods"] == []
        assert resp.data["data"]["confirmed"] is False

        resp = authed_client.get(CONFIRM_URL, {"confirm_type": "password"})
        assert [m["name"] for m in resp.data["data"]["methods"]] == ["password"]

    def test_confirm_with_password(self, authed_client):
        resp = authed_client.post(
            CONFIRM_URL, {"confirm_type": "password", "method": "password", "code": "Test@123456"}
        )
        assert resp.data["code"] == 1000, resp.data
        assert resp.data["data"]["expire_at"]

        resp = authed_client.get(CONFIRM_URL, {"confirm_type": "password"})
        assert resp.data["data"]["confirmed"] is True

    def test_confirm_wrong_password(self, authed_client):
        resp = authed_client.post(
            CONFIRM_URL, {"confirm_type": "password", "method": "password", "code": "Wrong@123"}
        )
        assert resp.data["code"] == 1002

    def test_password_method_cannot_satisfy_mfa_level(self, authed_client):
        """密码确认级别低于 MFA，不能用于 MFA 级别的敏感操作。"""
        resp = authed_client.post(
            CONFIRM_URL, {"confirm_type": "mfa", "method": "password", "code": "Test@123456"}
        )
        assert resp.data["code"] == 1002

    def test_confirm_with_otp(self, otp_user):
        """OTP 方式通过 mfa 级别确认。"""
        user, client, secret = otp_user
        resp = client.post(CONFIRM_URL, {"confirm_type": "mfa", "method": "otp", "code": pyotp.TOTP(secret).now()})
        assert resp.data["code"] == 1000, resp.data
        resp = client.get(CONFIRM_URL, {"confirm_type": "mfa"})
        assert resp.data["data"]["confirmed"] is True

    def test_confirm_state_level_aware(self, authed_client):
        """密码确认后：password 级别视为已确认，mfa 级别仍需重新验证。"""
        authed_client.post(CONFIRM_URL, {"confirm_type": "password", "method": "password", "code": "Test@123456"})
        resp = authed_client.get(CONFIRM_URL, {"confirm_type": "password"})
        assert resp.data["data"]["confirmed"] is True
        resp = authed_client.get(CONFIRM_URL, {"confirm_type": "mfa"})
        assert resp.data["data"]["confirmed"] is False


class TestSensitiveOperation:
    def test_disable_without_confirm_returns_412(self, otp_user):
        """敏感操作（解绑 OTP）未二次验证时统一返回 412 协议。"""
        _, client, _ = otp_user
        resp = client.post(OTP_DISABLE_URL)
        assert resp.status_code == 412
        assert resp.data["type"] == "user_confirm_required"
        assert resp.data["confirm_type"] == "password"

    def test_disable_after_confirm(self, otp_user):
        user, client, _ = otp_user
        resp = client.post(CONFIRM_URL, {"confirm_type": "password", "method": "password", "code": "Test@123456"})
        assert resp.data["code"] == 1000, resp.data
        resp = client.post(OTP_DISABLE_URL)
        assert resp.data["code"] == 1000, resp.data
        user.refresh_from_db()
        assert user.mfa_enabled is False
        assert user.otp_secret_key == ""

    def test_confirm_framework_can_be_disabled(self, otp_user, settings):
        """总开关关闭后敏感操作直接放行。"""
        settings.SECURITY_MFA_CONFIRM_ENABLED = False
        _, client, _ = otp_user
        resp = client.post(OTP_DISABLE_URL)
        assert resp.data["code"] == 1000


class TestBuiltinSensitiveOperations:
    """系统内置敏感操作（改密码 / 删除用户）的二次验证接入。"""

    def test_reset_password_requires_confirm(self, api_client, superuser):
        api_client.force_authenticate(user=superuser)
        resp = api_client.post(
            "/api/system/userinfo/reset-password",
            {"old_password": "Admin@123456", "sure_password": "New@123456"},
            format="json",
        )
        assert resp.status_code == 412
        assert resp.data["type"] == "user_confirm_required"

    def test_reset_password_after_confirm_clears_state(self, api_client, superuser):
        api_client.force_authenticate(user=superuser)
        api_client.post(
            CONFIRM_URL, {"confirm_type": "password", "method": "password", "code": "Admin@123456"}
        )
        def enc(v):
            return AESCipherV2(superuser.username).encrypt(v.encode()).decode()
        resp = api_client.post(
            "/api/system/userinfo/reset-password",
            {"old_password": enc("Admin@123456"), "sure_password": enc("New@123456")},
            format="json",
        )
        assert resp.data["code"] == 1000, resp.data
        # 密码变更后确认状态被清除，需要重新验证
        resp = api_client.get(CONFIRM_URL, {"confirm_type": "password"})
        assert resp.data["data"]["confirmed"] is False
        # 密码变更后确认状态被清除，需要重新验证
        resp = api_client.get(CONFIRM_URL, {"confirm_type": "password"})
        assert resp.data["data"]["confirmed"] is False

    def test_destroy_user_requires_confirm(self, api_client, superuser, normal_user):
        api_client.force_authenticate(user=superuser)
        resp = api_client.delete(f"/api/system/user/{normal_user.pk}")
        assert resp.status_code == 412

    def test_destroy_user_after_confirm(self, api_client, superuser, normal_user):
        api_client.force_authenticate(user=superuser)
        api_client.post(
            CONFIRM_URL, {"confirm_type": "password", "method": "password", "code": "Admin@123456"}
        )
        resp = api_client.delete(f"/api/system/user/{normal_user.pk}")
        assert resp.status_code == 200

    def test_admin_reset_mfa(self, api_client, otp_user, superuser):
        """管理员重置用户 OTP（自身需先通过密码二次确认）。"""
        user, _, secret = otp_user
        api_client.force_authenticate(user=superuser)
        resp = api_client.post(f"/api/system/user/{user.pk}/reset-mfa")
        assert resp.status_code == 412

        resp = api_client.post(
            CONFIRM_URL, {"confirm_type": "password", "method": "password", "code": "Admin@123456"}
        )
        assert resp.data["code"] == 1000, resp.data
        resp = api_client.post(f"/api/system/user/{user.pk}/reset-mfa")
        assert resp.data["code"] == 1000, resp.data
        user.refresh_from_db()
        assert user.mfa_enabled is False
        assert user.otp_secret_key == ""

    def test_login_mfa_skipped_when_no_method_available(self, otp_user, api_client, settings, login_free):
        """已开启 MFA 但可用方式被管理员全部关闭时，降级放行避免登录死锁。"""
        user, _, _ = otp_user
        settings.SECURITY_MFA_CONFIRM_BACKENDS = ["password"]
        api_client.force_authenticate(user=None)
        resp = api_client.post(
            BASIC_LOGIN_URL, {"username": user.username, "password": "Test@123456"}, format="json"
        )
        assert resp.data["data"]["access"]
        assert "mfa_required" not in resp.data["data"]

    def test_logout_clears_confirm_state(self, authed_client):
        authed_client.post(
            CONFIRM_URL, {"confirm_type": "password", "method": "password", "code": "Test@123456"}
        )
        resp = authed_client.get(CONFIRM_URL, {"confirm_type": "password"})
        assert resp.data["data"]["confirmed"] is True

        authed_client.post("/api/system/logout", {}, format="json")
        resp = authed_client.get(CONFIRM_URL, {"confirm_type": "password"})
        assert resp.data["data"]["confirmed"] is False


class TestChallengeCode:
    def test_email_challenge_flow(self, authed_client, normal_user, settings):
        """邮件挑战码全链路：发送 → 缓存取码 → 提交确认。"""
        settings.EMAIL_ENABLED = True
        normal_user.email = "zhangsan@example.com"
        normal_user.save(update_fields=["email"])

        resp = authed_client.post(SEND_CODE_URL, {"method": "email"})
        assert resp.data["code"] == 1000, resp.data
        assert len(mail.outbox) == 1

        code = cache.get("auth_verify_code_zhangsan@example.com")
        assert code
        resp = authed_client.post(CONFIRM_URL, {"confirm_type": "mfa", "method": "email", "code": code})
        assert resp.data["code"] == 1000, resp.data

    def test_send_code_sms_disabled(self, authed_client, normal_user, settings):
        """短信通道未开启时，sms 方式不可用。"""
        settings.SMS_ENABLED = False
        normal_user.phone = "13800138000"
        normal_user.save(update_fields=["phone"])
        resp = authed_client.post(SEND_CODE_URL, {"method": "sms"})
        assert resp.data["code"] == 1002

    def test_send_code_rejects_password_method(self, authed_client):
        resp = authed_client.post(SEND_CODE_URL, {"method": "password"})
        assert resp.status_code == 400  # serializer choices 校验直接拒绝


class TestLoginMFA:
    def test_login_without_mfa_unaffected(self, api_client, normal_user, login_free):
        resp = api_client.post(
            BASIC_LOGIN_URL, {"username": "zhangsan", "password": "Test@123456"}, format="json"
        )
        assert resp.data["code"] == 1000
        assert resp.data["data"]["access"]
        assert "mfa_required" not in resp.data["data"]

    def test_login_requires_mfa_after_bind(self, otp_user, api_client, login_free):
        """绑定 OTP 后登录返回 mfa_required + mfa_token，不再直接签发 JWT。"""
        user, _, secret = otp_user
        api_client.force_authenticate(user=None)
        resp = api_client.post(
            BASIC_LOGIN_URL, {"username": user.username, "password": "Test@123456"}, format="json"
        )
        assert resp.data["code"] == 1000
        data = resp.data["data"]
        assert data["mfa_required"] is True
        assert data["mfa_token"]
        assert "otp" in [m["name"] for m in data["methods"]]
        assert "access" not in data

        resp = api_client.post(
            LOGIN_MFA_VERIFY_URL,
            {"mfa_token": data["mfa_token"], "method": "otp", "code": pyotp.TOTP(secret).now()},
            format="json",
        )
        assert resp.data["code"] == 1000, resp.data
        assert resp.data["data"]["access"]
        assert resp.data["data"]["refresh"]

    def test_login_mfa_verify_wrong_code(self, otp_user, api_client, login_free):
        user, _, _ = otp_user
        api_client.force_authenticate(user=None)
        resp = api_client.post(
            BASIC_LOGIN_URL, {"username": user.username, "password": "Test@123456"}, format="json"
        )
        mfa_token = resp.data["data"]["mfa_token"]
        resp = api_client.post(
            LOGIN_MFA_VERIFY_URL, {"mfa_token": mfa_token, "method": "otp", "code": "000000"}, format="json"
        )
        assert resp.status_code == 400

    def test_login_mfa_verify_rejects_password_method(self, otp_user, api_client, login_free):
        user, _, _ = otp_user
        api_client.force_authenticate(user=None)
        resp = api_client.post(
            BASIC_LOGIN_URL, {"username": user.username, "password": "Test@123456"}, format="json"
        )
        mfa_token = resp.data["data"]["mfa_token"]
        resp = api_client.post(
            LOGIN_MFA_VERIFY_URL,
            {"mfa_token": mfa_token, "method": "password", "code": "Test@123456"},
            format="json",
        )
        assert resp.status_code == 400

    def test_login_mfa_token_one_time(self, otp_user, api_client, login_free):
        """mfa_token 一次性使用，验证成功后即销毁。"""
        user, _, secret = otp_user
        api_client.force_authenticate(user=None)
        resp = api_client.post(
            BASIC_LOGIN_URL, {"username": user.username, "password": "Test@123456"}, format="json"
        )
        mfa_token = resp.data["data"]["mfa_token"]
        payload = {"mfa_token": mfa_token, "method": "otp", "code": pyotp.TOTP(secret).now()}
        resp = api_client.post(LOGIN_MFA_VERIFY_URL, payload, format="json")
        assert resp.data["code"] == 1000

        resp = api_client.post(LOGIN_MFA_VERIFY_URL, payload, format="json")
        assert resp.status_code == 400

    def test_login_mfa_disabled_by_setting(self, otp_user, api_client, settings, login_free):
        settings.SECURITY_MFA_LOGIN_PROTECT_ENABLED = False
        user, _, _ = otp_user
        api_client.force_authenticate(user=None)
        resp = api_client.post(
            BASIC_LOGIN_URL, {"username": user.username, "password": "Test@123456"}, format="json"
        )
        assert resp.data["data"]["access"]
        assert "mfa_required" not in resp.data["data"]
