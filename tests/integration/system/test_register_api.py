# -*- coding: utf-8 -*-
"""注册接口集成测试。

注册前需先拿到 verify_token / verify_code。发送通道的行为由
``test_verify_code_api`` 覆盖，本文件手工构造验证码流程（直接向缓存写入
验证码并生成 verify_token），注册校验真实读取缓存中的验证码。默认配置下
注册加密开启，因此相关用例统一关闭加密以便直接传明文密码。
"""

import pytest

from common.utils.verify_code import SendAndVerifyCodeUtil, TokenTempCache
from identity.models import UserInfo

pytestmark = pytest.mark.django_db

REGISTER_URL = "/api/identity/register"

PASSWORD = "Test@123456"


@pytest.fixture
def register_free(settings):
    """关闭注册辅助安全项（图片验证码 / 临时 token / 加密），便于传明文密码并直达校验。"""
    settings.SECURITY_REGISTER_CAPTCHA_ENABLED = False
    settings.SECURITY_REGISTER_TEMP_TOKEN_ENABLED = False
    settings.SECURITY_REGISTER_ENCRYPTED_ENABLED = False


def _manual_verify(target, code="654321"):
    """手工构造验证码流程：验证码写入缓存 + 生成携带点位的 verify_token。"""
    SendAndVerifyCodeUtil(target, code=code, backend="email", dryrun=True).gen_and_send()
    token = TokenTempCache.generate_cache_token(
        300,
        {
            "target": target,
            "form_type": "username",
            "query_key": "username",
            "extra": {},
        },
    )
    return token, code


def _register(api_client, target, password=PASSWORD, channel="default", **extra):
    token, code = _manual_verify(target)
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
        token, code = _manual_verify(target)
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
