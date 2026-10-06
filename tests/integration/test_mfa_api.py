# -*- coding: utf-8 -*-
"""MFA / 敏感操作二次验证接口集成测试。"""

import pyotp
import pytest
from django.core import mail
from django.core.cache import cache

from common.base.utils import AESCipherV2
from tests.integration.aes_v2 import encrypt_v2

pytestmark = pytest.mark.django_db

BASIC_LOGIN_URL = "/api/system/login/basic"
LOGIN_MFA_VERIFY_URL = "/api/system/login/mfa/verify"
CONFIRM_URL = "/api/mfa/confirm"
SEND_CODE_URL = "/api/mfa/confirm/send-code"
OTP_URL = "/api/mfa/otp"
OTP_START_URL = "/api/mfa/otp/start"
OTP_CONFIRM_URL = "/api/mfa/otp/confirm"
OTP_CLOSE_URL = "/api/mfa/otp/close"
OTP_OPEN_URL = "/api/mfa/otp/open"
OTP_TEST_URL = "/api/mfa/otp/test"
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
def email_ready(authed_client, normal_user, settings):
    """绑定 OTP 的标准环境：挑战渠道基础设施开启 + 用户已配邮箱。"""
    settings.EMAIL_ENABLED = True
    normal_user.email = "zhangsan@example.com"
    normal_user.save(update_fields=["email"])
    return authed_client


@pytest.fixture
def otp_user(email_ready, normal_user):
    """已绑定 OTP 的登录用户，返回 (user, client, secret)。"""
    resp = email_ready.post(OTP_START_URL)
    assert resp.data["code"] == 1000, resp.data
    secret = resp.data["data"]["secret"]
    resp = email_ready.post(OTP_CONFIRM_URL, {"code": pyotp.TOTP(secret).now()})
    assert resp.data["code"] == 1000, resp.data
    return normal_user, email_ready, secret


class TestOTPBind:
    def test_bind_flow(self, otp_user, authed_client):
        user, client, secret = otp_user
        resp = client.get(OTP_URL)
        assert resp.data["data"]["enabled"] is True

    def test_start_returns_otpauth_uri(self, email_ready):
        resp = email_ready.post(OTP_START_URL)
        assert resp.data["code"] == 1000
        assert resp.data["data"]["uri"].startswith("otpauth://totp/")
        assert resp.data["data"]["secret"]

    def test_confirm_wrong_code(self, email_ready):
        email_ready.post(OTP_START_URL)
        resp = email_ready.post(OTP_CONFIRM_URL, {"code": "000000"})
        assert resp.data["code"] == 1002

    def test_confirm_without_start(self, authed_client):
        resp = authed_client.post(OTP_CONFIRM_URL, {"code": "123456"})
        assert resp.data["code"] == 1001

    def test_bind_twice_rejected(self, otp_user, authed_client):
        _, client, _ = otp_user
        resp = client.post(OTP_START_URL)
        assert resp.data["code"] == 1001


