# -*- coding: utf-8 -*-
"""配置批量读取（SysConfig.get_values / batch_user_config_values）语义守护。

批量结果必须与逐个单读（get_value / UserConfig(owner).get_value）完全一致：
命中取缓存值、未激活/缺席回退默认值或系统生效值、解密与渲染口径不变。
差异只允许在往返次数（get_many / key__in 各至多一次），不允许在返回值。
"""

import pytest
from django.core.cache import cache as django_cache

from common.core.config import SysConfig, UserConfig, batch_user_config_values
from identity.models import UserInfo
from system.models import SystemConfig, UserPersonalConfig

pytestmark = pytest.mark.django_db

KEY = "BATCH_READ_PROBE"


@pytest.fixture(autouse=True)
def _clear_config_cache():
    """真实 Redis 跨用例存活，逐用例清缓存防槽位/标记串扰。"""
    django_cache.clear()
    yield
    django_cache.clear()


@pytest.fixture
def user():
    return UserInfo.objects.create_user(username="batchcfg-user", password="Test@123456")


class TestSysConfigGetValues:
    def test_mixed_hit_inactive_and_absent_match_single_reads(self):
        SystemConfig.objects.create(key=f"{KEY}_HIT", value={"n": 1}, inherit=True, is_active=True)
        SystemConfig.objects.create(key=f"{KEY}_OFF", value=2, inherit=True, is_active=False)
        keys = [f"{KEY}_HIT", f"{KEY}_OFF", f"{KEY}_MISS"]

        result = SysConfig.get_values(keys)

        assert result == {key: SysConfig.get_value(key) for key in keys}
        assert result[f"{KEY}_HIT"] == {"n": 1}
        assert result[f"{KEY}_OFF"] == {}
        assert result[f"{KEY}_MISS"] == {}

    def test_absent_key_uses_caller_default(self):
        assert SysConfig.get_values([f"{KEY}_MISS"], 42) == {f"{KEY}_MISS": 42}

    def test_uses_cached_value_when_present(self):
        SystemConfig.objects.create(key=f"{KEY}_HIT", value=5, inherit=True, is_active=True)
        assert SysConfig.get_value(f"{KEY}_HIT") == 5  # 预热缓存槽
        SystemConfig.objects.filter(key=f"{KEY}_HIT").update(value=6)  # 绕过信号直改行

        # 批量与单读同口径：缓存槽未失效时都读槽内值
        assert SysConfig.get_values([f"{KEY}_HIT"]) == {f"{KEY}_HIT": 5}

    def test_db_fallback_for_cold_keys(self):
        SystemConfig.objects.create(key=f"{KEY}_DB", value=9, inherit=True, is_active=True)
        assert SysConfig.get_values([f"{KEY}_DB"]) == {f"{KEY}_DB": 9}

    def test_access_gated_row_hidden_when_ignore_access_off(self):
        SystemConfig.objects.create(key=f"{KEY}_GATE", value=1, is_active=True, access=False)

        assert SysConfig.get_values([f"{KEY}_GATE"]) == {f"{KEY}_GATE": 1}
        assert SysConfig.get_values([f"{KEY}_GATE"], None, ignore_access=False) == {f"{KEY}_GATE": {}}


class TestBatchUserConfigValues:
    def test_personal_row_and_system_inherit_match_single_reads(self, user):
        other = UserInfo.objects.create_user(username="batchcfg-other", password="Test@123456")
        SystemConfig.objects.create(key=KEY, value=7, inherit=True, is_active=True)
        UserConfig(user.pk).set_value(KEY, 100, is_active=True, access=True)

        result = batch_user_config_values([(user.pk, KEY), (other.pk, KEY)])

        # 有个人行读个人值；无个人行继承系统生效值；均与逐对读取一致
        assert result == {
            (user.pk, KEY): UserConfig(user.pk).get_value(KEY),
            (other.pk, KEY): UserConfig(other.pk).get_value(KEY),
        }
        assert result[(user.pk, KEY)] == 100
        assert result[(other.pk, KEY)] == 7

    def test_multiple_keys_match_single_reads(self, user):
        other_key = f"{KEY}_ALT"
        SystemConfig.objects.create(key=other_key, value="sys", inherit=True, is_active=True)
        UserConfig(user.pk).set_value(KEY, 1, is_active=True, access=True)

        pairs = [(user.pk, KEY), (user.pk, other_key)]
        result = batch_user_config_values(pairs)
        assert result == {pair: UserConfig(user.pk).get_value(pair[1]) for pair in pairs}

    def test_inactive_personal_row_falls_back_to_system(self, user):
        SystemConfig.objects.create(key=KEY, value=7, inherit=True, is_active=True)
        UserPersonalConfig.objects.create(owner=user, key=KEY, value=999, is_active=False)

        assert batch_user_config_values([(user.pk, KEY)]) == {(user.pk, KEY): 7}
        assert UserConfig(user.pk).get_value(KEY) == 7

    def test_absent_everywhere_returns_empty(self, user):
        assert SystemConfig.objects.filter(key=KEY).exists() is False

        assert batch_user_config_values([(user.pk, KEY)]) == {(user.pk, KEY): {}}
        assert UserConfig(user.pk).get_value(KEY) == {}

    def test_absence_marker_respected(self, user):
        UserConfig(user.pk).get_value(KEY)  # 预留 no_row 缺席标记
        assert batch_user_config_values([(user.pk, KEY)]) == {(user.pk, KEY): {}}

    def test_user_level_get_values_delegates_with_inherit(self, user):
        """UserConfig.get_values（单用户批量）与逐个 get_value 同口径。"""
        SystemConfig.objects.create(key=KEY, value=7, inherit=True, is_active=True)
        UserConfig(user.pk).set_value(KEY, 66, is_active=True, access=True)

        keys = [KEY, f"{KEY}_MISS"]
        result = UserConfig(user.pk).get_values(keys)
        assert result == {key: UserConfig(user.pk).get_value(key, None) for key in keys}
        assert result[KEY] == 66  # 个人行优先
        assert result[f"{KEY}_MISS"] == {}  # 个人行与系统行皆缺席
