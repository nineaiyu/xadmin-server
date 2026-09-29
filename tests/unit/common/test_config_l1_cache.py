# -*- coding: utf-8 -*-
"""系统配置进程内 L1 缓存（P1-4）。

固定开销链上标量配置（CSP_MODE / CSP_REPORT_URI / SLOW_REQUEST_THRESHOLD）每请求读
3+ 次，每次都是 Redis 往返。L1 承担 30s 内的重复读；失效口径必须与 Redis 同款
（写路径信号 → invalid_config_cache → 同步清 L1），访问控制仍在 L1 之外判定。
"""

import pytest

from common.core.config import SysConfig
from system.models import SystemConfig

pytestmark = pytest.mark.django_db

KEY = "L1_PROBE"


def _spy_redis_reads(monkeypatch):
    """记录配置读缓存层真实发生的 Redis 取数（不经 L1 的那些）。"""
    from common.cache.storage import UserSystemConfigCache

    calls = []
    original = UserSystemConfigCache.get_storage_cache

    def spy(self, defaults=None):
        calls.append(self.cache_key)
        return original(self, defaults)

    monkeypatch.setattr(UserSystemConfigCache, "get_storage_cache", spy)
    return calls


def test_second_read_served_from_l1(monkeypatch):
    SystemConfig.objects.create(key=KEY, value=7, inherit=True, is_active=True)
    calls = _spy_redis_reads(monkeypatch)

    assert SysConfig.get_value(KEY) == 7
    assert SysConfig.get_value(KEY) == 7

    # 两次读只有一次落到 Redis（第二次走进程内 L1）
    assert len(calls) == 1


def test_invalidate_clears_l1_synchronously(monkeypatch):
    row = SystemConfig.objects.create(key=KEY, value=1, inherit=True, is_active=True)
    assert SysConfig.get_value(KEY) == 1  # 填 L1

    SystemConfig.objects.filter(pk=row.pk).update(value=2)
    SysConfig.invalid_config_cache(KEY)

    calls = _spy_redis_reads(monkeypatch)
    assert SysConfig.get_value(KEY) == 2
    # 失效后必须重新经 Redis 取数（未被 L1 旧值挡住）
    assert len(calls) == 1


def test_row_signal_clears_l1(monkeypatch):
    """绕过 invalid_config_cache 直接改行：post_save 信号同样清 L1。"""
    row = SystemConfig.objects.create(key=KEY, value=1, inherit=True, is_active=True)
    assert SysConfig.get_value(KEY) == 1

    row.value = 3
    row.save()

    calls = _spy_redis_reads(monkeypatch)
    assert SysConfig.get_value(KEY) == 3
    assert len(calls) == 1


def test_absence_marker_cached_in_l1(monkeypatch):
    """无行键：no_row 标记进 L1，避免每次读都穿透到 Redis/DB。"""
    calls = _spy_redis_reads(monkeypatch)

    assert SysConfig.get_value("L1_ABSENT", 42) == 42
    assert SysConfig.get_value("L1_ABSENT", 42) == 42

    assert len(calls) == 1


def test_l1_isolated_per_key(monkeypatch):
    SystemConfig.objects.create(key=f"{KEY}_A", value=1, inherit=True, is_active=True)
    SystemConfig.objects.create(key=f"{KEY}_B", value=2, inherit=True, is_active=True)

    assert SysConfig.get_value(f"{KEY}_A") == 1
    assert SysConfig.get_value(f"{KEY}_B") == 2
    # 改 A 不影响 B 的缓存值
    SystemConfig.objects.filter(key=f"{KEY}_A").update(value=11)
    SysConfig.invalid_config_cache(f"{KEY}_A")
    assert SysConfig.get_value(f"{KEY}_A") == 11
    assert SysConfig.get_value(f"{KEY}_B") == 2


def test_wildcard_invalidate_clears_prefix(monkeypatch):
    SystemConfig.objects.create(key=f"{KEY}_A", value=1, inherit=True, is_active=True)
    SystemConfig.objects.create(key=f"{KEY}_B", value=2, inherit=True, is_active=True)
    assert SysConfig.get_value(f"{KEY}_A") == 1
    assert SysConfig.get_value(f"{KEY}_B") == 2

    SysConfig.invalid_config_cache(f"{KEY}_*")
    SystemConfig.objects.filter(key=f"{KEY}_A").update(value=10)

    calls = _spy_redis_reads(monkeypatch)
    assert SysConfig.get_value(f"{KEY}_A") == 10
    assert len(calls) == 1


def test_l1_bounded_by_capacity(monkeypatch):
    """容量兜底：超过上限整体清空（用户级键理论上无界）。"""
    from common.core.config.base import ConfigCacheBase

    for index in range(ConfigCacheBase.L1_MAX_ENTRIES + 5):
        ConfigCacheBase._l1_set(f"probe_{index}", {"value": index})

    assert len(ConfigCacheBase._L1_STORE) <= ConfigCacheBase.L1_MAX_ENTRIES
    # 超限触发整体淘汰：最早的条目已不在 L1（防止无界增长）
    assert "probe_0" not in ConfigCacheBase._L1_STORE


def test_l1_returns_isolated_objects(monkeypatch):
    """L1 与 Redis 路径同语义：每次读拿到独立对象，调用方就地变更不污染缓存。"""
    SystemConfig.objects.create(key=KEY, value={"brand": "xadmin"}, inherit=True, is_active=True)

    first = SysConfig.get_value(KEY)
    assert first == {"brand": "xadmin"}
    first["brand"] = "mutated"

    assert SysConfig.get_value(KEY) == {"brand": "xadmin"}