class TestBindingPolicyGate:
    """绑定入口与验证同口径：方式白名单收窄到空集时拒绝绑定（共享/演示账号防锁死）。"""

    def test_start_rejected_when_policy_disallows(self, authed_client, normal_user):
        normal_user.allowed_mfa_types = ["none"]
        normal_user.save(update_fields=["allowed_mfa_types"])
        resp = authed_client.post(OTP_START_URL)
        assert resp.data["code"] == 1001
        assert "policy" in resp.data["detail"] or "不允许" in resp.data["detail"]

    def test_confirm_rejected_when_policy_disallows(self, authed_client, normal_user):
        # start 在收窄前发出（候选密钥已进缓存），confirm 仍须拒绝落库
        resp = authed_client.post(OTP_START_URL)
        assert resp.data["code"] == 1000
        normal_user.allowed_mfa_types = ["none"]
        normal_user.save(update_fields=["allowed_mfa_types"])
        resp = authed_client.post(OTP_CONFIRM_URL, {"code": "000000"})
        assert resp.data["code"] == 1001
        normal_user.refresh_from_db()
        assert not normal_user.otp_secret_key

    def test_unrestricted_account_still_binds(self, email_ready):
        resp = email_ready.post(OTP_START_URL)
        assert resp.data["code"] == 1000

    def test_start_rejected_without_backup_channel(self, authed_client, normal_user, settings):
        """有挑战渠道基础设施而用户未配置任一渠道时，拒绝绑定 OTP。"""
        settings.EMAIL_ENABLED = True
        resp = authed_client.post(OTP_START_URL)
        assert resp.data["code"] == 1001
        assert "challenge channel" in resp.data["detail"] or "备用挑战渠道" in resp.data["detail"]
        normal_user.refresh_from_db()
        assert not normal_user.otp_secret_key

    def test_start_allowed_when_no_channel_infra(self, authed_client, settings):
        """部署侧无任何挑战渠道基础设施（EMAIL/SMS 均关）时不拦截：恢复码是唯一自救通道。"""
        settings.EMAIL_ENABLED = False
        settings.SMS_ENABLED = False
        resp = authed_client.post(OTP_START_URL)
        assert resp.data["code"] == 1000, resp.data


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
        resp = authed_client.post(CONFIRM_URL, {"confirm_type": "password", "method": "password", "code": "Wrong@123"})
        assert resp.data["code"] == 1002

    def test_password_method_cannot_satisfy_mfa_level(self, authed_client):
        """密码确认级别低于 MFA，不能用于 MFA 级别的敏感操作。"""
        resp = authed_client.post(CONFIRM_URL, {"confirm_type": "mfa", "method": "password", "code": "Test@123456"})
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

    def test_close_keeps_secret_and_open_without_rescan(self, otp_user):
        """关闭仅停用开关（密钥保留），重新开启校验动态码即可，无需重新扫码。"""
        user, client, secret = otp_user
        resp = client.post(CONFIRM_URL, {"confirm_type": "password", "method": "password", "code": "Test@123456"})
        assert resp.data["code"] == 1000, resp.data
        resp = client.post(OTP_CLOSE_URL)
        assert resp.data["code"] == 1000, resp.data
        user.refresh_from_db()
        assert user.mfa_enabled is False
        assert user.otp_secret_key == secret

        # 状态接口：bound 仍为 True，enabled 为 False
        resp = client.get(OTP_URL)
        assert resp.data["data"]["bound"] is True
        assert resp.data["data"]["enabled"] is False

        # 重新开启：动态码校验通过即恢复，无需重新绑定
        resp = client.post(OTP_OPEN_URL, {"code": pyotp.TOTP(secret).now()})
        assert resp.data["code"] == 1000, resp.data
        user.refresh_from_db()
        assert user.mfa_enabled is True
        assert user.otp_secret_key == secret

    def test_open_with_wrong_code_rejected(self, otp_user):
        _, client, _ = otp_user
        client.post(CONFIRM_URL, {"confirm_type": "password", "method": "password", "code": "Test@123456"})
        client.post(OTP_CLOSE_URL)
        resp = client.post(OTP_OPEN_URL, {"code": "000000"})
        assert resp.data["code"] == 1002

    def test_close_without_bound_rejected(self, authed_client):
        """未绑定密钥时关闭接口先被敏感操作协议拦截（412，同解绑）。"""
        resp = authed_client.post(OTP_CLOSE_URL)
        assert resp.status_code == 412

    def test_test_code_does_not_change_state(self, otp_user):
        """校验接口：正确码通过、错误码 1002，且无论成败都不改变任何状态。"""
        user, client, secret = otp_user
        level_before = user.mfa_level

        resp = client.post(OTP_TEST_URL, {"code": "000000"})
        assert resp.data["code"] == 1002, resp.data

        resp = client.post(OTP_TEST_URL, {"code": pyotp.TOTP(secret).now()})
        assert resp.data["code"] == 1000, resp.data

        user.refresh_from_db()
        assert user.mfa_level == level_before
        assert user.otp_secret_key == secret

    def test_test_code_increments_block_counter(self, otp_user):
        """校验接口失败同样计入防爆破计数（达到阈值锁定）。"""
        from django.conf import settings as dj_settings

        user, client, _ = otp_user
        limit = int(dj_settings.SECURITY_LOGIN_LIMIT_COUNT)
        for _ in range(limit):
            resp = client.post(OTP_TEST_URL, {"code": "000000"})
            assert resp.data["code"] == 1002
        # 计数已达阈值：即使提交正确码也直接拒绝（锁定中）
        user.refresh_from_db()
        secret = user.otp_secret_key
        import pyotp as _pyotp

        resp = client.post(OTP_TEST_URL, {"code": _pyotp.TOTP(secret).now()})
        assert resp.data["code"] == 1001, resp.data

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
        api_client.post(CONFIRM_URL, {"confirm_type": "password", "method": "password", "code": "Admin@123456"})

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

    def test_reset_password_after_confirm_with_v2_payload(self, api_client, superuser):
        """v2 协议（WebCrypto PBKDF2+AES-GCM）密文走真实改密链路。"""
        api_client.force_authenticate(user=superuser)
        api_client.post(CONFIRM_URL, {"confirm_type": "password", "method": "password", "code": "Admin@123456"})

        def enc(v):
            return encrypt_v2(superuser.username, v)

        resp = api_client.post(
            "/api/system/userinfo/reset-password",
            {"old_password": enc("Admin@123456"), "sure_password": enc("New@123456")},
            format="json",
        )
        assert resp.data["code"] == 1000, resp.data
        assert superuser.check_password("New@123456")

    def test_destroy_user_requires_confirm(self, api_client, superuser, normal_user):
        api_client.force_authenticate(user=superuser)
        resp = api_client.delete(f"/api/system/user/{normal_user.pk}")
        assert resp.status_code == 412

    def test_destroy_user_after_confirm(self, api_client, superuser, normal_user):
        api_client.force_authenticate(user=superuser)
        api_client.post(CONFIRM_URL, {"confirm_type": "password", "method": "password", "code": "Admin@123456"})
        resp = api_client.delete(f"/api/system/user/{normal_user.pk}")
        assert resp.status_code == 200

    def test_admin_reset_mfa(self, api_client, otp_user, superuser):
        """管理员重置用户 OTP（自身需先通过密码二次确认）。"""
        user, _, secret = otp_user
        api_client.force_authenticate(user=superuser)
        resp = api_client.post(f"/api/system/user/{user.pk}/reset-mfa")
        assert resp.status_code == 412

        resp = api_client.post(CONFIRM_URL, {"confirm_type": "password", "method": "password", "code": "Admin@123456"})
        assert resp.data["code"] == 1000, resp.data
        resp = api_client.post(f"/api/system/user/{user.pk}/reset-mfa")
        assert resp.data["code"] == 1000, resp.data
        user.refresh_from_db()
        assert user.mfa_enabled is False
        assert user.otp_secret_key == ""
        assert user.mfa_recovery_codes.count() == 0  # 恢复码随重置一并作废

    def test_login_mfa_skipped_when_no_method_available(self, otp_user, api_client, settings, login_free):
        """已开启 MFA 但可用方式被管理员全部关闭时，降级放行避免登录死锁。"""
        user, _, _ = otp_user
        settings.SECURITY_MFA_CONFIRM_BACKENDS = ["password"]
        api_client.force_authenticate(user=None)
        resp = api_client.post(BASIC_LOGIN_URL, {"username": user.username, "password": "Test@123456"}, format="json")
        assert resp.data["data"]["access"]
        assert "mfa_required" not in resp.data["data"]

    def test_logout_clears_confirm_state(self, authed_client):
        authed_client.post(CONFIRM_URL, {"confirm_type": "password", "method": "password", "code": "Test@123456"})
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
        resp = api_client.post(BASIC_LOGIN_URL, {"username": "zhangsan", "password": "Test@123456"}, format="json")
        assert resp.data["code"] == 1000
        assert resp.data["data"]["access"]
        assert "mfa_required" not in resp.data["data"]

    def test_login_requires_mfa_after_bind(self, otp_user, api_client, login_free):
        """绑定 OTP 后登录返回 mfa_required + mfa_token，不再直接签发 JWT。"""
        user, _, secret = otp_user
        api_client.force_authenticate(user=None)
        resp = api_client.post(BASIC_LOGIN_URL, {"username": user.username, "password": "Test@123456"}, format="json")
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
        resp = api_client.post(BASIC_LOGIN_URL, {"username": user.username, "password": "Test@123456"}, format="json")
        mfa_token = resp.data["data"]["mfa_token"]
        resp = api_client.post(
            LOGIN_MFA_VERIFY_URL, {"mfa_token": mfa_token, "method": "otp", "code": "000000"}, format="json"
        )
        assert resp.status_code == 400

    def test_login_mfa_verify_rejects_password_method(self, otp_user, api_client, login_free):
        user, _, _ = otp_user
        api_client.force_authenticate(user=None)
        resp = api_client.post(BASIC_LOGIN_URL, {"username": user.username, "password": "Test@123456"}, format="json")
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
        resp = api_client.post(BASIC_LOGIN_URL, {"username": user.username, "password": "Test@123456"}, format="json")
        mfa_token = resp.data["data"]["mfa_token"]
        payload = {"mfa_token": mfa_token, "method": "otp", "code": pyotp.TOTP(secret).now()}
        resp = api_client.post(LOGIN_MFA_VERIFY_URL, payload, format="json")
        assert resp.data["code"] == 1000

        resp = api_client.post(LOGIN_MFA_VERIFY_URL, payload, format="json")
        assert resp.status_code == 400

    def test_login_mfa_personal_enabled_ignores_global_switch(self, otp_user, api_client, settings, login_free):
        """个人开启 MFA 的账号登录必须验证，全局「登录 MFA 强制」关闭也不放行。"""
        settings.SECURITY_MFA_LOGIN_PROTECT_ENABLED = False
        user, _, secret = otp_user
        api_client.force_authenticate(user=None)
        resp = api_client.post(BASIC_LOGIN_URL, {"username": user.username, "password": "Test@123456"}, format="json")
        data = resp.data["data"]
        assert data["mfa_required"] is True
        resp = api_client.post(
            LOGIN_MFA_VERIFY_URL,
            {"mfa_token": data["mfa_token"], "method": "otp", "code": pyotp.TOTP(secret).now()},
            format="json",
        )
        assert resp.data["code"] == 1000, resp.data

    def test_login_mfa_global_forces_closed_account(self, otp_user, api_client, login_free):
        """全局强制开启时，已绑定但个人关闭的账号登录也要验证（密钥保留，可验证）。"""
        user, client, secret = otp_user
        resp = client.post(CONFIRM_URL, {"confirm_type": "password", "method": "password", "code": "Test@123456"})
        assert resp.data["code"] == 1000, resp.data
        resp = client.post(OTP_CLOSE_URL)
        assert resp.data["code"] == 1000, resp.data
        user.refresh_from_db()
        assert user.mfa_enabled is False

        api_client.force_authenticate(user=None)
        resp = api_client.post(BASIC_LOGIN_URL, {"username": user.username, "password": "Test@123456"}, format="json")
        data = resp.data["data"]
        assert data["mfa_required"] is True
        resp = api_client.post(
            LOGIN_MFA_VERIFY_URL,
            {"mfa_token": data["mfa_token"], "method": "otp", "code": pyotp.TOTP(secret).now()},
            format="json",
        )
        assert resp.data["code"] == 1000, resp.data

    def test_login_mfa_skipped_when_closed_and_global_off(self, otp_user, api_client, settings, login_free):
        """个人关闭且全局强制关闭：登录不要求验证。"""
        settings.SECURITY_MFA_LOGIN_PROTECT_ENABLED = False
        user, client, _ = otp_user
        resp = client.post(CONFIRM_URL, {"confirm_type": "password", "method": "password", "code": "Test@123456"})
        assert resp.data["code"] == 1000, resp.data
        resp = client.post(OTP_CLOSE_URL)
        assert resp.data["code"] == 1000, resp.data

        api_client.force_authenticate(user=None)
        resp = api_client.post(BASIC_LOGIN_URL, {"username": user.username, "password": "Test@123456"}, format="json")
        assert resp.data["data"]["access"]
        assert "mfa_required" not in resp.data["data"]


