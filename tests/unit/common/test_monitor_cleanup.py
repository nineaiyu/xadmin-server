# -*- coding: utf-8 -*-
"""Monitor 心跳历史保留期清理：MONITOR_RETENTION_DAYS 配置化 + 分批删除。

第一期边界登记项：Monitor 30s 心跳长期落库无保留期配置（原硬编码 30 天）。
"""

import datetime

import pytest
from django.utils import timezone

from common.core.config import SysConfig
from common.models import Monitor
from common.tasks import auto_clean_monitor_logs

pytestmark = pytest.mark.django_db


def _make_monitor(days_ago=0):
    monitor = Monitor.objects.create(cpu_percent=1.0, memory_used=1.0, disk_used=1.0)
    if days_ago:
        Monitor.objects.filter(pk=monitor.pk).update(created_time=timezone.now() - datetime.timedelta(days=days_ago))
    return monitor


def test_monitor_cleanup_removes_expired_only(monkeypatch):
    recent = _make_monitor(0)
    old = _make_monitor(40)

    monkeypatch.setattr(type(SysConfig), "MONITOR_RETENTION_DAYS", property(lambda self: 30), raising=False)
    auto_clean_monitor_logs.run()

    assert Monitor.objects.filter(pk=recent.pk).exists()
    assert not Monitor.objects.filter(pk=old.pk).exists()


def test_monitor_cleanup_reads_retention_config(monkeypatch):
    """保留期走系统配置：改成 7 天后，8 天前的心跳被清理。"""
    old = _make_monitor(8)

    monkeypatch.setattr(type(SysConfig), "MONITOR_RETENTION_DAYS", property(lambda self: 7), raising=False)
    auto_clean_monitor_logs.run()

    assert not Monitor.objects.filter(pk=old.pk).exists()


def test_monitor_cleanup_zero_keeps_all(monkeypatch):
    """保留期 0 表示不清理（历史行全保留）。"""
    old = _make_monitor(400)

    monkeypatch.setattr(type(SysConfig), "MONITOR_RETENTION_DAYS", property(lambda self: 0), raising=False)
    auto_clean_monitor_logs.run()

    assert Monitor.objects.filter(pk=old.pk).exists()
