# -*- coding: utf-8 -*-
"""system.utils.menu 中的 get_view_permissions 单元测试（URL registry 依赖真实路由）。"""
import server.urls  # noqa: F401  # 预加载 urlconf，让 get_all_url_dict 的 import_string 生效

import pytest

from system.utils.menu import get_view_permissions

pytestmark = pytest.mark.django_db

USER_VIEW = "system.views.admin.user.UserViewSet"


class TestGetViewPermissions:
    def test_user_viewset_returns_permissions(self):
        perms = get_view_permissions(USER_VIEW)
        assert perms
        for p in perms:
            assert p["method"]
            assert p["url"]
            assert p["code"]
            assert p["description"]

    def test_standard_actions_carry_models(self):
        perms = get_view_permissions(USER_VIEW)
        models = set()
        for p in perms:
            if p["models"]:
                models.update(p["models"])
        assert models
        assert "system.userinfo" in models

    def test_unknown_view_returns_empty(self):
        assert get_view_permissions("system.views.admin.user.NoSuchViewSet") == []