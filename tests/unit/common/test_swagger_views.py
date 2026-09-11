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


class TestApiLogout:
    def test_logout_redirects_to_login_page(self, superuser):
        request = _attach_session(rf.get("/api-docs/logout/"))
        request.user = superuser
        response = ApiLogout.as_view()(request)
        assert response.status_code == 302
        assert response["Location"] == "/api-docs/login/"
