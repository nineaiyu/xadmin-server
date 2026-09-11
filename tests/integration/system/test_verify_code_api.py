# -*- coding: utf-8 -*-
"""验证码发送 + 校验集成测试。

验证码发送走 username/basic 通道（dryrun），响应直接回显 verify_code，
避免依赖邮件/短信网关（默认 EMAIL_ENABLED/SMS_ENABLED 均为 False）。
"""

import pytest
from django.core.cache import cache

from common.base.utils import AESCipherV2
from common.sdk.sms.exceptions import CodeError, CodeExpired
from common.utils.verify_code import SendAndVerifyCodeUtil
from system.models import UserInfo
from system.views.auth.verify_code import SendVerifyCodeAPIView

pytestmark = pytest.mark.django_db

SEND_VERIFY_URL = "/api/system/auth/verify"
TEMP_TOKEN_URL = "/api/system/auth/token"
VERIFY_CODE_KEY_TPL = "auth_verify_code_{}"


def _assert_bilingual(text, en_kw, zh_kw):
    """文案断言兼容 zh/en 双语（活动语言不确定，二者命中其一即可）。"""
    assert en_kw in text or zh_kw in text, text


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


@pytest.fixture
def login_send_free(settings):
    """关闭登录/重置类别的辅助安全项（图片验证码 / 临时 token / 加密），basic 通道开启。"""
    settings.SECURITY_LOGIN_CAPTCHA_ENABLED = False
    settings.SECURITY_LOGIN_TEMP_TOKEN_ENABLED = False
    settings.SECURITY_LOGIN_ENCRYPTED_ENABLED = False
    settings.SECURITY_LOGIN_BY_BASIC_ENABLED = True
    settings.SECURITY_RESET_PASSWORD_CAPTCHA_ENABLED = False
    settings.SECURITY_RESET_PASSWORD_TEMP_TOKEN_ENABLED = False
    settings.SECURITY_RESET_PASSWORD_ENCRYPTED_ENABLED = False


class TestVerifyCodeConfig:
    """GET 配置接口：各 category 的字段结构与开关联动。"""

    def test_get_login_config(self, api_client, settings):
        settings.EMAIL_ENABLED = True
        resp = api_client.get(SEND_VERIFY_URL, {"category": "login"})
        assert resp.status_code == 200, resp.data
        assert resp.data["code"] == 1000
        data = resp.data["data"]
        assert {
            "access",
            "captcha",
            "token",
            "encrypted",
            "email",
            "sms",
            "basic",
            "rate",
            "lifetime",
            "reset",
            "register",
        } <= set(data)
        # email 开关 = 业务开关 且 邮件通道可用
        assert data["email"] is True
        assert data["lifetime"] == settings.SIMPLE_JWT["REFRESH_TOKEN_LIFETIME"].days

    def test_get_register_config(self, api_client):
        resp = api_client.get(SEND_VERIFY_URL, {"category": "register"})
        assert resp.data["code"] == 1000
        data = resp.data["data"]
        assert {"access", "captcha", "token", "encrypted", "email", "sms", "rate", "basic", "password"} <= set(data)
        # 密码规则结构：[{key, value}]
        for rule in data["password"]:
            assert {"key", "value"} <= set(rule)
            assert isinstance(rule["value"], int)

    def test_get_reset_config(self, api_client):
        resp = api_client.get(SEND_VERIFY_URL, {"category": "reset"})
        assert resp.data["code"] == 1000
        data = resp.data["data"]
        assert {"access", "captcha", "token", "encrypted", "email", "sms", "rate", "password"} <= set(data)

    def test_get_bind_email_config(self, api_client, settings):
        settings.EMAIL_ENABLED = False
        resp = api_client.get(SEND_VERIFY_URL, {"category": "bind_email"})
        assert resp.data["code"] == 1000
        data = resp.data["data"]
        assert data["email"] is False
        assert data["rate"] == settings.VERIFY_CODE_LIMIT

    def test_get_bind_phone_config(self, api_client):
        resp = api_client.get(SEND_VERIFY_URL, {"category": "bind_phone"})
        assert resp.data["code"] == 1000
        # 默认 SMS_ENABLED=False，sms 开关跟随通道可用性
        assert resp.data["data"]["sms"] is False

    def test_get_config_category_missing(self, api_client):
        """category 缺失 → 视图内 AttributeError 走 500 兜底。"""
        resp = api_client.get(SEND_VERIFY_URL)
        assert resp.status_code == 500, resp.data
        assert resp.data["code"] == 500

    def test_get_config_category_invalid(self, api_client):
        """category 非法 → 同样命中 getattr 失败的 500 兜底。"""
        resp = api_client.get(SEND_VERIFY_URL, {"category": "hacker"})
        assert resp.status_code == 500, resp.data
        assert resp.data["code"] == 500