RECOVERY_URL = "/api/mfa/otp/recovery-codes"
RECOVERY_REGENERATE_URL = "/api/mfa/otp/recovery-codes/regenerate"


class TestRecoveryCodes:
    """OTP 恢复码：绑定生成 / 登录自救 / 一次性 / 重生成 / 解绑作废。"""

    @pytest.fixture
    def bound(self, email_ready, normal_user):
        """完成绑定，返回 (user, client, secret, recovery_codes)。"""
        resp = email_ready.post(OTP_START_URL)
        assert resp.data["code"] == 1000, resp.data
        secret = resp.data["data"]["secret"]
        resp = email_ready.post(OTP_CONFIRM_URL, {"code": pyotp.TOTP(secret).now()})
        assert resp.data["code"] == 1000, resp.data
        return normal_user, email_ready, secret, resp.data["data"]["recovery_codes"]

    def _mfa_token_and_methods(self, api_client, user):
        api_client.force_authenticate(user=None)
        resp = api_client.post(BASIC_LOGIN_URL, {"username": user.username, "password": "Test@123456"}, format="json")
        data = resp.data["data"]
        assert data["mfa_required"] is True, data
        return data["mfa_token"], data["methods"]

    def test_bind_returns_ten_codes_hashed_at_rest(self, bound):
        from mfa.models import MfaRecoveryCode

        user, client, _, codes = bound
        assert len(codes) == 10 == len(set(codes))
        assert all(len(code) == 11 and code[5] == "-" for code in codes)
        assert MfaRecoveryCode.objects.filter(user=user, code_hash__in=codes).count() == 0
        assert MfaRecoveryCode.objects.filter(user=user).count() == 10
        resp = client.get(RECOVERY_URL)
        assert resp.data["data"]["remaining"] == 10

    def test_login_with_recovery_code_issues_jwt_once(self, bound, api_client, login_free):
        user, client, _, codes = bound
        mfa_token, methods = self._mfa_token_and_methods(api_client, user)
        assert "recovery" in [m["name"] for m in methods]

        resp = api_client.post(
            LOGIN_MFA_VERIFY_URL, {"mfa_token": mfa_token, "method": "recovery", "code": codes[0]}, format="json"
        )
        assert resp.data["code"] == 1000, resp.data
        assert resp.data["data"]["access"]
        assert resp.data["data"]["refresh"]

        # 一次性：同码重放拒绝，剩余 9
        mfa_token, _ = self._mfa_token_and_methods(api_client, user)
        resp = api_client.post(
            LOGIN_MFA_VERIFY_URL, {"mfa_token": mfa_token, "method": "recovery", "code": codes[0]}, format="json"
        )
        assert resp.status_code == 400
        client.force_authenticate(user=user)  # api_client 与 authed_client 同源，恢复认证态
        resp = client.get(RECOVERY_URL)
        assert resp.data["data"]["remaining"] == 9

    def test_recovery_code_entry_form_tolerant(self, bound, api_client, login_free):
        user, _, _, codes = bound
        mfa_token, _ = self._mfa_token_and_methods(api_client, user)
        resp = api_client.post(
            LOGIN_MFA_VERIFY_URL,
            {"mfa_token": mfa_token, "method": "recovery", "code": codes[0].upper().replace("-", "")},
            format="json",
        )
        assert resp.data["code"] == 1000, resp.data

    def test_recovery_brute_force_counts_block(self, bound, api_client, settings, login_free):
        """恢复码爆破走统一 MFABlockUtils 防爆破：达到阈值后正确码也被拒。"""
        user, client, _, codes = bound
        api_client.force_authenticate(user=None)
        resp = api_client.post(BASIC_LOGIN_URL, {"username": user.username, "password": "Test@123456"}, format="json")
        mfa_token = resp.data["data"]["mfa_token"]
        for _ in range(int(settings.SECURITY_LOGIN_LIMIT_COUNT)):
            resp = api_client.post(
                LOGIN_MFA_VERIFY_URL,
                {"mfa_token": mfa_token, "method": "recovery", "code": "zzzzz-zzzzz"},
                format="json",
            )
            assert resp.status_code == 400
        # 锁定中：提交正确恢复码也直接拒绝，且不消费
        resp = api_client.post(
            LOGIN_MFA_VERIFY_URL, {"mfa_token": mfa_token, "method": "recovery", "code": codes[0]}, format="json"
        )
        assert resp.status_code == 400
        client.force_authenticate(user=user)  # api_client 与 authed_client 同源，恢复认证态
        assert client.get(RECOVERY_URL).data["data"]["remaining"] == 10

    def test_confirm_methods_include_recovery(self, bound):
        """恢复码同时服务 412 敏感操作确认（设备全丢的自救闭环）。"""
        _, client, _, _ = bound
        resp = client.get(CONFIRM_URL, {"confirm_type": "mfa"})
        names = [m["name"] for m in resp.data["data"]["methods"]]
        assert "recovery" in names and "otp" in names

    def test_sensitive_confirm_with_recovery_code(self, bound):
        user, client, _, codes = bound
        resp = client.post(CONFIRM_URL, {"confirm_type": "mfa", "method": "recovery", "code": codes[0]})
        assert resp.data["code"] == 1000, resp.data
        resp = client.get(CONFIRM_URL, {"confirm_type": "mfa"})
        assert resp.data["data"]["confirmed"] is True
        assert client.get(RECOVERY_URL).data["data"]["remaining"] == 9

    def test_regenerate_requires_password_confirm(self, bound):
        _, client, _, _ = bound
        resp = client.post(RECOVERY_REGENERATE_URL)
        assert resp.status_code == 412
        assert resp.data["type"] == "user_confirm_required"

    def test_regenerate_rotates_batch(self, bound, api_client, login_free):
        user, client, _, codes = bound
        resp = client.post(CONFIRM_URL, {"confirm_type": "password", "method": "password", "code": "Test@123456"})
        assert resp.data["code"] == 1000, resp.data
        resp = client.post(RECOVERY_REGENERATE_URL)
        assert resp.data["code"] == 1000, resp.data
        new_codes = resp.data["data"]["recovery_codes"]
        assert len(new_codes) == 10
        assert not set(new_codes) & set(codes)
        # 旧码整批作废：登录走旧码拒绝
        api_client.force_authenticate(user=None)
        resp = api_client.post(BASIC_LOGIN_URL, {"username": user.username, "password": "Test@123456"}, format="json")
        mfa_token = resp.data["data"]["mfa_token"]
        resp = api_client.post(
            LOGIN_MFA_VERIFY_URL, {"mfa_token": mfa_token, "method": "recovery", "code": codes[1]}, format="json"
        )
        assert resp.status_code == 400
        client.force_authenticate(user=user)  # api_client 与 authed_client 同源，恢复认证态
        assert client.get(RECOVERY_URL).data["data"]["remaining"] == 10

    def test_regenerate_without_bound_rejected(self, authed_client):
        resp = authed_client.post(
            CONFIRM_URL, {"confirm_type": "password", "method": "password", "code": "Test@123456"}
        )
        assert resp.data["code"] == 1000, resp.data
        resp = authed_client.post(RECOVERY_REGENERATE_URL)
        assert resp.data["code"] == 1001
        assert "OTP is not bound" in resp.data["detail"] or "未绑定" in resp.data["detail"]

    def test_disable_wipes_codes(self, bound):
        _, client, _, _ = bound
        resp = client.post(CONFIRM_URL, {"confirm_type": "password", "method": "password", "code": "Test@123456"})
        assert resp.data["code"] == 1000, resp.data
        resp = client.post(OTP_DISABLE_URL)
        assert resp.data["code"] == 1000, resp.data
        assert client.get(RECOVERY_URL).data["data"]["remaining"] == 0


