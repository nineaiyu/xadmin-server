# -*- coding: utf-8 -*-
"""common/core/permission.py 菜单/接口权限校验单元测试。"""
import pytest
from rest_framework.exceptions import NotAuthenticated, PermissionDenied
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory, force_authenticate

from common.core.permission import (
    IsAuthenticated,
    get_menu_pk,
    get_user_menu_queryset,
    get_user_permission,
)
from system.models import Menu, UserInfo

pytestmark = pytest.mark.django_db

factory = APIRequestFactory()


def make_request(path, user=None, method="get"):
    # force_authenticate 必须在 Request 包装前设置到底层 WSGIRequest，
    # DRF Request.__init__ 读取 _force_auth_user 决定 authenticators
    django_request = getattr(factory, method)(path)
    if user is not None:
        force_authenticate(django_request, user=user)
    return Request(django_request, authenticators=[])


class TestGetUserMenuQueryset:
    def test_user_without_role_returns_none(self, db):
        user = UserInfo.objects.create_user(username="norole", password="Test@123456")
        assert get_user_menu_queryset(user) is None

    def test_role_menu_included(self, normal_user, role, menu_factory):
        menu = menu_factory("菜单1", menu_type=Menu.MenuChoices.MENU, path="/system/user/index")
        role.menu.add(menu)
        assert menu in get_user_menu_queryset(normal_user)

    def test_inactive_role_menu_excluded(self, normal_user, role, menu_factory):
        menu = menu_factory("菜单1", menu_type=Menu.MenuChoices.MENU, path="/system/user/index")
        role.menu.add(menu)
        role.is_active = False
        role.save(update_fields=["is_active"])
        assert menu not in get_user_menu_queryset(normal_user)

    def test_inactive_menu_excluded(self, normal_user, role, menu_factory):
        menu = menu_factory("菜单1", menu_type=Menu.MenuChoices.MENU, path="/x", is_active=False)
        role.menu.add(menu)
        assert menu not in get_user_menu_queryset(normal_user)

    def test_dept_bound_role_menu_included(self, dept, normal_user, role, menu_factory):
        """用户未直接绑定角色，角色由其部门绑定时也可获得菜单。"""
        normal_user.dept = dept
        normal_user.save(update_fields=["dept"])
        menu = menu_factory("菜单1", menu_type=Menu.MenuChoices.MENU, path="/x")
        role.menu.add(menu)
        dept.roles.add(role)
        assert menu in get_user_menu_queryset(normal_user)


class TestGetUserPermission:
    def test_only_permission_type_menu_with_matching_method(self, normal_user, role, menu_factory):
        get_menu = menu_factory("p-list", path="api/demo/book$", method="GET")
        post_menu = menu_factory("p-create", path="api/demo/book-save$", method="POST")
        role.menu.add(get_menu, post_menu)

        perms = get_user_permission(normal_user, "GET")
        assert "api/demo/book$" in perms
        assert "api/demo/book-save$" not in perms

    def test_result_cached_per_user_and_method(self, normal_user, role, menu_factory):
        menu = menu_factory("p-list", path="api/demo/book$", method="GET")
        role.menu.add(menu)
        first = get_user_permission(normal_user, "GET")
        second = get_user_permission(normal_user, "GET")
        assert first == second


class TestGetMenuPk:
    def test_exact_match_with_dollar_anchor(self):
        data = {"api/demo/book$": (1, None)}
        assert get_menu_pk(data, "/api/demo/book") == (1, None)

    def test_regex_prefix_match(self):
        data = {"api/system/user/[^/.]+$": (2, None)}
        assert get_menu_pk(data, "/api/system/user/123") == (2, None)

    def test_no_match_returns_none(self):
        assert get_menu_pk({}, "/api/other") is None


class TestIsAuthenticatedPermission:
    permission = IsAuthenticated()

    def test_unauthenticated_raises_not_authenticated(self):
        request = make_request("/api/demo/book")
        with pytest.raises(NotAuthenticated):
            self.permission.has_permission(request, None)

    def test_superuser_allowed_and_ignores_field_permission(self, superuser):
        request = make_request("/api/demo/book", superuser)
        assert self.permission.has_permission(request, None) is True
        assert request.ignore_field_permission is True

    def test_white_url_allowed_without_menu(self, normal_user, settings):
        settings.PERMISSION_WHITE_URL = {"/api/common/health": ["GET"]}
        request = make_request("/api/common/health", normal_user)
        assert self.permission.has_permission(request, None) is True

    def test_white_url_method_mismatch_denied(self, normal_user, settings):
        settings.PERMISSION_WHITE_URL = {"/api/common/health": ["POST"]}
        request = make_request("/api/common/health", normal_user)
        with pytest.raises(PermissionDenied):
            self.permission.has_permission(request, None)

    def test_permission_granted_sets_user_menu(self, normal_user, role, menu_factory):
        menu = menu_factory("p-list", path="api/demo/book$", method="GET")
        role.menu.add(menu)
        request = make_request("/api/demo/book", normal_user)
        assert self.permission.has_permission(request, None) is True
        assert request.user.menu == menu.pk

    def test_permission_denied_without_menu(self, normal_user):
        request = make_request("/api/demo/book", normal_user)
        with pytest.raises(PermissionDenied):
            self.permission.has_permission(request, None)

    def test_search_columns_reuses_list_permission(self, normal_user, role, menu_factory):
        """/search-columns 请求应复用列表接口的权限。"""
        menu = menu_factory("p-list", path="api/demo/book$", method="GET")
        role.menu.add(menu)
        request = make_request("/api/demo/book/search-columns", normal_user)
        assert self.permission.has_permission(request, None) is True

    def test_import_data_falls_back_to_base_url_permission(self, normal_user, role, menu_factory):
        """导入导出接口未绑定模型时，回退到基础 url 的权限。"""
        base_menu = menu_factory("p-save", path="api/demo/book$", method="POST")
        role.menu.add(base_menu)
        import_menu = menu_factory("p-import", path="api/demo/book/import-data$", method="POST")
        role.menu.add(import_menu)

        request = make_request("/api/demo/book/import-data", normal_user, method="post")
        assert self.permission.has_permission(request, None) is True