class TestSendVerifyCodeLoginReset:
    """login / reset 类别发送：目标用户存在性校验与 dryrun 回显。"""

    def test_login_send_user_not_exist(self, api_client, login_send_free):
        resp = api_client.post(
            SEND_VERIFY_URL + "?category=login",
            {"form_type": "username", "target": "ghost"},
            format="json",
        )
        assert resp.status_code == 200, resp.data
        assert resp.data["code"] == 1001
        _assert_bilingual(str(resp.data["detail"]), "Username does not exist", "用户名不存在")

    def test_login_send_user_exist(self, api_client, normal_user, login_send_free):
        resp = api_client.post(
            SEND_VERIFY_URL + "?category=login",
            {"form_type": "username", "target": "zhangsan", "extra": {"k": "v"}},
            format="json",
        )
        assert resp.status_code == 200, resp.data
        assert resp.data["code"] == 1000, resp.data
        data = resp.data["data"]
        assert data["verify_token"]
        # username 通道 dryrun：回显验证码且与缓存一致
        assert data["verify_code"]
        assert data["extra"] == {"k": "v"}
        assert cache.get(VERIFY_CODE_KEY_TPL.format("zhangsan")) == data["verify_code"]

    def test_login_send_inactive_user_rejected(self, api_client, normal_user, login_send_free):
        """login 类别只对启用用户下发（check_reset_config 过滤 is_active=True）。"""
        normal_user.is_active = False
        normal_user.save(update_fields=["is_active"])
        resp = api_client.post(
            SEND_VERIFY_URL + "?category=login",
            {"form_type": "username", "target": "zhangsan"},
            format="json",
        )
        assert resp.data["code"] == 1001

    def test_reset_send_username_form_not_allowed(self, api_client, login_send_free):
        """reset 类别配置不含 basic 通道：username 表单直接判数据异常（1004）。"""
        resp = api_client.post(
            SEND_VERIFY_URL + "?category=reset",
            {"form_type": "username", "target": "ghost"},
            format="json",
        )
        assert resp.status_code == 200, resp.data
        assert resp.data["code"] == 1004

    def test_reset_send_email_user_exist(self, api_client, normal_user, settings, monkeypatch):
        """reset 类别 email 通道：目标用户存在则下发（mock 发送，不依赖邮件网关）。"""
        settings.SECURITY_RESET_PASSWORD_CAPTCHA_ENABLED = False
        settings.SECURITY_RESET_PASSWORD_TEMP_TOKEN_ENABLED = False
        settings.SECURITY_RESET_PASSWORD_ENCRYPTED_ENABLED = False
        settings.SECURITY_RESET_PASSWORD_BY_EMAIL_ENABLED = True
        settings.EMAIL_ENABLED = True
        normal_user.email = "zhangsan@example.com"
        normal_user.save(update_fields=["email"])
        monkeypatch.setattr(SendAndVerifyCodeUtil, "gen_and_send_async", lambda self: None)
        resp = api_client.post(
            SEND_VERIFY_URL + "?category=reset",
            {"form_type": "email", "target": "zhangsan@example.com"},
            format="json",
        )
        assert resp.status_code == 200, resp.data
        assert resp.data["code"] == 1000, resp.data
        assert resp.data["data"]["verify_token"]
        assert "verify_code" not in resp.data["data"]

    def test_reset_send_email_user_not_exist(self, api_client, settings):
        """reset 类别 email 通道：目标用户不存在 → 用户名/邮箱不存在文案。"""
        settings.SECURITY_RESET_PASSWORD_CAPTCHA_ENABLED = False
        settings.SECURITY_RESET_PASSWORD_TEMP_TOKEN_ENABLED = False
        settings.SECURITY_RESET_PASSWORD_ENCRYPTED_ENABLED = False
        settings.SECURITY_RESET_PASSWORD_BY_EMAIL_ENABLED = True
        settings.EMAIL_ENABLED = True
        resp = api_client.post(
            SEND_VERIFY_URL + "?category=reset",
            {"form_type": "email", "target": "ghost@example.com"},
            format="json",
        )
        assert resp.status_code == 200, resp.data
        assert resp.data["code"] == 1001
        _assert_bilingual(str(resp.data["detail"]), "Email does not exist", "邮件不存在")

    def test_login_send_encrypted_target(self, api_client, normal_user, settings):
        """encrypted + temp token 开启：先用临时 token 加密 target，服务端解密后下发。"""
        settings.SECURITY_LOGIN_CAPTCHA_ENABLED = False
        settings.SECURITY_LOGIN_TEMP_TOKEN_ENABLED = True
        settings.SECURITY_LOGIN_ENCRYPTED_ENABLED = True
        resp = api_client.get(TEMP_TOKEN_URL, HTTP_ACCEPT="application/json")
        assert resp.data["code"] == 1000, resp.data
        token = resp.data["token"]
        enc_target = AESCipherV2(token).encrypt(b"zhangsan").decode()
        resp = api_client.post(
            SEND_VERIFY_URL + "?category=login",
            {"form_type": "username", "target": enc_target, "token": token},
            format="json",
            HTTP_ACCEPT="application/json",
        )
        assert resp.status_code == 200, resp.data
        assert resp.data["code"] == 1000, resp.data
        # 解密后的 target 落到缓存，dryrun 回显与缓存一致
        code = cache.get(VERIFY_CODE_KEY_TPL.format("zhangsan"))
        assert code
        assert code == resp.data["data"]["verify_code"]

    def test_login_send_temp_token_missing(self, api_client, normal_user, settings):
        """temp token 开启但未携带 → 临时令牌校验失败（ValidateError → HTTP 400）。"""
        settings.SECURITY_LOGIN_CAPTCHA_ENABLED = False
        settings.SECURITY_LOGIN_TEMP_TOKEN_ENABLED = True
        resp = api_client.post(
            SEND_VERIFY_URL + "?category=login",
            {"form_type": "username", "target": "zhangsan"},
            format="json",
        )
        assert resp.status_code == 400, resp.data
        assert resp.data["code"] == 400
        _assert_bilingual(str(resp.data["detail"]), "Temporary Token validation failed", "临时Token校验失败")

    def test_login_send_captcha_missing(self, api_client, normal_user, settings):
        """图片验证码开启但缺 captcha_key/captcha_code → 校验失败。"""
        settings.SECURITY_LOGIN_CAPTCHA_ENABLED = True
        settings.SECURITY_LOGIN_TEMP_TOKEN_ENABLED = False
        resp = api_client.post(
            SEND_VERIFY_URL + "?category=login",
            {"form_type": "username", "target": "zhangsan"},
            format="json",
        )
        assert resp.status_code == 400, resp.data
        assert resp.data["code"] == 400
        _assert_bilingual(str(resp.data["detail"]), "Captcha validation failed", "图片验证码校验失败")

    def test_login_send_phone_via_sms(self, api_client, normal_user, settings, monkeypatch):
        """sms 通道开启：phone 表单类型映射为 sms 后端下发（非 dryrun，不回显验证码）。"""
        settings.SECURITY_LOGIN_BY_SMS_ENABLED = True
        settings.SMS_ENABLED = True
        settings.SECURITY_LOGIN_CAPTCHA_ENABLED = False
        settings.SECURITY_LOGIN_TEMP_TOKEN_ENABLED = False
        settings.SECURITY_LOGIN_ENCRYPTED_ENABLED = False
        normal_user.phone = "13800138000"
        normal_user.save(update_fields=["phone"])
        monkeypatch.setattr(SendAndVerifyCodeUtil, "gen_and_send_async", lambda self: None)
        resp = api_client.post(
            SEND_VERIFY_URL + "?category=login",
            {"form_type": "phone", "target": "13800138000"},
            format="json",
        )
        assert resp.status_code == 200, resp.data
        assert resp.data["code"] == 1000, resp.data
        assert resp.data["data"]["verify_token"]
        assert "verify_code" not in resp.data["data"]

    def test_send_rate_limit_then_block(self, api_client, register_free, settings):
        """发送限流：达到阈值后同一目标再次发送被锁定（ValidateError → HTTP 400）。"""
        settings.SECURITY_LOGIN_LIMIT_COUNT = 1
        first = api_client.post(
            SEND_VERIFY_URL + "?category=register",
            {"form_type": "username", "target": "ratelimit"},
            format="json",
        )
        assert first.data["code"] == 1000, first.data
        second = api_client.post(
            SEND_VERIFY_URL + "?category=register",
            {"form_type": "username", "target": "ratelimit"},
            format="json",
        )
        assert second.status_code == 400, second.data
        _assert_bilingual(str(second.data["detail"]), "The account has been locked", "账号已被锁定")