class TestDisableGuard:
    """解绑（disable）与 close/open/test/regenerate 同口径：未绑定先拒绝，不做任何写操作。"""

    def test_disable_without_bound_rejected(self, authed_client, normal_user):
        """未绑定 OTP 时解绑返回 1001，不再像旧实现那样清字段并报成功。"""
        resp = authed_client.post(
            CONFIRM_URL, {"confirm_type": "password", "method": "password", "code": "Test@123456"}
        )
        assert resp.data["code"] == 1000, resp.data
        resp = authed_client.post(OTP_DISABLE_URL)
        assert resp.data["code"] == 1001, resp.data
        assert "OTP is not bound" in resp.data["detail"] or "未绑定" in resp.data["detail"]

    def test_disable_guard_keeps_state_untouched(self, authed_client, normal_user):
        """守卫先于任何写路径：密钥、开关、恢复码在拒绝后保持原状。"""
        from mfa import recovery

        normal_user.otp_secret_key = ""
        normal_user.save(update_fields=["otp_secret_key"])
        level_before = normal_user.mfa_level

        resp = authed_client.post(
            CONFIRM_URL, {"confirm_type": "password", "method": "password", "code": "Test@123456"}
        )
        assert resp.data["code"] == 1000, resp.data
        resp = authed_client.post(OTP_DISABLE_URL)
        assert resp.data["code"] == 1001
        normal_user.refresh_from_db()
        assert normal_user.otp_secret_key == ""
        assert normal_user.mfa_level == level_before
        assert recovery.remaining_count(normal_user) == 0


