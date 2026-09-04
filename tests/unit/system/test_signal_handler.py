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