class TestSendVerifyCodeRegisterEmail:
    """register 类别 email 通道：已存在拒绝 / 新目标下发。"""

    @pytest.fixture
    def email_register(self, settings):
        settings.SECURITY_REGISTER_CAPTCHA_ENABLED = False
        settings.SECURITY_REGISTER_TEMP_TOKEN_ENABLED = False
        settings.SECURITY_REGISTER_ENCRYPTED_ENABLED = False
        settings.SECURITY_REGISTER_BY_EMAIL_ENABLED = True
        settings.EMAIL_ENABLED = True

    def test_register_email_already_exist(self, api_client, email_register):
        UserInfo.objects.create_user(username="hasmail", email="hasmail@example.com", password="Test@123456")
        resp = api_client.post(
            SEND_VERIFY_URL + "?category=register",
            {"form_type": "email", "target": "hasmail@example.com"},
            format="json",
        )
        assert resp.status_code == 200, resp.data
        assert resp.data["code"] == 1001
        _assert_bilingual(str(resp.data["detail"]), "Email already exist", "邮件已经存在")

    def test_register_email_send_success(self, api_client, email_register, monkeypatch):
        monkeypatch.setattr(SendAndVerifyCodeUtil, "gen_and_send_async", lambda self: None)
        resp = api_client.post(
            SEND_VERIFY_URL + "?category=register",
            {"form_type": "email", "target": "fresh@example.com"},
            format="json",
        )
        assert resp.status_code == 200, resp.data
        assert resp.data["code"] == 1000, resp.data
        assert resp.data["data"]["verify_token"]
        # email 非 dryrun：不回显验证码
        assert "verify_code" not in resp.data["data"]

    def test_register_username_already_exist(self, api_client, normal_user, register_free):
        resp = api_client.post(
            SEND_VERIFY_URL + "?category=register",
            {"form_type": "username", "target": "zhangsan"},
            format="json",
        )
        assert resp.status_code == 200, resp.data
        assert resp.data["code"] == 1001
        _assert_bilingual(str(resp.data["detail"]), "Username already exist", "用户名已经存在")

    def test_register_phone_already_exist(self, api_client, normal_user, settings, monkeypatch):
        """register 类别 sms 通道：手机号已注册 → 手机号已经存在。"""
        settings.SECURITY_REGISTER_CAPTCHA_ENABLED = False
        settings.SECURITY_REGISTER_TEMP_TOKEN_ENABLED = False
        settings.SECURITY_REGISTER_ENCRYPTED_ENABLED = False
        settings.SECURITY_REGISTER_BY_SMS_ENABLED = True
        settings.SMS_ENABLED = True
        normal_user.phone = "13800138001"
        normal_user.save(update_fields=["phone"])
        monkeypatch.setattr(SendAndVerifyCodeUtil, "gen_and_send_async", lambda self: None)
        resp = api_client.post(
            SEND_VERIFY_URL + "?category=register",
            {"form_type": "phone", "target": "13800138001"},
            format="json",
        )
        assert resp.status_code == 200, resp.data
        assert resp.data["code"] == 1001
        _assert_bilingual(str(resp.data["detail"]), "Phone already exist", "手机号已经存在")


