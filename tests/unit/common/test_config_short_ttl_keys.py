# -*- coding: utf-8 -*-
"""凭据类配置键的短 TTL 自愈（告警回调令牌直改库行场景）。

背景：`BACKUP_ALERT_TOKEN` / `OPS_ALERT_TOKEN` / `SCIM_TOKEN` 的运维热修通常做法是
**直接改库行**（`UPDATE system_config ...`，绕过 post_save 信号 → 缓存不失效），
默认 30 天缓存会让改动长期不生效（端点上仍是旧令牌，判断为「令牌不匹配」）。
这里给这些键 60s 短 TTL：读路径最多 1 分钟回归新值，L1 同步收紧。
"""

import pytest

from common.core.config import SysConfig
from common.core.config.base import ConfigCacheBase
from system.models import SystemConfig

pytestmark = pytest.mark.django_db


def _spy_cache_timeouts(monkeypatch):
    """记录写入 Redis 配置缓存时使用的 timeout。"""
    from common.cache.storage import UserSystemConfigCache

    timeouts = []
    original = UserSystemConfigCache.set_storage_cache

    def spy(self, data, timeout=None):
        timeouts.append(timeout)
        return original(self, data, timeout)

    monkeypatch.setattr(UserSystemConfigCache, "set_storage_cache", spy)
    return timeouts


class TestShortTtlKeys:
    def test_token_keys_use_short_ttl(self):
        for key in ("BACKUP_ALERT_TOKEN", "OPS_ALERT_TOKEN", "SCIM_TOKEN"):
            assert SysConfig._ttl_for(key) == 60, key

    def test_other_keys_keep_default_ttl(self):
        assert SysConfig._ttl_for("CSP_MODE") == SysConfig.timeout == 60 * 60 * 24 * 30

    def test_token_read_writes_cache_with_short_timeout(self, monkeypatch):
        SystemConfig.objects.create(key="BACKUP_ALERT_TOKEN", value="tok-1", inherit=True, is_active=True)
        timeouts = _spy_cache_timeouts(monkeypatch)
        assert SysConfig.BACKUP_ALERT_TOKEN == "tok-1"
        assert timeouts and timeouts[-1] == 60

    def test_normal_key_keeps_long_timeout(self, monkeypatch):
        SystemConfig.objects.create(key="CSP_MODE", value="report-only", inherit=True, is_active=True)
        timeouts = _spy_cache_timeouts(monkeypatch)
        SysConfig.get_value("CSP_MODE")
        assert timeouts and timeouts[-1] == 60 * 60 * 24 * 30


class TestDirectDbEditSelfHeals:
    """直改库行（绕过信号）后在短 TTL 内必须能读到新值。"""

    def test_new_value_visible_after_cache_expiry(self, monkeypatch):
        row = SystemConfig.objects.create(key="BACKUP_ALERT_TOKEN", value="tok-old", inherit=True, is_active=True)
        assert SysConfig.BACKUP_ALERT_TOKEN == "tok-old"

        # 绕过信号直改库行：缓存仍是旧值（这是需要短 TTL 兜底的原因）
        SystemConfig.objects.filter(pk=row.pk).update(value="tok-new")
        assert SysConfig.BACKUP_ALERT_TOKEN == "tok-old"

        # 短 TTL 到期（等价于缓存键过期）后自动回归新值，无需重启或手工清缓存
        from common.cache.storage import UserSystemConfigCache

        UserSystemConfigCache("system_BACKUP_ALERT_TOKEN").del_many()
        ConfigCacheBase._L1_STORE.clear()
        assert SysConfig.BACKUP_ALERT_TOKEN == "tok-new"
