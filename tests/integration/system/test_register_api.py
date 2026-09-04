# -*- coding: utf-8 -*-
"""注册接口集成测试。

注册前需先通过验证码流程拿到 verify_token / verify_code。username/basic
通道 dryrun 回显验证码；注册校验真实读取缓存中的验证码。默认配置下
注册加密开启，因此相关用例统一关闭加密以便直接传明文密码。
"""
import pytest

from common.utils.verify_code import SendAndVerifyCodeUtil, TokenTempCache
from system.models import UserInfo

pytestmark = pytest.mark.django_db

SEND_VERIFY_URL = "/api/system/auth/verify"
REGISTER_URL = "/api/system/register"

PASSWORD = "Test@123456"


@pytest.fixture
def register_free(settings):
    """关闭注册辅助安全项（图片验证码 / 临时 token / 加密），便于传明文密码并直达校验。"""
    settings.SECURITY_REGISTER_CAPTCHA_ENABLED = False
    settings.SECURITY_REGISTER_TEMP_TOKEN_ENABLED = False
    settings.SECURITY_REGISTER_ENCRYPTED_ENABLED = False
    settings.SECURITY_REGISTER_BY_BASIC_ENABLED = True


def _send_verify(api_client, target):
    """通过发送验证码接口获取 verify_token / verify_code（username 通道回显验证码）。"""
    resp = api_client.post(
        SEND_VERIFY_URL + "?category=register",
        {"form_type": "username", "target": target},
        format="json",
    )
    assert resp.status_code == 200 and resp.data["code"] == 1000, resp.data
    return resp.data["data"]["verify_token"], resp.data["data"]["verify_code"]


def _manual_verify(target):
    """为已存在用户目标手工构造验证码流程（发送接口对已注册用户会拒绝）。"""
    SendAndVerifyCodeUtil(target, code="654321", backend="username", dryrun=True).gen_and_send()
    token = TokenTempCache.generate_cache_token(300, {
        "target": target, "form_type": "username", "query_key": "username", "extra": {},
    })
    return token, "654321"


def _register(api_client, target, password=PASSWORD, channel="default", **extra):
    token, code = _send_verify(api_client, target)
    body = {"channel": channel, "verify_token": token, "verify_code": code, "password": password}
    body.update(extra)
    return api_client.post(REGISTER_URL, body, format="json")


class TestRegister:
    def test_register_success(self, api_client, register_free):
        target = "newuser"
        resp = _register(api_client, target)
        assert resp.status_code == 200, resp.data
        assert resp.data["code"] == 1000, resp.data
        assert resp.data["data"]["access"]
        assert resp.data["data"]["refresh"]
        user = UserInfo.objects.get(username=target)
        assert user.check_password(PASSWORD)

    def test_register_missing_password(self, api_client, register_free):
        target = "nopassword"
        token, code = _send_verify(api_client, target)
        resp = api_client.post(
            REGISTER_URL,
            {"channel": "default", "verify_token": token, "verify_code": code},
            format="json",
        )
        assert resp.status_code == 200, resp.data
        assert resp.data["code"] == 1004
        assert not UserInfo.objects.filter(username=target).exists()

    def test_register_weak_password(self, api_client, register_free):
        target = "weakpassword"
        resp = _register(api_client, target, password="abc!")
        assert resp.status_code == 200, resp.data
        assert resp.data["code"] == 1001
        assert not UserInfo.objects.filter(username=target).exists()

    def test_register_duplicate(self, api_client, register_free):
        target = "dupuser"
        resp = _register(api_client, target)
        assert resp.data["code"] == 1000, resp.data

        # 目标已存在，发送接口会拒绝，需手工构造其验证码流程
        token, code = _manual_verify(target)
        resp2 = api_client.post(
            REGISTER_URL,
            {"channel": "default", "verify_token": token, "verify_code": code, "password": PASSWORD},
            format="json",
        )
        assert resp2.status_code == 200, resp2.data
        assert resp2.data["code"] == 1002
        assert UserInfo.objects.filter(username=target).count() == 1

    def test_register_access_disabled(self, api_client, settings):
        settings.SECURITY_REGISTER_ACCESS_ENABLED = False
        resp = api_client.post(
            REGISTER_URL,
            {"verify_token": "x", "verify_code": "y", "password": PASSWORD},
            format="json",
        )
        assert resp.status_code == 200, resp.data
        assert resp.data["code"] == 1001
        assert not UserInfo.objects.filter(username="x").exists()


class TestRegisterAutoBindDept:
    def test_register_auto_bind_dept(self, api_client, dept, register_free):
        dept.code = "devcode"
        dept.auto_bind = True
        dept.is_active = True
        dept.save()
        target = "devuser"
        resp = _register(api_client, target, channel="devcode")
        assert resp.status_code == 200, resp.data
        assert resp.data["code"] == 1000, resp.data
        user = UserInfo.objects.get(username=target)
        assert user.dept_id == dept.pk
        assert user.dept_belong_id == dept.pk
        assert user.creator_id == user.pk