class TestSendVerifyCodeBind:
    """bind_email / bind_phone 类别：目标已绑定时回传用户信息（含头像绝对地址）。"""

    def test_bind_email_existing_user_extra(self, api_client, normal_user, settings, monkeypatch):
        settings.EMAIL_ENABLED = True
        settings.SECURITY_BIND_EMAIL_CAPTCHA_ENABLED = False
        settings.SECURITY_BIND_EMAIL_TEMP_TOKEN_ENABLED = False
        settings.SECURITY_BIND_EMAIL_ENCRYPTED_ENABLED = False
        normal_user.email = "bind@example.com"
        normal_user.save(update_fields=["email"])
        # 头像字段为空文件对象，mock 绝对地址构造逻辑验证 request 拼接
        monkeypatch.setattr(
            "system.views.auth.verify_code.get_file_absolute_uri",
            lambda value, request=None, use_url=True: f"{request.scheme}://{request.get_host()}/media/avatar.png",
        )
        resp = api_client.post(
            SEND_VERIFY_URL + "?category=bind_email",
            {"form_type": "email", "target": "bind@example.com"},
            format="json",
        )
        assert resp.status_code == 200, resp.data
        assert resp.data["code"] == 1000, resp.data
        extra = resp.data["data"]["extra"]
        assert extra["username"] == "zhangsan"
        assert extra["nickname"] == "张三"
        # 头像必须带 request 的 scheme/host 绝对地址
        assert extra["avatar"] == "http://testserver/media/avatar.png"

    def test_bind_email_new_target_empty_extra(self, api_client, settings, monkeypatch):
        settings.EMAIL_ENABLED = True
        settings.SECURITY_BIND_EMAIL_CAPTCHA_ENABLED = False
        settings.SECURITY_BIND_EMAIL_TEMP_TOKEN_ENABLED = False
        settings.SECURITY_BIND_EMAIL_ENCRYPTED_ENABLED = False
        monkeypatch.setattr(SendAndVerifyCodeUtil, "gen_and_send_async", lambda self: None)
        resp = api_client.post(
            SEND_VERIFY_URL + "?category=bind_email",
            {"form_type": "email", "target": "nobody@example.com"},
            format="json",
        )
        assert resp.status_code == 200, resp.data
        assert resp.data["code"] == 1000, resp.data
        assert resp.data["data"]["extra"] == {}

    def test_bind_phone_existing_user_extra(self, api_client, normal_user, settings, monkeypatch):
        settings.SMS_ENABLED = True
        settings.SECURITY_BIND_PHONE_CAPTCHA_ENABLED = False
        settings.SECURITY_BIND_PHONE_TEMP_TOKEN_ENABLED = False
        settings.SECURITY_BIND_PHONE_ENCRYPTED_ENABLED = False
        normal_user.phone = "13800138000"
        normal_user.save(update_fields=["phone"])
        monkeypatch.setattr(SendAndVerifyCodeUtil, "gen_and_send_async", lambda self: None)
        resp = api_client.post(
            SEND_VERIFY_URL + "?category=bind_phone",
            {"form_type": "phone", "target": "13800138000"},
            format="json",
        )
        assert resp.status_code == 200, resp.data
        assert resp.data["code"] == 1000, resp.data
        assert resp.data["data"]["extra"]["username"] == "zhangsan"


