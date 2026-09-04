# -*- coding: utf-8 -*-
"""system 登录 / 认证接口集成测试（关闭验证码、加密、临时 token 便于直接断言）。"""
import pytest

pytestmark = pytest.mark.django_db

BASIC_LOGIN_URL = "/api/system/login/basic"
CAPTCHA_URL = "/api/system/auth/captcha"
REFRESH_URL = "/api/system/refresh"
LOGOUT_URL = "/api/system/logout"


@pytest.fixture
def login_free(settings):
    """关闭登录辅助安全项：验证码 / 加密 / 临时 token。"""
    settings.SECURITY_LOGIN_CAPTCHA_ENABLED = False
    settings.SECURITY_LOGIN_ENCRYPTED_ENABLED = False
    settings.SECURITY_LOGIN_TEMP_TOKEN_ENABLED = False


class TestBasicLogin:
    def test_login_success(self, api_client, normal_user, login_free):
        resp = api_client.post(
            BASIC_LOGIN_URL,
            {"username": "zhangsan", "password": "Test@123456"},
            format="json",
        )
        assert resp.status_code == 200, resp.data
        assert resp.data["code"] == 1000
        assert resp.data["data"]["access"]
        assert resp.data["data"]["refresh"]

    def test_login_wrong_password(self, api_client, normal_user, login_free):
        resp = api_client.post(
            BASIC_LOGIN_URL,
            {"username": "zhangsan", "password": "Wrong@123456"},
            format="json",
        )
        # 登录失败显式返回 HTTP 400（ValidateError），避免前端 401 处理导致登录页刷新
        assert resp.status_code == 400
        assert resp.data["code"] == 400

    def test_refresh_token(self, api_client, normal_user, login_free):
        login_resp = api_client.post(
            BASIC_LOGIN_URL,
            {"username": "zhangsan", "password": "Test@123456"},
            format="json",
        )
        refresh = login_resp.data["data"]["refresh"]
        resp = api_client.post(REFRESH_URL, {"refresh": refresh}, format="json")
        assert resp.status_code == 200, resp.data
        assert resp.data["code"] == 1000
        assert resp.data["data"]["access"]

    def test_logout(self, api_client, normal_user, login_free):
        api_client.force_authenticate(user=normal_user)
        resp = api_client.post(LOGOUT_URL, {}, format="json")
        assert resp.status_code == 200
        assert resp.data["code"] == 1000

    def test_login_config(self, api_client, login_free):
        resp = api_client.get(BASIC_LOGIN_URL)
        assert resp.status_code == 200
        assert resp.data["data"]["access"] is True


class TestCaptchaApi:
    def test_get_captcha(self, api_client):
        resp = api_client.get(CAPTCHA_URL)
        assert resp.status_code == 200
        assert resp.data["code"] == 1000
        assert resp.data["captcha_key"]
        assert resp.data["captcha_image"]