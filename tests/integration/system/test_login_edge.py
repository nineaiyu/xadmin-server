# -*- coding: utf-8 -*-
"""登录接口边界分支：禁用开关 / 会话登记降级 / 锁定文案 / 验证码登录全链路。"""

import pytest
from django.core.cache import cache

from common.base.utils import AESCipherV2
from common.utils.verify_code import SendAndVerifyCodeUtil, TokenTempCache
from settings.services import LoginBlockUtil
from system.models.log import UserLoginLog

pytestmark = pytest.mark.django_db

BASIC_LOGIN_URL = "/api/system/login/basic"
LOGIN_CODE_URL = "/api/system/login/code"
TEMP_TOKEN_URL = "/api/system/auth/token"


def _assert_bilingual(text, en_kw, zh_kw):
    """文案断言兼容 zh/en 双语（活动语言不确定，二者命中其一即可）。"""
    assert en_kw in text or zh_kw in text, text


@pytest.fixture
def login_free(settings):
    """关闭登录辅助安全项：验证码 / 加密 / 临时 token。"""
    settings.SECURITY_LOGIN_CAPTCHA_ENABLED = False
    settings.SECURITY_LOGIN_ENCRYPTED_ENABLED = False
    settings.SECURITY_LOGIN_TEMP_TOKEN_ENABLED = False


class TestBasicLoginEdge:
    def test_login_forbidden(self, api_client, normal_user, settings):
        """登录总开关关闭 → 1001 禁止登录。"""
        settings.SECURITY_LOGIN_ACCESS_ENABLED = False
        resp = api_client.post(
            BASIC_LOGIN_URL,
            {"username": "zhangsan", "password": "Test@123456"},
            format="json",
        )
        assert resp.status_code == 200, resp.data
        assert resp.data["code"] == 1001
        _assert_bilingual(str(resp.data["detail"]), "Login forbidden", "禁止登录")

    def test_session_register_failure_does_not_block_login(self, api_client, normal_user, login_free, monkeypatch):
        """会话登记失败仅告警：登录主流程不受影响，正常签发 token。"""

        def boom(request, user, login_type, channel_name=""):
            raise RuntimeError("session down")

        monkeypatch.setattr("system.views.auth.login.register_user_session", boom)
        resp = api_client.post(
            BASIC_LOGIN_URL,
            {"username": "zhangsan", "password": "Test@123456"},
            format="json",
        )
        assert resp.status_code == 200, resp.data
        assert resp.data["code"] == 1000, resp.data
        assert resp.data["data"]["access"]
        assert resp.data["data"]["refresh"]

    def test_session_claim_bind_failure_fallback(self, api_client, normal_user, login_free, monkeypatch):
        """sid claim 绑定失败：退回无 sid 的 token，登录仍成功。"""

        def boom(refresh_token, session_pk):
            raise RuntimeError("claim bind failed")

        monkeypatch.setattr("system.views.auth.login.bind_session_claim", boom)
        resp = api_client.post(
            BASIC_LOGIN_URL,
            {"username": "zhangsan", "password": "Test@123456"},
            format="json",
        )
        assert resp.status_code == 200, resp.data
        assert resp.data["code"] == 1000, resp.data
        assert resp.data["data"]["access"]

    def test_lock_message_when_count_exhausted(self, api_client, normal_user, settings, login_free):
        """失败计数恰好耗尽：提示账号已锁定而非剩余次数。"""
        settings.SECURITY_LOGIN_LIMIT_COUNT = 2
        # 预置一次失败计数（未达阈值不触发锁定），本次登录失败后恰好耗尽
        LoginBlockUtil("zhangsan", "127.0.0.1").incr_failed_count()
        resp = api_client.post(
            BASIC_LOGIN_URL,
            {"username": "zhangsan", "password": "Wrong@123456"},
            format="json",
        )
        assert resp.status_code == 400, resp.data
        _assert_bilingual(str(resp.data["detail"]), "The account has been locked", "账号已被锁定")


