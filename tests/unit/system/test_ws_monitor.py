# -*- coding: utf-8 -*-
"""监控 WS 实时推送：权限同口径 + panel 载荷结构。"""

from unittest import mock

import pytest
from asgiref.sync import async_to_sync

pytestmark = pytest.mark.django_db


def test_monitor_ws_permission_matrix(normal_user, superuser):
    """超管放行；匿名拒绝；普通用户无 SystemMonitor 菜单权限时 fail-closed。"""
    from system.ws_monitor import _has_monitor_permission

    assert async_to_sync(_has_monitor_permission)(superuser) is True
    assert async_to_sync(_has_monitor_permission)(None) is False
    assert async_to_sync(_has_monitor_permission)(normal_user) is False


def test_monitor_ws_permission_granted_via_menu(normal_user):
    """普通用户持有 SystemMonitor 的 list 权限菜单（GET overview）即可订阅，与 HTTP 同口径。"""
    from system.ws_monitor import MONITOR_PERMISSION_KEY, _has_monitor_permission

    with mock.patch(
        "common.core.permission.get_user_permission",
        return_value={MONITOR_PERMISSION_KEY: (1, [])},
    ):
        assert async_to_sync(_has_monitor_permission)(normal_user) is True


def test_collect_panel_payload_shape():
    from system.ws_monitor import _collect_panel

    # 测试环境无 celery worker：与 HTTP 测试同款，patch 掉 celery 探测
    with mock.patch("system.utils.metrics.probe_celery", return_value=(True, 0.01)):
        panel = async_to_sync(_collect_panel)()
    assert panel["section"] == "panel"
    assert panel["services"]["status"] is True
    assert "redis" in panel["redis"]
    # 测试环境 HEALTH_CHECK_SKIP_CELERY：celery 载荷为跳过结构
    assert panel["celery"].get("skipped") is True or panel["celery"]["total"] >= 0
    assert "threshold" in panel["slow"]
    assert isinstance(panel["trend"], list)


def test_collect_live_metrics_shape():
    from system.utils.metrics import collect_live_metrics

    live = collect_live_metrics()
    assert live is not None
    assert {"cpu_percent", "memory_used", "disk_used", "swap_percent", "net_sent_mb"} <= set(live.keys())
