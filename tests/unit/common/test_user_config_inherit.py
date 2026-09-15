# -*- coding: utf-8 -*-
"""UserPersonalConfigCache 用户级读路径语义测试（继承值不落个人缓存）。

个人缓存槽只存两种数据：个人行值（长 TTL）或缺席标记（短 TTL）。本文件守护：

1. 无个人行 → 读系统生效值（系统行值 → conf.py 静态默认），系统值变更即时生效，
   无需失效任何用户缓存；
2. 已有个人行 → 读个人值，系统值变更不重置个人行；
3. 缺席标记：读后落 no_row 标记；管理员经 ORM/管理页直写个人行（post_save 信号）
   后缓存被清理，用户立即读到个人值；删除个人行（post_delete）后回到系统生效值；
4. 系统行不存在 → 回退静态默认值（conf.py 单源），裸环境不会拿到 {} 撞上 int()/min()；
5. get_personal_config_data 只认真实个人行（system_fallback/no_row 标记不算）；
6. batch_user_config 缓存 miss 时回查个人行，无行回退系统级。
"""

import pytest
from django.core.cache import cache as django_cache

from common.cache.storage import UserSystemConfigCache
from common.core.config import SysConfig, UserConfig, batch_user_config
from server.const import CONFIG
from system.models import SystemConfig, UserInfo, UserPersonalConfig

pytestmark = pytest.mark.django_db

KEY = "EXPORT_ASYNC_MAX_RUNNING"


@pytest.fixture(autouse=True)
def _clear_config_cache():
    """FakeRedisCache 跨用例存活，逐用例清缓存防标记/个人值串扰。"""
    django_cache.clear()
    yield
    django_cache.clear()


@pytest.fixture
def user():
    return UserInfo.objects.create_user(username="ucfg-inherit", password="Test@123456")


def _personal_cache_key(pk):
    return UserSystemConfigCache(f"user_{pk}_{KEY}")


class TestInheritFromSystem:
    def test_no_personal_row_reads_system_value(self):
        SystemConfig.objects.create(key=KEY, value=7, inherit=True, is_active=True)
        user = UserInfo.objects.create_user(username="ucfg-a1", password="Test@123456")
        assert UserConfig(user.pk).get_value(KEY) == 7

    def test_system_value_change_propagates_immediately(self):
        """核心回归：系统默认值变更即时生效，未设置个人值的用户无需清缓存。"""
        SystemConfig.objects.create(key=KEY, value=7, inherit=True, is_active=True)
        user = UserInfo.objects.create_user(username="ucfg-a2", password="Test@123456")
        assert UserConfig(user.pk).get_value(KEY) == 7
        # set_value 走 post_save 信号自动失效系统级缓存
        SysConfig.set_value(KEY, 9)
        assert UserConfig(user.pk).get_value(KEY) == 9

    def test_no_personal_row_reads_system_value_even_flag_off(self):
        """inherit=false 的系统行值同样是系统生效值：用户级缺席时读到它（三级回退）。"""
        SystemConfig.objects.create(key=KEY, value=7, inherit=False, is_active=True)
        user = UserInfo.objects.create_user(username="ucfg-a3", password="Test@123456")
        assert UserConfig(user.pk).get_value(KEY) == 7

    def test_missing_system_row_falls_back_to_static_default(self, user):
        assert SystemConfig.objects.filter(key=KEY).exists() is False
        assert UserConfig(user.pk).get_value(KEY, CONFIG.EXPORT_ASYNC_MAX_RUNNING) == CONFIG.EXPORT_ASYNC_MAX_RUNNING


class TestPersonalRowPriority:
    def test_personal_row_survives_system_value_change(self, user):
        SystemConfig.objects.create(key=KEY, value=7, inherit=True, is_active=True)
        UserConfig(user.pk).set_value(KEY, 100, is_active=True, access=True)
        SysConfig.set_value(KEY, 9)
        assert UserConfig(user.pk).get_value(KEY) == 100
        # 个人行数据不被系统值变更重置
        assert UserPersonalConfig.objects.get(owner=user, key=KEY).value == 100

    def test_admin_orm_write_invalidates_absence_marker(self, user):
        """管理员在用户配置页/ORM 直写个人行（post_save 信号）→ 缓存被清理 → 立即生效。"""
        SystemConfig.objects.create(key=KEY, value=7, inherit=True, is_active=True)
        assert UserConfig(user.pk).get_value(KEY) == 7
        marker = _personal_cache_key(user.pk).get_storage_cache()
        assert marker == {"key": KEY, "no_row": True}
        UserPersonalConfig.objects.create(owner=user, key=KEY, value=88, is_active=True, access=True)
        assert UserConfig(user.pk).get_value(KEY) == 88

    def test_personal_row_delete_falls_back_to_inherit(self, user):
        SystemConfig.objects.create(key=KEY, value=7, inherit=True, is_active=True)
        UserConfig(user.pk).set_value(KEY, 100, is_active=True, access=True)
        assert UserConfig(user.pk).get_value(KEY) == 100
        # post_delete 信号清理个人值缓存，删除后回到继承口径
        UserPersonalConfig.objects.filter(owner=user, key=KEY).delete()
        assert UserConfig(user.pk).get_value(KEY) == 7


class TestAbsenceMarker:
    def test_marker_written_on_read(self, user):
        assert UserConfig(user.pk).get_value(KEY) == {}
        assert _personal_cache_key(user.pk).get_storage_cache() == {"key": KEY, "no_row": True}

    def test_marker_replaced_by_personal_value(self, user):
        assert UserConfig(user.pk).get_value(KEY) == {}
        UserConfig(user.pk).set_value(KEY, 66, is_active=True, access=True)
        # set_value 经 post_save 信号清缓存，个人值在下次读取时回填缓存槽
        assert UserConfig(user.pk).get_value(KEY) == 66
        cached = _personal_cache_key(user.pk).get_storage_cache()
        assert cached.get("no_row") is None
        assert cached["value"] == 66


class TestBatchUserConfig:
    def test_marker_skipped_and_system_default_used(self, user):
        UserConfig(user.pk).get_value(KEY)  # 预留 no_row 缺席标记
        result = batch_user_config([user.pk], KEY, default=5)
        assert result[user.pk] == 5

    def test_personal_value_respected_in_batch(self, user):
        UserConfig(user.pk).set_value(KEY, 66, is_active=True, access=True)
        result = batch_user_config([user.pk], KEY, default=5)
        assert result[user.pk] == 66


class TestPersonalConfigDataHelper:
    def test_system_fallback_is_not_personal_row(self, user):
        """系统生效值回退（system_fallback）不被误判为个人行。"""
        SystemConfig.objects.create(key=KEY, value=7, is_active=True)
        from common.core.config import get_personal_config_data, get_personal_int_config

        assert get_personal_config_data(user, KEY) is None
        assert get_personal_int_config(user, KEY, 99) == 99

    def test_real_personal_row_recognized(self, user):
        from common.core.config import get_personal_config_data, get_personal_int_config

        UserConfig(user.pk).set_value(KEY, 88, is_active=True, access=True)
        assert get_personal_config_data(user, KEY)["value"] == 88
        assert get_personal_int_config(user, KEY, 99) == 88
