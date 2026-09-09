# -*- coding: utf-8 -*-
"""系统监控面板：5 个只读接口的结构、缓存与慢请求阈值过滤。"""

from datetime import timedelta
from unittest import mock

import pytest
from django.utils import timezone

from common.models import Monitor
from system.models.log import OperationLog
from system.models.user import UserInfo
from system.views.monitor import MonitorViewSet

pytestmark = pytest.mark.django_db


def _call(action, user=None):
    from rest_framework.test import APIRequestFactory, force_authenticate

    factory = APIRequestFactory()
    request = factory.get(f"/api/system/monitor/{action}")
    if user:
        force_authenticate(request, user=user)
    # url_path 中的连字符对应方法名的下划线
    return MonitorViewSet.as_view({"get": action.replace("-", "_")})(request)


def test_monitor_requires_auth():
    response = _call("overview")
    assert response.status_code in (401, 403)


def test_overview_returns_live_latest_and_trend(superuser):
    Monitor.objects.create(cpu_percent=11.5, cpu_load=0.5, memory_used=40.0, disk_used=60.0)
    Monitor.objects.create(cpu_percent=22.5, cpu_load=1.5, memory_used=50.0, disk_used=70.0)
    response = _call("overview", superuser)
    assert response.data["code"] == 1000
    data = response.data["data"]
    assert data["latest"]["cpu_percent"] == 22.5
    assert len(data["trend"]) == 2
    # 趋势按时间升序返回，供前端直接画线
    assert data["trend"][0]["cpu_percent"] == 11.5
    # live 为 psutil 实时快照（卡片直读，不等 30s 心跳落盘）
    assert data["live"] is not None
    assert {"cpu_percent", "memory_used", "disk_used", "swap_percent", "process_count"} <= set(data["live"].keys())


def test_services_reports_all_components(superuser):
    with mock.patch("system.utils.metrics.probe_celery", return_value=(True, 0.01)):
        response = _call("services", superuser)
    data = response.data["data"]
    assert set(data.keys()) == {"db", "redis", "celery", "status"}
    assert data["db"]["status"] is True
    assert data["celery"]["status"] is True
    assert data["status"] is True


def test_redis_info_graceful_when_unavailable(superuser):
    with mock.patch("system.utils.metrics.get_redis_client", side_effect=RuntimeError("down")):
        response = _call("redis-info", superuser)
    data = response.data["data"]
    assert data["redis"]["status"] is False
    assert "down" in data["redis"]["error"]


def test_celery_skipped_when_configured(superuser, monkeypatch):
    from django.conf import settings

    monkeypatch.setattr(settings, "HEALTH_CHECK_SKIP_CELERY", True, raising=False)
    response = _call("celery", superuser)
    assert response.data["data"]["skipped"] is True


def test_slow_filters_by_threshold(superuser):
    user = UserInfo.objects.create_user(username="slowlog", password="x")
    for exec_time in (0.5, 2.0, 3.0):
        OperationLog.objects.create(module="测试", exec_time=exec_time, creator=user)
    # 默认阈值 1.0：0.5s 的不出现
    response = _call("slow", superuser)
    results = response.data["data"]["results"]
    assert response.data["data"]["threshold"] >= 1.0
    assert {row["exec_time"] for row in results} == {2.0, 3.0}
    # 按耗时降序
    assert results[0]["exec_time"] == 3.0


def test_slow_ignores_records_outside_window(superuser):
    user = UserInfo.objects.create_user(username="slowlog2", password="x")
    log = OperationLog.objects.create(module="测试", exec_time=5.0, creator=user)
    OperationLog.objects.filter(pk=log.pk).update(created_time=timezone.now() - timedelta(hours=48))
    response = _call("slow", superuser)
    assert response.data["data"]["results"] == []
