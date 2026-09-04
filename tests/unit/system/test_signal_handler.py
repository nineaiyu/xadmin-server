# -*- coding: utf-8 -*-
"""system/signal_handler.py 信号处理器单元测试。

这些 handler 是一系列 post_save/pre_delete 的 receiver，副作用是批量清理
用户权限缓存（MagicCacheData / MagicCacheResponse）。本文件通过直接调用
handler（含参数对齐）与触发真实 post_save 信号两种方式，断言缓存键被清除。
"""
from unittest import mock

import pytest
from django.core.cache import cache

from system.models import DeptInfo, Menu, SystemConfig, UserInfo, UserRole
from system.signal_handler import (
    clean_cache_handler,
    invalid_config_cache_handler,
    invalid_dept_cache_handler,
    invalid_role_cache_handler,
    invalid_user_cache,
    invalid_user_cache_handler,
)

pytestmark = pytest.mark.django_db


def permission_cache_key(pk, method="GET"):
    """权限缓存实际存储的 key（MagicCacheData.invalid_caches 会拼接前缀）。"""
    return f"magic_cache_data_get_user_permission_{pk}_{method}"


def warm_permission_cache(user):
    """调用真实缓存函数预热，返回 {path: (pk, model)}。"""
    from common.core.permission import get_user_permission

    return get_user_permission(user, "GET")


class TestInvalidUserCacheHandler:
    def test_direct_call_clears_user_permission_cache(self):
        user = UserInfo.objects.create_user(username="u1", password="Test@123456")
        cache.set(permission_cache_key(user.pk), {"data": 1})
        invalid_user_cache_handler(sender=UserInfo, instance=user)
        assert cache.get(permission_cache_key(user.pk)) is None

    def test_post_save_signal_clears_user_permission_cache(self):
        user = UserInfo.objects.create_user(username="u1", password="Test@123456")
        cache.set(permission_cache_key(user.pk), {"data": 1})
        user.nickname = "新昵称"
        user.save(update_fields=["nickname"])
        assert cache.get(permission_cache_key(user.pk)) is None


class TestCleanCacheHandler:
    def test_menu_save_clears_superuser_permission_cache(self, superuser, menu_factory):
        menu = menu_factory("m", menu_type=Menu.MenuChoices.MENU)
        cache.set(permission_cache_key(superuser.pk), {"data": 1})
        clean_cache_handler(sender=Menu, instance=menu)
        assert cache.get(permission_cache_key(superuser.pk)) is None

    def test_menu_post_save_signal_clears_superuser_permission_cache(self, superuser, menu_factory):
        menu = menu_factory("m", menu_type=Menu.MenuChoices.MENU)
        cache.set(permission_cache_key(superuser.pk), {"data": 1})
        menu.rank = menu.rank + 1
        menu.save(update_fields=["rank"])
        assert cache.get(permission_cache_key(superuser.pk)) is None


class TestInvalidRoleCacheHandler:
    def test_role_save_clears_bound_user_permission_cache(self, normal_user, role):
        cache.set(permission_cache_key(normal_user.pk), {"data": 1})
        invalid_role_cache_handler(sender=UserRole, instance=role)
        assert cache.get(permission_cache_key(normal_user.pk)) is None

    def test_role_post_save_signal_clears_bound_user_permission_cache(self, normal_user, role):
        cache.set(permission_cache_key(normal_user.pk), {"data": 1})
        role.description = "desc"
        role.save(update_fields=["description"])
        assert cache.get(permission_cache_key(normal_user.pk)) is None


class TestInvalidDeptCacheHandler:
    def test_dept_save_clears_member_permission_cache(self, dept, normal_user):
        normal_user.dept = dept
        normal_user.save(update_fields=["dept"])
        cache.set(permission_cache_key(normal_user.pk), {"data": 1})
        invalid_dept_cache_handler(sender=DeptInfo, instance=dept)
        assert cache.get(permission_cache_key(normal_user.pk)) is None


