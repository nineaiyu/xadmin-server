# -*- coding: utf-8 -*-
"""公共测试 fixtures。"""

import pytest
from django.core.cache import cache
from rest_framework.test import APIClient

from server.utils import set_current_request
from system.models import DeptInfo, Menu, MenuMeta, UserInfo, UserRole


@pytest.fixture(autouse=True)
def _clean_cache():
    """每个测试前后清空缓存，避免 MagicCacheData（权限缓存 24h）跨测试污染。"""
    cache.clear()
    yield
    cache.clear()


@pytest.fixture(autouse=True, scope="session")
def _isolate_settings_pubsub():
    """测试进程内禁用 Setting 热更新 pub/sub。

    生产链路是「保存 → Redis pub/sub → 后台订阅线程回写 django.conf.settings」；
    测试里保留后台线程会让某个测试保存的配置被**异步**回灌全局 settings，
    造成同 worker 内跨测试污染（历史上表现为偶发的 LDAP/通知设置断言失败）。
    置为惰性桩后，需要验证回写效果的测试按
    tests/integration/test_monitor_settings.py 的约定显式调用 refresh_setting()。
    """
    from settings import signal_handlers

    class _NoopPubSub:
        def publish(self, data):
            return True

        def subscribe(self, *_args, **_kwargs):
            return None

    original = signal_handlers.setting_pub_sub
    signal_handlers.setting_pub_sub = _NoopPubSub()
    yield
    signal_handlers.setting_pub_sub = original


@pytest.fixture(autouse=True)
def _clean_thread_local():
    """每个测试后清理 thread-local 中残留的 request，避免污染序列化器测试。"""
    yield
    set_current_request(None)


@pytest.fixture
def api_client():
    # 附带 User-Agent：ApiLoggingMiddleware 直接取 META['HTTP_USER_AGENT']，缺失会 500
    return APIClient(HTTP_USER_AGENT="pytest-agent")


@pytest.fixture
def superuser(db):
    return UserInfo.objects.create_superuser(username="admin", email="admin@example.com", password="Admin@123456")


@pytest.fixture
def role(db):
    return UserRole.objects.create(name="普通用户", code="common")


@pytest.fixture
def dept(db):
    return DeptInfo.objects.create(name="研发部", code="dev")


@pytest.fixture
def normal_user(db, role):
    user = UserInfo.objects.create_user(username="zhangsan", password="Test@123456", nickname="张三")
    user.roles.add(role)
    return user


@pytest.fixture
def menu_factory(db):
    """创建菜单的工厂。权限类型菜单需绑定 path（正则，不带前导斜杠）与 method。"""

    def _make(
        name,
        path=None,
        method=None,
        menu_type=Menu.MenuChoices.PERMISSION,
        parent=None,
        is_active=True,
    ):
        meta = MenuMeta.objects.create(title=name)
        return Menu.objects.create(
            name=name,
            path=path or "",
            method=method,
            menu_type=menu_type,
            parent=parent,
            meta=meta,
            is_active=is_active,
        )

    return _make


@pytest.fixture
def auth_client(api_client, superuser):
    """以超级管理员身份请求（跳过权限校验，专注 ViewSet 冒烟）。"""
    api_client.force_authenticate(user=superuser)
    return api_client


@pytest.fixture
def module_config(settings):
    """应用一次功能模块裁剪配置（preset / enable / disable）并清空派生缓存。

    模块组合变更在生产环境需重启进程；测试中通过 settings + reset_module_state()
    模拟同等效果（见 common/core/modules/ 包）。
    """
    from common.core.modules import reset_module_state

    def _apply(preset="full", enable=(), disable=()):
        settings.MODULE_PRESET = preset
        settings.MODULE_ENABLE = list(enable)
        settings.MODULE_DISABLE = list(disable)
        reset_module_state()

    yield _apply
    reset_module_state()