class TestOtpAntiReplay:
    """OTP 防重放：同一动态码在有效窗口内只允许消费一次（UsedOtpCodeCache）。"""

    def test_confirm_same_code_replay_rejected(self, otp_user):
        """敏感操作确认链路：正确码首次通过，同码重放拒绝。"""
        user, client, secret = otp_user
        code = pyotp.TOTP(secret).now()
        resp = client.post(CONFIRM_URL, {"confirm_type": "mfa", "method": "otp", "code": code})
        assert resp.data["code"] == 1000, resp.data

        resp = client.post(CONFIRM_URL, {"confirm_type": "mfa", "method": "otp", "code": code})
        assert resp.data["code"] == 1002, resp.data
        assert "used" in resp.data["detail"] or "已被使用" in resp.data["detail"]

    def test_login_verify_same_code_replay_rejected(self, otp_user, api_client, login_free):
        """登录 MFA 链路：换新的 mfa_token 重放同一动态码同样拒绝。"""
        user, _, secret = otp_user
        code = pyotp.TOTP(secret).now()
        api_client.force_authenticate(user=None)

        resp = api_client.post(BASIC_LOGIN_URL, {"username": user.username, "password": "Test@123456"}, format="json")
        token = resp.data["data"]["mfa_token"]
        resp = api_client.post(LOGIN_MFA_VERIFY_URL, {"mfa_token": token, "method": "otp", "code": code}, format="json")
        assert resp.data["code"] == 1000, resp.data

        resp = api_client.post(BASIC_LOGIN_URL, {"username": user.username, "password": "Test@123456"}, format="json")
        token = resp.data["data"]["mfa_token"]
        resp = api_client.post(LOGIN_MFA_VERIFY_URL, {"mfa_token": token, "method": "otp", "code": code}, format="json")
        assert resp.status_code == 400

    def test_replay_failure_counts_toward_lock(self, otp_user, settings):
        """重放拒绝与码错误同口径计入防爆破计数：重放不能作为无限制的试码预言机。"""
        user, client, secret = otp_user
        code = pyotp.TOTP(secret).now()
        resp = client.post(CONFIRM_URL, {"confirm_type": "mfa", "method": "otp", "code": code})
        assert resp.data["code"] == 1000, resp.data

        for _ in range(int(settings.SECURITY_LOGIN_LIMIT_COUNT)):
            resp = client.post(CONFIRM_URL, {"confirm_type": "mfa", "method": "otp", "code": code})
            assert resp.data["code"] == 1002
        # 计数达到阈值：此后新的正确动态码也被锁定拒绝
        resp = client.post(CONFIRM_URL, {"confirm_type": "mfa", "method": "otp", "code": pyotp.TOTP(secret).now()})
        assert resp.data["code"] == 1002
        assert "locked" in resp.data["detail"] or "锁定" in resp.data["detail"]