class TestInvalidConfigCacheHandler:
    def test_config_save_invalidates_config_cache(self):
        instance = SystemConfig(key="site_name", value={}, description="desc")
        with mock.patch("system.signal_handler.SysConfig.invalid_config_cache") as inval:
            invalid_config_cache_handler(sender=SystemConfig, instance=instance)
        inval.assert_called_once_with("site_name")


class TestInvalidUserCacheSignal:
    def test_by_user_kwarg(self):
        user = UserInfo.objects.create_user(username="u1", password="Test@123456")
        cache.set(permission_cache_key(user.pk), {"data": 1})
        invalid_user_cache(sender=UserInfo, user=user)
        assert cache.get(permission_cache_key(user.pk)) is None

    def test_by_user_pk_kwarg(self):
        user = UserInfo.objects.create_user(username="u1", password="Test@123456")
        cache.set(permission_cache_key(user.pk), {"data": 1})
        invalid_user_cache(sender=UserInfo, user_pk=user.pk)
        assert cache.get(permission_cache_key(user.pk)) is None

    def test_without_user_returns_without_error(self):
        # 既无 user 也无 user_pk 时，handler 直接返回，不抛错且不影响其他缓存
        cache.set(permission_cache_key(999999), {"data": 1})
        invalid_user_cache(sender=UserInfo)
        assert cache.get(permission_cache_key(999999)) is not None


class TestRealCacheRefreshFlow:
    """真实链路测试：预热 get_user_permission 缓存后变更权限数据，断言结果立即刷新。

    覆盖三条变更路径（含不走 API 的 ORM M2M 直改）：
    - role.menu M2M（post_save 之外的 m2m_changed 路径）
    - user.roles M2M
    - dept.roles M2M（部门继承角色）
    """

    def test_role_menu_m2m_add_refreshes_cache(self, normal_user, role, menu_factory):
        assert warm_permission_cache(normal_user) == {}
        perm = menu_factory("用户查询", path="api/system/user$", method="GET")
        role.menu.add(perm)  # 不经过实例 save，依赖 m2m_changed 接收器失效
        assert "api/system/user$" in warm_permission_cache(normal_user)

    def test_role_menu_m2m_remove_refreshes_cache(self, normal_user, role, menu_factory):
        perm = menu_factory("用户查询", path="api/system/user$", method="GET")
        role.menu.add(perm)
        assert "api/system/user$" in warm_permission_cache(normal_user)
        role.menu.remove(perm)
        assert warm_permission_cache(normal_user) == {}

    def test_role_menu_m2m_clear_refreshes_cache(self, normal_user, role, menu_factory):
        perm = menu_factory("用户查询", path="api/system/user$", method="GET")
        role.menu.add(perm)
        assert "api/system/user$" in warm_permission_cache(normal_user)
        role.menu.clear()
        assert warm_permission_cache(normal_user) == {}

    def test_user_roles_m2m_add_refreshes_cache(self, role, menu_factory):
        perm = menu_factory("用户查询", path="api/system/user$", method="GET")
        role.menu.add(perm)
        user = UserInfo.objects.create_user(username="lisi", password="Test@123456")
        assert warm_permission_cache(user) == {}
        user.roles.add(role)  # 用户未重新 save，仅 M2M 变更
        assert "api/system/user$" in warm_permission_cache(user)

    def test_user_roles_m2m_remove_refreshes_cache(self, normal_user, role, menu_factory):
        perm = menu_factory("用户查询", path="api/system/user$", method="GET")
        role.menu.add(perm)
        assert "api/system/user$" in warm_permission_cache(normal_user)
        normal_user.roles.remove(role)
        assert warm_permission_cache(normal_user) == {}

    def test_dept_roles_m2m_add_refreshes_member_cache(self, dept, role, menu_factory):
        perm = menu_factory("用户查询", path="api/system/user$", method="GET")
        role.menu.add(perm)
        member = UserInfo.objects.create_user(username="wangwu", password="Test@123456")
        member.dept = dept
        member.save(update_fields=["dept"])
        assert warm_permission_cache(member) == {}
        dept.roles.add(role)  # 部门继承角色，仅 M2M 变更
        assert "api/system/user$" in warm_permission_cache(member)