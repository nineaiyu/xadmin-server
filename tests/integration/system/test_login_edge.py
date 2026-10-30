# -*- coding: utf-8 -*-
"""登录接口边界分支：禁用开关 / 会话登记降级 / 锁定文案 / 验证码登录全链路。"""

import pytest
from django.core.cache import cache

from audit.models.log import UserLoginLog
from common.base.crypto import AESCipherV2
from common.utils.verify_code import SendAndVerifyCodeUtil, TokenTempCache
from identity.services.auth_login import complete_login
from identity.utils.auth import ValidateError
from settings.services import LoginBlockUtil

pytestmark = pytest.mark.django_db

BASIC_LOGIN_URL = "/api/identity/login/basic"
LOGIN_CODE_URL = "/api/identity/login/code"
TEMP_TOKEN_URL = "/api/identity/auth/token"


def _assert_bilingual(text, en_kw, zh_kw):
    """文案断言兼容 zh/en 双语（活动语言不确定，二者命中其一即可）。"""
    assert en_kw in text or zh_kw in text, text


@pytest.fixture
def login_free(settings):
    """关闭登录辅助安全项：验证码 / 加密 / 临时 token。"""
    settings.SECURITY_LOGIN_CAPTCHA_ENABLED = False
    settings.SECURITY_LOGIN_ENCRYPTED_ENABLED = False
    settings.SECURITY_LOGIN_TEMP_TOKEN_ENABLED = False


@pytest.fixture
def email_channel_on(settings):
    """email 通道开放(场景开关 + 投递通道):提交侧复核通道后,email 用例需显式打开。"""
    settings.SECURITY_LOGIN_BY_EMAIL_ENABLED = True
    settings.EMAIL_ENABLED = True


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

        monkeypatch.setattr("identity.services.token_issue_login.register_user_session", boom)
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

        monkeypatch.setattr("identity.services.token_issue_login.bind_session_claim", boom)
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

    def test_complete_login_rejects_inactive_user(self, normal_user):
        """防御纵深：禁用账号在任何经 complete_login 收口的登录路径兜底拒绝。

        发码 / IdP 认证到提交之间存在「账号随后被禁用」的窗口，且文档约定
        complete_login 是所有（含未来）登录路径的唯一收口——is_active 校验
        在此兜底，新增登录方式漏做校验也不会放行。
        """
        normal_user.is_active = False
        normal_user.save(update_fields=["is_active"])
        with pytest.raises(ValidateError):
            complete_login(None, normal_user)


class TestTempTokenExpire:
    """临时令牌独立时效：TTL 取安全设置 SECURITY_TEMP_TOKEN_EXPIRE（不再硬编码 600）。"""

    def test_temp_token_uses_configured_expire(self, api_client, settings, monkeypatch):
        captured = {}

        def fake_make_token_cache(key, time_limit=60, **kwargs):
            captured["time_limit"] = time_limit
            return "tmp_token_fake"

        monkeypatch.setattr("identity.views.auth.token.make_token_cache", fake_make_token_cache)
        settings.SECURITY_TEMP_TOKEN_EXPIRE = 1200
        resp = api_client.get(TEMP_TOKEN_URL, HTTP_ACCEPT="application/json")
        assert resp.data["code"] == 1000, resp.data
        assert resp.data["token"] == b"tmp_token_fake"
        assert captured["time_limit"] == 1200

    def test_temp_token_falls_back_to_default(self, api_client, settings, monkeypatch):
        """配置缺失/为 0 时回落 600 秒默认，不因异常配置拒绝签发。"""
        captured = {}

        def fake_make_token_cache(key, time_limit=60, **kwargs):
            captured["time_limit"] = time_limit
            return "tmp_token_fake"

        monkeypatch.setattr("identity.views.auth.token.make_token_cache", fake_make_token_cache)
        settings.SECURITY_TEMP_TOKEN_EXPIRE = 0
        resp = api_client.get(TEMP_TOKEN_URL, HTTP_ACCEPT="application/json")
        assert resp.data["code"] == 1000, resp.data
        assert captured["time_limit"] == 600