class TestVerifyCodeLogin:
    """验证码登录：token/验证码校验、username 与 email 两条分支。"""

    def _send_login_code(self, api_client, target, temp_token=None):
        """通过发送接口（login 类别 basic 通道 dryrun）获取 verify_token / verify_code。

        `temp_token` 非空时按 encrypted 契约先用它加密 target（与前端
        `ReSendVerifyCode` → `AesEncrypted(data.token, target)` 同一链路），
        并随请求带上该 token（服务端用它解密）。
        """
        payload = {"form_type": "username", "target": target}
        if temp_token:
            payload["target"] = AESCipherV2(temp_token).encrypt(target.encode()).decode()
            payload["token"] = temp_token
        # 临时 token 绑定客户端指纹（UA + Accept + IP）：取值请求与使用请求的 Accept
        # 必须一致，否则 verify_token_cache 校验失败（见 test_verify_code_api 同款写法）
        resp = api_client.post(
            "/api/system/auth/verify?category=login", payload, format="json", HTTP_ACCEPT="application/json"
        )
        assert resp.status_code == 200 and resp.data["code"] == 1000, resp.data
        data = resp.data["data"]
        return data["verify_token"], data["verify_code"]

    def test_login_code_forbidden(self, api_client, settings):
        settings.SECURITY_LOGIN_ACCESS_ENABLED = False
        resp = api_client.post(LOGIN_CODE_URL, {"verify_token": "x", "verify_code": "y"}, format="json")
        assert resp.status_code == 200, resp.data
        assert resp.data["code"] == 1001
        _assert_bilingual(str(resp.data["detail"]), "Login forbidden", "禁止登录")

    def test_login_code_missing_params(self, api_client, login_free):
        resp = api_client.post(LOGIN_CODE_URL, {}, format="json")
        assert resp.status_code == 400, resp.data
        _assert_bilingual(str(resp.data["detail"]), "Operation failed. Abnormal data", "操作失败，数据异常")

    def test_login_code_invalid_token(self, api_client, login_free):
        resp = api_client.post(LOGIN_CODE_URL, {"verify_token": "bad", "verify_code": "123456"}, format="json")
        assert resp.status_code == 400, resp.data
        _assert_bilingual(str(resp.data["detail"]), "Token is invalid or expired", "令牌无效或过期")

    def test_login_code_username_wrong_password(self, api_client, normal_user, login_free):
        verify_token, verify_code = self._send_login_code(api_client, "zhangsan")
        resp = api_client.post(
            LOGIN_CODE_URL,
            {"verify_token": verify_token, "verify_code": verify_code, "password": "Wrong@123456"},
            format="json",
        )
        assert resp.status_code == 400, resp.data
        assert not UserLoginLog.objects.filter(creator=normal_user, status=True).exists()

    def test_login_code_username_success(self, api_client, normal_user, login_free):
        verify_token, verify_code = self._send_login_code(api_client, "zhangsan")
        resp = api_client.post(
            LOGIN_CODE_URL,
            {"verify_token": verify_token, "verify_code": verify_code, "password": "Test@123456"},
            format="json",
        )
        assert resp.status_code == 200, resp.data
        assert resp.data["code"] == 1000, resp.data
        data = resp.data["data"]
        assert data["access"]
        assert data["refresh"]
        assert isinstance(data["access_token_lifetime"], int)
        # 登录成功写入成功日志并更新最近登录时间
        assert UserLoginLog.objects.filter(creator=normal_user, status=True).exists()
        normal_user.refresh_from_db()
        assert normal_user.last_login is not None

    def test_login_code_username_mfa_required(self, api_client, normal_user, login_free):
        """username 分支走 complete_login：开启 MFA 的账号返回 mfa_required 而非 token。"""
        normal_user.otp_secret_key = "JBSWY3DPEHPK3PXP"
        normal_user.mfa_level = normal_user.MFALevelChoices.ENABLED
        normal_user.save(update_fields=["otp_secret_key", "mfa_level"])
        verify_token, verify_code = self._send_login_code(api_client, "zhangsan")
        resp = api_client.post(
            LOGIN_CODE_URL,
            {"verify_token": verify_token, "verify_code": verify_code, "password": "Test@123456"},
            format="json",
        )
        assert resp.status_code == 200, resp.data
        assert resp.data["code"] == 1000, resp.data
        data = resp.data["data"]
        assert data["mfa_required"] is True
        assert data["mfa_token"]
        assert "access" not in data

    def test_login_code_email_success(self, api_client, normal_user, login_free):
        """email 动态因子登录：验证码通过即视为已验证，直接签发 token（不再走 MFA）。"""
        normal_user.email = "zhangsan@example.com"
        normal_user.save(update_fields=["email"])
        code = "654321"
        # dryrun 只落缓存不真发邮件，模拟短信/邮件验证码已下发
        SendAndVerifyCodeUtil(normal_user.email, code=code, backend="email", dryrun=True).gen_and_send()
        verify_token = TokenTempCache.generate_cache_token(
            300,
            {"target": normal_user.email, "form_type": "email", "query_key": "email", "extra": {}},
        )
        resp = api_client.post(
            LOGIN_CODE_URL,
            {"verify_token": verify_token, "verify_code": code},
            format="json",
        )
        assert resp.status_code == 200, resp.data
        assert resp.data["code"] == 1000, resp.data
        assert resp.data["data"]["access"]
        normal_user.refresh_from_db()
        assert normal_user.last_login is not None

    def test_login_code_email_wrong_code(self, api_client, normal_user, login_free):
        """验证码错误：计入重置/登录防爆破计数并拒绝。"""
        normal_user.email = "zhangsan@example.com"
        normal_user.save(update_fields=["email"])
        SendAndVerifyCodeUtil(normal_user.email, code="654321", backend="email", dryrun=True).gen_and_send()
        verify_token = TokenTempCache.generate_cache_token(
            300,
            {"target": normal_user.email, "form_type": "email", "query_key": "email", "extra": {}},
        )
        resp = api_client.post(
            LOGIN_CODE_URL,
            {"verify_token": verify_token, "verify_code": "000000"},
            format="json",
        )
        assert resp.status_code == 400, resp.data
        # 失败计数按验证目标（邮箱）累加
        assert cache.get("_LOGIN_LIMIT_zhangsan@example.com_127.0.0.1") == 1

    def test_login_code_encrypted_password(self, api_client, normal_user, settings):
        """encrypted 开启：target 用临时 token 加密下发，密码用 verify_token 解密。

        与前端契约一致：`ReSendVerifyCode` 加密 target（临时 token）、
        `loginByUsername` 加密 username+password；验证码登录态下服务端只解密码
        （target 从 verify_token 缓存取回，不再由请求携带明文）。
        """
        settings.SECURITY_LOGIN_CAPTCHA_ENABLED = False
        settings.SECURITY_LOGIN_TEMP_TOKEN_ENABLED = True
        settings.SECURITY_LOGIN_ENCRYPTED_ENABLED = True
        token_resp = api_client.get(TEMP_TOKEN_URL, HTTP_ACCEPT="application/json")
        assert token_resp.data["code"] == 1000, token_resp.data
        verify_token, verify_code = self._send_login_code(api_client, "zhangsan", temp_token=token_resp.data["token"])
        enc_password = AESCipherV2(verify_token).encrypt(b"Test@123456").decode()
        resp = api_client.post(
            LOGIN_CODE_URL,
            {"verify_token": verify_token, "verify_code": verify_code, "password": enc_password},
            format="json",
        )
        assert resp.status_code == 200, resp.data
        assert resp.data["code"] == 1000, resp.data
        assert resp.data["data"]["access"]

    def test_login_code_session_register_failure_fallback(self, api_client, normal_user, login_free, monkeypatch):
        """验证码登录会话登记失败：退回无 sid token，登录仍成功。"""

        def boom(request, user, login_type, channel_name=""):
            raise RuntimeError("session down")

        monkeypatch.setattr("system.views.auth.login.register_user_session", boom)
        verify_token, verify_code = self._send_login_code(api_client, "zhangsan")
        resp = api_client.post(
            LOGIN_CODE_URL,
            {"verify_token": verify_token, "verify_code": verify_code, "password": "Test@123456"},
            format="json",
        )
        assert resp.status_code == 200, resp.data
        assert resp.data["code"] == 1000, resp.data
        assert resp.data["data"]["access"]
        assert resp.data["data"]["refresh"]

    def test_login_code_session_claim_bind_failure_fallback(self, api_client, normal_user, login_free, monkeypatch):
        """验证码登录 sid claim 绑定失败：退回无 sid token。"""

        def boom(refresh_token, session_pk):
            raise RuntimeError("claim bind failed")

        monkeypatch.setattr("system.views.auth.login.bind_session_claim", boom)
        verify_token, verify_code = self._send_login_code(api_client, "zhangsan")
        resp = api_client.post(
            LOGIN_CODE_URL,
            {"verify_token": verify_token, "verify_code": verify_code, "password": "Test@123456"},
            format="json",
        )
        assert resp.status_code == 200, resp.data
        assert resp.data["code"] == 1000, resp.data
        assert resp.data["data"]["access"]
