# -*- coding: utf-8 -*-
"""接口文档视图测试（OpenAPI Schema / Swagger UI / 文档登录登出）。"""

from importlib import import_module

import pytest
from django.conf import settings
from django.test import RequestFactory

from common.swagger.views import ApiLogin, ApiLogout

pytestmark = pytest.mark.django_db

SCHEMA_URL = "/api-docs/schema/"
SWAGGER_UI_URL = "/api-docs/swagger/"
LOGIN_URL = "/api-docs/login/"

rf = RequestFactory()


def _attach_session(request):
    """RequestFactory 构造的请求默认无 session，手动挂接以便测试登录/登出。"""
    engine = import_module(settings.SESSION_ENGINE)
    request.session = engine.SessionStore()
    return request


class TestSchemaEndpoints:
    def test_json_schema_rendered(self, api_client):
        resp = api_client.get(SCHEMA_URL)
        assert resp.status_code == 200
        assert "openapi" in resp.json()
        # SchemaMixin 标记了 xframe_options_exempt，不应携带框架禁止头
        assert "X-Frame-Options" not in resp.headers

    def test_json_schema_uses_cache_key_by_user(self, api_client):
        """第二次请求命中响应缓存（缓存键由 get_cache_key 生成）仍应 200。"""
        assert api_client.get(SCHEMA_URL).status_code == 200
        assert api_client.get(SCHEMA_URL).status_code == 200

    def test_swagger_ui_rendered(self, api_client):
        resp = api_client.get(SWAGGER_UI_URL)
        assert resp.status_code == 200


class TestApiLogin:
    def test_post_wrong_credentials_returns_detail(self, api_client):
        resp = api_client.post(LOGIN_URL, {"username": "nobody", "password": "bad-pass"}, format="json")
        assert resp.status_code == 200
        assert resp.json()["detail"]

    def test_post_valid_credentials_redirects_to_next(self, superuser, api_client):
        resp = api_client.post(
            f"{LOGIN_URL}?next=/next-target/",
            {"username": "admin", "password": "Admin@123456"},
            format="json",
        )
        assert resp.status_code == 302
        assert resp["Location"] == "/next-target/"

    def test_post_valid_credentials_default_redirect(self, superuser, api_client):
        resp = api_client.post(LOGIN_URL, {"username": "admin", "password": "Admin@123456"}, format="json")
        assert resp.status_code == 302
        assert resp["Location"] == SWAGGER_UI_URL

    def test_get_anonymous_prompts_login(self):
        request = rf.get(LOGIN_URL)
        response = ApiLogin.as_view()(request)
        assert response.status_code == 200

    def test_get_authenticated_redirects_to_swagger(self, superuser):
        request = rf.get(LOGIN_URL)
        request.user = superuser
        response = ApiLogin.as_view()(request)
        assert response.status_code == 302
        assert response["Location"] == SWAGGER_UI_URL


class TestApiLoginHardening:
    """文档站登录加固（S-1）：开放重定向拦截 + 账号锁定接入（与主登录共用计数）。"""

    CREDENTIALS = {"username": "admin", "password": "Admin@123456"}

    def test_external_next_is_rejected(self, superuser, api_client):
        """外部域 next 回落到默认文档地址（防钓鱼跳转）。"""
        resp = api_client.post(f"{LOGIN_URL}?next=https://evil.example.com/phish", self.CREDENTIALS, format="json")
        assert resp.status_code == 302
        assert resp["Location"] == SWAGGER_UI_URL

    def test_protocol_relative_next_is_rejected(self, superuser, api_client):
        resp = api_client.post(f"{LOGIN_URL}?next=//evil.example.com/phish", self.CREDENTIALS, format="json")
        assert resp.status_code == 302
        assert resp["Location"] == SWAGGER_UI_URL

    def test_same_origin_absolute_next_is_allowed(self, superuser, api_client):
        target = "http://testserver/api-docs/redoc/"
        resp = api_client.post(f"{LOGIN_URL}?next={target}", self.CREDENTIALS, format="json")
        assert resp.status_code == 302
        assert resp["Location"] == target

    def test_locked_account_rejected_even_with_valid_password(self, superuser, api_client):
        """锁定判据复用主登录的失败计数键：达阈值后正确口令同样被拒。"""
        from settings.services import LoginBlockUtil

        block = LoginBlockUtil("admin", "127.0.0.1")
        for _ in range(int(settings.SECURITY_LOGIN_LIMIT_COUNT)):
            block.incr_failed_count()
        assert block.is_block()

        resp = api_client.post(LOGIN_URL, self.CREDENTIALS, format="json")
        assert resp.status_code == 200
        assert resp.json()["code"] == 1001
        # 本机有 .mo 时中文、CI 无 .mo 时英文，断言双语（项目既有一致口径）
        detail = str(resp.json()["detail"])
        assert "locked" in detail.lower() or "锁定" in detail

    def test_failed_login_increments_shared_counter(self, superuser, api_client):
        from settings.services import LoginBlockUtil

        api_client.post(LOGIN_URL, {"username": "admin", "password": "bad-pass"}, format="json")
        assert LoginBlockUtil("admin", "127.0.0.1").get_failed_count() >= 1

    def test_success_clears_failed_counter(self, superuser, api_client):
        from settings.services import LoginBlockUtil

        api_client.post(LOGIN_URL, {"username": "admin", "password": "bad-pass"}, format="json")
        assert api_client.post(LOGIN_URL, self.CREDENTIALS, format="json").status_code == 302
        assert LoginBlockUtil("admin", "127.0.0.1").get_failed_count() == 0


class TestApiLogout:
    def test_logout_redirects_to_login_page(self, superuser):
        request = _attach_session(rf.get("/api-docs/logout/"))
        request.user = superuser
        response = ApiLogout.as_view()(request)
        assert response.status_code == 302
        assert response["Location"] == "/api-docs/login/"