class TestOtpBruteForce:
    """OTP 防爆破：确认链路（check_user_mfa_code）失败累计，达阈值后正确码也被拒。"""

    def test_confirm_wrong_codes_reach_lock(self, otp_user, settings):
        user, client, secret = otp_user
        limit = int(settings.SECURITY_LOGIN_LIMIT_COUNT)
        for _ in range(limit):
            resp = client.post(CONFIRM_URL, {"confirm_type": "mfa", "method": "otp", "code": "000000"})
            assert resp.data["code"] == 1002

        # 锁定中：提交正确动态码也直接拒绝
        resp = client.post(CONFIRM_URL, {"confirm_type": "mfa", "method": "otp", "code": pyotp.TOTP(secret).now()})
        assert resp.data["code"] == 1002, resp.data
        assert "locked" in resp.data["detail"] or "锁定" in resp.data["detail"]

    def test_success_resets_failed_counter(self, otp_user, settings):
        """成功校验清零失败计数：失败-成功-失败交错不会误锁定。"""
        from settings.services import MFABlockUtils

        user, client, secret = otp_user
        for _ in range(int(settings.SECURITY_LOGIN_LIMIT_COUNT) - 1):
            client.post(CONFIRM_URL, {"confirm_type": "mfa", "method": "otp", "code": "000000"})
        resp = client.post(CONFIRM_URL, {"confirm_type": "mfa", "method": "otp", "code": pyotp.TOTP(secret).now()})
        assert resp.data["code"] == 1000, resp.data
        assert not MFABlockUtils(user.username, "127.0.0.1").is_block()


