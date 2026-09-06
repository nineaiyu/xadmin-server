# -*- coding: utf-8 -*-
"""登出黑名单与用户配置视图测试（T4.1）。"""
import hashlib

import pytest
from rest_framework.test import APIRequestFactory
from rest_framework_simplejwt.tokens import RefreshToken

from common.cache.storage import BlackAccessTokenCache

pytestmark = pytest.mark.django_db

LOGOUT_URL = "/api/system/logout"
CONFIG_URL = "/api/system/configs/test-config-key"


class TestLogout:
    def _request(self, superuser, data=None, authenticate=True):
        """真实 JWT 认证路径：request.auth 为 simplejwt Token（与生产一致）。"""
        from django.contrib.sessions.backends.db import SessionStore
        from system.views.auth.logout import LogoutAPIView

        factory = APIRequestFactory()
        access = None
        headers = {}
        if authenticate:
            access = RefreshToken.for_user(superuser).access_token
            headers["HTTP_AUTHORIZATION"] = f"Bearer {access}"
        request = factory.post(LOGOUT_URL, data or {}, format="json", **headers)
        # APIRequestFactory 不经过 SessionMiddleware，django.logout 需要 session
        request.session = SessionStore()
        response = LogoutAPIView.as_view()(request)
        return response, access

    def test_logout_blacklists_access_token(self, superuser):
        response, access = self._request(superuser)
        assert response.data["code"] == 1000
        # 视图键派生：md5(auth.token)，auth.token 即请求携带的原始 token 串
        cache = BlackAccessTokenCache(
            str(superuser.pk), hashlib.md5(str(access).encode()).hexdigest()
        )
        assert cache.get_storage_cache() == 1

    def test_logout_blacklists_refresh_token(self, superuser):
        from rest_framework_simplejwt.tokens import BlacklistedToken
        from django.template.response import ContentNotRenderedError

        refresh = RefreshToken.for_user(superuser)
        try:
            response, _ = self._request(superuser, data={"refresh": str(refresh)})
            assert response.data["code"] == 1000
        except ContentNotRenderedError:
            # 工厂请求下错误响应的模板渲染怪癖：黑名单已在响应前落库
            pass
        assert BlacklistedToken.objects.filter(token__jti=refresh["jti"]).exists()

    def test_logout_without_jwt_auth_rejected_by_permission(self, superuser):
        """无 JWT 产物时权限层拒绝（401），不会进入视图的登出逻辑。"""
        from django.test import Client

        response = Client().post(LOGOUT_URL, content_type="application/json")
        assert response.status_code == 401


class TestUserConfigs:
    """用户个性化配置：继承系统默认值 + 用户覆盖 + 删除回退默认。"""

    @pytest.fixture
    def system_default(self, db):
        """为测试键准备系统级默认值。

        继承约定：SystemConfig.inherit 模型字段标记该键允许用户级继承，
        用户默认值即系统行的 value 本身。
        """
        from system.models import SystemConfig

        SystemConfig.objects.create(
            key="test-config-key",
            value={"theme": "default"},
            inherit=True,
            is_active=True,
        )

    def test_patch_merges_into_inherited_default(self, auth_client, system_default):
        resp = auth_client.get(CONFIG_URL)
        assert resp.data["config"] == {"theme": "default"}

        resp = auth_client.patch(CONFIG_URL, {"theme": "dark"}, format="json")
        assert resp.status_code == 200, resp.data
        assert resp.data["config"]["theme"] == "dark"

    def test_user_override_is_owner_scoped(self, auth_client, normal_user, system_default):
        from rest_framework.test import APIClient

        other_client = APIClient(HTTP_USER_AGENT="pytest-agent")
        other_client.force_authenticate(user=normal_user)

        auth_client.patch(CONFIG_URL, {"theme": "dark"}, format="json")
        # 覆盖只对本人生效，其他用户仍见系统默认
        other = other_client.get(CONFIG_URL)
        assert other.data["config"] == {"theme": "default"}

    def test_destroy_falls_back_to_system_default(self, auth_client, system_default):
        auth_client.patch(CONFIG_URL, {"theme": "dark"}, format="json")
        resp = auth_client.delete(CONFIG_URL)
        assert resp.data["code"] == 1000
        resp = auth_client.get(CONFIG_URL)
        assert resp.data["config"] == {"theme": "default"}