class TestVerifyCodeLogin:
    """验证码登录：token/验证码校验、username 与 email 两条分支。"""

    def _send_login_code(self, api_client, target):
        """手工构造账号密码组合登录（username 分支）的验证码流程。

        发送通道已不再提供 username 表单类型（行为见 test_verify_code_api），
        此处直接向缓存写入验证码并生成 verify_token，覆盖登录端点的
        username 分支语义（密码校验 / MFA / 会话登记）。
        """
        code = "654321"
        SendAndVerifyCodeUtil(target, code=code, backend="email", dryrun=True).gen_and_send()
        verify_token = TokenTempCache.generate_cache_token(
            300,
            {"target": target, "form_type": "username", "query_key": "username", "extra": {}},
        )
        return verify_token, code

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
        # 文案随翻译源略有差异（项目域"令牌无效或过期"/令牌组件域"令牌无效或已过期"），中文取稳定前缀
        _assert_bilingual(str(resp.data["detail"]), "Token is invalid or expired", "令牌无效")

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

    def test_login_code_email_success(self, api_client, normal_user, login_free, email_channel_on):
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

    def test_login_code_email_wrong_code(self, api_client, normal_user, login_free, email_channel_on):
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

    def test_login_code_email_disabled_after_send(self, api_client, normal_user, login_free, email_channel_on):
        """发码后账号被禁用：提交时兜底复核 is_active，统一失败文案（防枚举），不签发 token。"""
        normal_user.email = "zhangsan@example.com"
        normal_user.save(update_fields=["email"])
        code = "654321"
        SendAndVerifyCodeUtil(normal_user.email, code=code, backend="email", dryrun=True).gen_and_send()
        verify_token = TokenTempCache.generate_cache_token(
            300,
            {"target": normal_user.email, "form_type": "email", "query_key": "email", "extra": {}},
        )
        # 模拟「发码成功之后、提交之前」账号被管理员禁用
        normal_user.is_active = False
        normal_user.save(update_fields=["is_active"])
        resp = api_client.post(
            LOGIN_CODE_URL,
            {"verify_token": verify_token, "verify_code": code},
            format="json",
        )
        assert resp.status_code == 400, resp.data
        assert "access" not in (resp.data.get("data") or {})

    def test_login_code_email_deleted_after_send(self, api_client, normal_user, login_free, email_channel_on):
        """发码后账号被删除：走统一失败文案，不再抛未处理的 DoesNotExist（500）。"""
        normal_user.email = "zhangsan@example.com"
        normal_user.save(update_fields=["email"])
        code = "654321"
        SendAndVerifyCodeUtil(normal_user.email, code=code, backend="email", dryrun=True).gen_and_send()
        verify_token = TokenTempCache.generate_cache_token(
            300,
            {"target": normal_user.email, "form_type": "email", "query_key": "email", "extra": {}},
        )
        normal_user.delete()
        resp = api_client.post(
            LOGIN_CODE_URL,
            {"verify_token": verify_token, "verify_code": code},
            format="json",
        )
        assert resp.status_code == 400, resp.data

    def test_login_code_encrypted_password(self, api_client, normal_user, settings):
        """encrypted 开启：密码用 verify_token 解密（target 从 verify_token 缓存取回）。

        与前端契约一致：`loginByUsername` 加密 username+password；验证码登录态下
        服务端只解密码，不再由请求携带明文 target。
        """
        settings.SECURITY_LOGIN_CAPTCHA_ENABLED = False
        settings.SECURITY_LOGIN_ENCRYPTED_ENABLED = True
        verify_token, verify_code = self._send_login_code(api_client, "zhangsan")
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

        monkeypatch.setattr("identity.services.token_issue_login.register_user_session", boom)
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

        monkeypatch.setattr("identity.services.token_issue_login.bind_session_claim", boom)
        verify_token, verify_code = self._send_login_code(api_client, "zhangsan")
        resp = api_client.post(
            LOGIN_CODE_URL,
            {"verify_token": verify_token, "verify_code": verify_code, "password": "Test@123456"},
            format="json",
        )
        assert resp.status_code == 200, resp.data
        assert resp.data["code"] == 1000, resp.data
        assert resp.data["data"]["access"]
