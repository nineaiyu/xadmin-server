# -*- coding: utf-8 -*-
"""审计日志增强：检索扩展、分层留存、敏感操作告警。"""

import json
from datetime import timedelta

import pytest
from django.core.cache import cache
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from system.models.log import OperationLog
from system.models.user import UserInfo
from system.notifications import SensitiveOperationMessage, maybe_alert_sensitive_operation
from system.views.admin.operationlog import OperationLogViewSet

pytestmark = pytest.mark.django_db

LIST_URL = "/api/system/logs/operation"


def _make_user():
    return UserInfo.objects.create_superuser(username="auditadmin", password="x")


def _make_log(**kwargs):
    defaults = dict(
        module="用户管理",
        method="DELETE",
        path="/api/system/user/1",
        ipaddress="10.0.0.1",
        status_code=1000,
        exec_time=0.5,
        creator=None,
    )
    defaults.update(kwargs)
    return OperationLog.objects.create(**defaults)


def _list(user, params=None):
    factory = APIRequestFactory()
    request = factory.get(LIST_URL, params or {})
    force_authenticate(request, user=user)
    return OperationLogViewSet.as_view({"get": "list"})(request)


def test_filter_by_method(superuser):
    _make_log()
    _make_log(method="POST")
    results = _list(superuser, {"method": "POST"}).data["data"]["results"]
    assert len(results) == 1
    assert results[0]["method"] == "POST"


def test_filter_by_exec_time_range(superuser):
    _make_log(exec_time=0.5)
    _make_log(exec_time=2.5)
    results = _list(superuser, {"exec_time_min": 2}).data["data"]["results"]
    assert len(results) == 1
    assert results[0]["exec_time"] == 2.5
    results = _list(superuser, {"exec_time_max": 1}).data["data"]["results"]
    assert len(results) == 1
    assert results[0]["exec_time"] == 0.5


def test_filter_by_has_changes(superuser):
    _make_log()
    _make_log(changes=json.dumps({"name": {"old": "a", "new": "b"}}))
    results = _list(superuser, {"has_changes": "true"}).data["data"]["results"]
    assert len(results) == 1
    assert json.loads(results[0]["changes"])["name"]["new"] == "b"
    results = _list(superuser, {"has_changes": "false"}).data["data"]["results"]
    assert len(results) == 1
    assert results[0]["changes"] in (None, "")


def test_filter_by_module_icontains(superuser):
    _make_log(module="用户管理")
    _make_log(module="部门管理")
    results = _list(superuser, {"module": "用户"}).data["data"]["results"]
    assert len(results) == 1


def test_changes_exported_in_serializer(superuser):
    _make_log(changes=json.dumps({"name": {"old": "a", "new": "b"}}))
    response = _list(superuser)
    fields = response.data["data"]["results"][0].keys()
    assert "changes" in fields


def test_layered_retention_keeps_recent_errors(superuser):
    user = _make_user()
    ok_log = _make_log(status_code=1000, creator=user)
    error_log = _make_log(status_code=500, creator=user)
    # 成功日志 400 天（超全量保留期）；错误日志 250 天（超全量但在错误保留期内）
    OperationLog.objects.filter(pk=ok_log.pk).update(created_time=timezone.now() - timedelta(days=400))
    OperationLog.objects.filter(pk=error_log.pk).update(created_time=timezone.now() - timedelta(days=250))

    removed = OperationLog.remove_expired(clean_day=180)
    # 成功日志（>180 天）被删，错误日志按错误保留期（365 天）仍在
    assert removed == 1
    assert not OperationLog.objects.filter(pk=ok_log.pk).exists()
    assert OperationLog.objects.filter(pk=error_log.pk).exists()

    # 错误日志超过 365 天后同样删除
    OperationLog.objects.filter(pk=error_log.pk).update(created_time=timezone.now() - timedelta(days=400))
    removed = OperationLog.remove_expired(clean_day=180)
    assert removed == 1
    assert not OperationLog.objects.filter(pk=error_log.pk).exists()


def test_layered_retention_error_days_follows_all_when_zero(superuser, monkeypatch):
    """守护：OPERATION_LOG_ERROR_RETENTION_DAYS=0 时跟随全量保留期（分层关闭）。"""
    from common.core.config import SysConfig

    user = _make_user()
    error_log = _make_log(status_code=500, creator=user)
    OperationLog.objects.filter(pk=error_log.pk).update(created_time=timezone.now() - timedelta(days=250))
    original_get_value = SysConfig.get_value

    def fake_get_value(key, default=None):
        if key == "OPERATION_LOG_ERROR_RETENTION_DAYS":
            return 0
        return original_get_value(key, default)

    monkeypatch.setattr(SysConfig, "get_value", fake_get_value)
    removed = OperationLog.remove_expired(clean_day=180)
    # 无分层：250 天的错误日志跟随全量保留期（180 天）一并删除
    assert removed == 1
    assert not OperationLog.objects.filter(pk=error_log.pk).exists()


def test_alert_triggered_for_delete(monkeypatch):
    _make_log(method="DELETE", path="/api/system/user/1")
    info = {"method": "DELETE", "path": "/api/system/user/1", "module": "用户管理", "ipaddress": "127.0.0.1"}
    published = []
    monkeypatch.setattr(SensitiveOperationMessage, "publish", lambda self, *a, **kw: published.append(self.operation))
    cache.clear()
    maybe_alert_sensitive_operation(info)
    assert len(published) == 1
    assert published[0]["path"] == "/api/system/user/1"


def test_alert_throttled_and_method_filtered(monkeypatch):
    monkeypatch.setattr(SensitiveOperationMessage, "publish", lambda self, *a, **kw: published.append(1))
    published = []
    info = {"method": "GET", "path": "/api/system/user", "module": "用户管理"}
    cache.clear()
    # 默认方法清单 ["DELETE"]：GET 不告警
    maybe_alert_sensitive_operation(info)
    assert published == []
    # DELETE 命中后 60s 内同路径只告警一次
    info["method"] = "DELETE"
    maybe_alert_sensitive_operation(info)
    maybe_alert_sensitive_operation(info)
    assert len(published) == 1


def test_alert_path_regex_filter(monkeypatch):
    from common.core.config import SysConfig

    published = []
    monkeypatch.setattr(SensitiveOperationMessage, "publish", lambda self, *a, **kw: published.append(1))
    cache.clear()
    original_get_value = SysConfig.get_value

    def fake_get_value(key, default=None):
        if key == "SENSITIVE_OPERATION_PATHS":
            return [r"/api/system/role"]
        return original_get_value(key, default)

    # SysConfig 属性为只读 property，改为打桩 get_value（缓存/DB 读取的唯一入口）
    monkeypatch.setattr(SysConfig, "get_value", fake_get_value)
    maybe_alert_sensitive_operation({"method": "DELETE", "path": "/api/system/user/1"})
    assert published == []
    maybe_alert_sensitive_operation({"method": "DELETE", "path": "/api/system/role/1"})
    assert len(published) == 1
