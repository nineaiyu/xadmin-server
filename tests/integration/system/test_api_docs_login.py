# -*- coding: utf-8 -*-
"""文档站登录闭环核验：未登录引导 → ApiLogin 建 session → schema/swagger 可达 → 登出回登录页。"""

import pytest
from rest_framework.test import APIClient

pytestmark = pytest.mark.django_db

DOCS_LOGIN_URL = "/api-docs/login/"
DOCS_LOGOUT_URL = "/api-docs/logout/"
SCHEMA_URL = "/api-docs/schema/"
SWAGGER_URL = "/api-docs/swagger/"


class TestApiDocsLoginFlow:
    def test_login_roundtrip(self, superuser):
        client = APIClient(HTTP_USER_AGENT="pytest-agent")

        # 1) 未登录 GET 登录页：返回提示（不跳转），未建 session
        prompted = client.get(DOCS_LOGIN_URL)
        assert prompted.status_code == 200

        # 2) POST 登录：账号密码正确 → 302 重定向 swagger 并建立 session
        login_resp = client.post(
            DOCS_LOGIN_URL,
            {"username": "admin", "password": "Admin@123456"},
        )
        assert login_resp.status_code == 302
        assert SWAGGER_URL in login_resp.headers["Location"]

        # 3) 登录态：schema 与 swagger 均可达
        assert client.get(SCHEMA_URL).status_code == 200
        assert client.get(SWAGGER_URL).status_code == 200

        # 4) 登出：回登录页（闭环完成）。登出后 schema 恢复不可见——访问控制
        #    由应用层承担（SERVE_PERMISSIONS 与权限点 retrieve:Spectacular* 同源），
        #    不依赖网关配置；匿名枚举 API 结构被应用层兜底拦截
        client.get(DOCS_LOGOUT_URL)
        assert client.get(DOCS_LOGIN_URL).status_code == 200
        assert client.get(SCHEMA_URL).status_code == 401

    def test_wrong_password_rejected(self, superuser):
        client = APIClient(HTTP_USER_AGENT="pytest-agent")
        resp = client.post(DOCS_LOGIN_URL, {"username": "admin", "password": "wrong"}, format="json")
        assert resp.status_code == 200  # ApiResponse 业务提示，不建 session
        assert "Incorrect" in str(resp.data) or resp.data.get("detail")

    def test_docs_session_cannot_access_business_api(self, superuser):
        """文档站 session 只信任 /api-docs/*，不能当通用 API session 使用。"""
        client = APIClient(HTTP_USER_AGENT="pytest-agent")
        login_resp = client.post(
            DOCS_LOGIN_URL,
            {"username": "admin", "password": "Admin@123456"},
        )
        assert login_resp.status_code == 302
        assert client.get(SCHEMA_URL).status_code == 200
        # 业务 API：docs 标记的 session 不被认证类接受（无 JWT/PAT 时 401）
        assert client.get("/api/identity/userinfo").status_code == 401

    def test_mfa_required_rejected(self, superuser):
        """要求 MFA 的账号不能经文档站登录静默建立 session。"""
        from unittest import mock

        client = APIClient(HTTP_USER_AGENT="pytest-agent")
        with (
            mock.patch("common.swagger.views.is_login_mfa_required", return_value=True),
            mock.patch("common.swagger.views.get_login_mfa_methods", return_value=[{"key": "otp"}]),
        ):
            resp = client.post(DOCS_LOGIN_URL, {"username": "admin", "password": "Admin@123456"})
        assert resp.status_code == 200
        assert resp.data["code"] == 1001
        assert "MFA" in resp.data["detail"]
        assert client.get(SCHEMA_URL).status_code == 401

    def test_login_policy_reject_denied(self, superuser):
        """主登录访问策略拒绝时，文档站登录不得绕过。"""
        from unittest import mock

        client = APIClient(HTTP_USER_AGENT="pytest-agent")
        with mock.patch(
            "common.swagger.views.evaluate_login_policy_for_request",
            return_value=(False, "Login is not allowed by policy: E2E"),
        ):
            resp = client.post(DOCS_LOGIN_URL, {"username": "admin", "password": "Admin@123456"})
        assert resp.status_code == 200
        assert resp.data["code"] == 1001
        assert "policy" in resp.data["detail"]
        assert client.get(SCHEMA_URL).status_code == 401