ALL_METHODS = ["otp", "sms", "email", "password", "passkey", "recovery"]


class TestMethodPolicyChain:
    """六后端策略链：全局白名单 ∩ 角色允许集 ∩ 用户允许集，逐层只能收窄。

    服务层交集语义由 passkey 策略单测覆盖；这里经 HTTP 面验证各层收窄在
    「可用方式列表」「验证入口」两处同口径生效，且六后端逐一被收窄拒绝。
    """

    SIX = set(ALL_METHODS)

    @pytest.fixture
    def all_methods(self, email_ready, normal_user, settings):
        """六后端全部可用的用户：otp+recovery（绑定产生）、sms、email、password、passkey。"""
        from identity.models import UserPasskey

        settings.SMS_ENABLED = True
        normal_user.phone = "13800138000"
        normal_user.save(update_fields=["phone"])
        UserPasskey.objects.create(
            user=normal_user, creator=normal_user, credential_id="policy-cred", public_key=b"\xa1\x01", sign_count=0
        )
        resp = email_ready.post(OTP_START_URL)
        assert resp.data["code"] == 1000, resp.data
        secret = resp.data["data"]["secret"]
        resp = email_ready.post(OTP_CONFIRM_URL, {"code": pyotp.TOTP(secret).now()})
        assert resp.data["code"] == 1000, resp.data
        return normal_user, email_ready, secret

    def _listed(self, client, confirm_type="password"):
        resp = client.get(CONFIRM_URL, {"confirm_type": confirm_type})
        assert resp.data["code"] == 1000, resp.data
        return [m["name"] for m in resp.data["data"]["methods"]]

    def test_all_six_methods_listed(self, all_methods):
        user, client, _ = all_methods
        assert set(self._listed(client)) == self.SIX

    def test_global_whitelist_narrows(self, all_methods, settings):
        user, client, _ = all_methods
        settings.SECURITY_MFA_METHODS = ["otp", "recovery"]
        assert self._listed(client) == ["otp", "recovery"]

    def test_role_layer_narrows(self, all_methods, role):
        user, client, _ = all_methods
        role.allowed_mfa_types = ["otp", "sms", "recovery"]
        role.save(update_fields=["allowed_mfa_types"])
        assert set(self._listed(client)) == {"otp", "sms", "recovery"}

    def test_user_layer_narrows(self, all_methods):
        user, client, _ = all_methods
        user.allowed_mfa_types = ["email"]
        user.save(update_fields=["allowed_mfa_types"])
        assert self._listed(client) == ["email"]

    def test_three_layers_intersect(self, all_methods, role, settings):
        user, client, _ = all_methods
        settings.SECURITY_MFA_METHODS = ["otp", "sms", "email"]
        role.allowed_mfa_types = ["sms", "email", "passkey"]
        role.save(update_fields=["allowed_mfa_types"])
        user.allowed_mfa_types = ["sms", "recovery"]
        user.save(update_fields=["allowed_mfa_types"])
        assert self._listed(client) == ["sms"]

    @pytest.mark.parametrize(
        ("method", "confirm_type"),
        [
            ("otp", "mfa"),
            ("sms", "mfa"),
            ("email", "mfa"),
            ("passkey", "mfa"),
            ("recovery", "mfa"),
            ("password", "password"),
        ],
    )
    def test_excluded_method_rejected_before_code_check(self, all_methods, method, confirm_type):
        """被策略排除的方式：验证入口直接拒绝（方式不可用），不再进入码校验。"""
        user, client, _ = all_methods
        user.allowed_mfa_types = sorted(self.SIX - {method})
        user.save(update_fields=["allowed_mfa_types"])
        resp = client.post(CONFIRM_URL, {"confirm_type": confirm_type, "method": method, "code": "irrelevant"})
        assert resp.data["code"] == 1002, resp.data
        assert "unavailable" in resp.data["detail"] or "不可用" in resp.data["detail"]

    def test_role_mfa_required_forces_login_mfa(self, all_methods, role, api_client, login_free):
        """角色 mfa_required：个人已关闭 MFA 的账号登录仍强制二次验证。"""
        from django.contrib.auth import get_user_model

        user, client, secret = all_methods
        user.mfa_level = get_user_model().MFALevelChoices.DISABLED
        user.save(update_fields=["mfa_level"])
        role.mfa_required = True
        role.save(update_fields=["mfa_required"])

        api_client.force_authenticate(user=None)
        resp = api_client.post(BASIC_LOGIN_URL, {"username": user.username, "password": "Test@123456"}, format="json")
        data = resp.data["data"]
        assert data["mfa_required"] is True, data
        assert "otp" in [m["name"] for m in data["methods"]]