class TestSendVerifyCodeFailurePaths:
    """校验函数异常与发送工具异常的兜底路径。"""

    def test_send_check_unexpected_error(self, api_client, register_free, monkeypatch):
        """check 阶段非 APIException 异常 → 统一 1001 数据异常文案（不泄露内部细节）。"""

        def boom(request, form_type, query_key, target):
            raise RuntimeError("boom")

        monkeypatch.setattr(SendVerifyCodeAPIView, "check_register_config", staticmethod(boom))
        resp = api_client.post(
            SEND_VERIFY_URL + "?category=register",
            {"form_type": "username", "target": "whoever"},
            format="json",
        )
        assert resp.status_code == 200, resp.data
        assert resp.data["code"] == 1001
        _assert_bilingual(str(resp.data["detail"]), "Operation failed. Abnormal data", "操作失败，数据异常")

    def test_send_sender_value_error(self, api_client, register_free, monkeypatch):
        """发送工具抛 ValueError（业务文案）→ 1002 透传。"""

        def boom(self):
            raise ValueError("send failed")

        monkeypatch.setattr(SendAndVerifyCodeUtil, "gen_and_send_async", boom)
        resp = api_client.post(
            SEND_VERIFY_URL + "?category=register",
            {"form_type": "username", "target": "whoever2"},
            format="json",
        )
        assert resp.status_code == 200, resp.data
        assert resp.data["code"] == 1002
        assert "send failed" in str(resp.data["detail"])
