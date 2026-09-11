# -*- coding: utf-8 -*-
"""F4 聚合抽屉：导出 / 导入 / 任务执行三类记录统计的统一口径与短缓存。

口径要点（聚合抽屉跨类并排展示，三类必须一致）：
- 取值域与列表同口径（超管全量、其余本人）；
- 窗口 = 近 N 天；进行中 = PENDING/RUNNING；失败 = FAILURE/REVOKED/FAILED；
- 统一返回 `{days, total, in_progress, failed, latest}`，latest 键名固定（模型字段名各异）。
"""

import datetime
import json

import pytest
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from system.models.export import ExportRecord
from system.models.import_ import ImportRecord
from system.models.task import TaskExecution
from system.utils.record_stats import RECORD_STATS_WINDOW_DAYS, record_stats
from system.views.admin.export import ExportRecordViewSet
from system.views.admin.import_ import ImportRecordViewSet
from system.views.task import TaskExecutionViewSet

pytestmark = pytest.mark.django_db

STATS_KEYS = {"days", "total", "in_progress", "failed", "latest"}


def _make(model, creator, name, status, days_ago=0):
    """建一条记录，必要时把 created_time 回溯到 N 天前（绕开 auto_now_add）。"""
    record = model.objects.create(name=name, creator=creator, status=status)
    if days_ago:
        when = timezone.now() - datetime.timedelta(days=days_ago)
        model.objects.filter(pk=record.pk).update(created_time=when)
    return record


def _stats(viewset, url, user, query=""):
    factory = APIRequestFactory()
    request = factory.get(f"{url}{query}")
    force_authenticate(request, user=user)
    return viewset.as_view({"get": "stats"})(request)


def stats_data(response) -> dict:
    """取响应 data：命中短缓存时返回的是已渲染的 HttpResponse（无 .data）。"""
    if hasattr(response, "data"):
        return response.data["data"]
    return json.loads(response.content.decode())["data"]


class TestScope:
    def test_superuser_sees_all(self, superuser, normal_user):
        _make(ExportRecord, superuser, "by-admin", "SUCCESS")
        _make(ExportRecord, normal_user, "by-user", "SUCCESS")
        assert record_stats(ExportRecord.objects.all(), superuser)["total"] == 2

    def test_normal_user_only_own(self, superuser, normal_user):
        """非超管只统计本人提交：与列表取值域（RecordOwnerFilter）同口径。"""
        _make(ExportRecord, superuser, "by-admin", "SUCCESS")
        _make(ExportRecord, normal_user, "by-user", "FAILURE")
        data = record_stats(ExportRecord.objects.all(), normal_user)
        assert data["total"] == 1
        assert data["failed"] == 1


class TestWindow:
    def test_outside_window_excluded(self, superuser):
        _make(ExportRecord, superuser, "fresh", "SUCCESS")
        _make(ExportRecord, superuser, "stale", "SUCCESS", days_ago=RECORD_STATS_WINDOW_DAYS + 10)
        data = record_stats(ExportRecord.objects.all(), superuser)
        assert data["total"] == 1
        assert data["latest"]["name"] == "fresh"

    def test_window_boundary_inclusive(self, superuser):
        """窗口内（含边界）计入：N 天整仍算近期。"""
        _make(ExportRecord, superuser, "edge", "SUCCESS", days_ago=RECORD_STATS_WINDOW_DAYS - 1)
        assert record_stats(ExportRecord.objects.all(), superuser)["total"] == 1


class TestStatusBuckets:
    def test_in_progress_buckets(self, superuser):
        _make(ExportRecord, superuser, "p", "PENDING")
        _make(ExportRecord, superuser, "r", "RUNNING")
        _make(ExportRecord, superuser, "s", "SUCCESS")
        data = record_stats(ExportRecord.objects.all(), superuser)
        assert data["in_progress"] == 2
        assert data["total"] == 3

    def test_revoked_counts_as_failed(self, superuser):
        """任务执行多一个 REVOKED 终态，聚合口径按失败计（否则抽屉里会漏计数）。"""
        _make(TaskExecution, superuser, "failed", "FAILURE")
        _make(TaskExecution, superuser, "revoked", "REVOKED")
        _make(TaskExecution, superuser, "success", "SUCCESS")
        data = record_stats(TaskExecution.objects.all(), superuser)
        assert data["failed"] == 2
        assert data["total"] == 3


class TestLatest:
    def test_latest_is_recent_and_normalized(self, superuser):
        _make(ExportRecord, superuser, "old", "SUCCESS", days_ago=5)
        recent = _make(ExportRecord, superuser, "new", "RUNNING")
        latest = record_stats(ExportRecord.objects.all(), superuser)["latest"]
        assert latest["pk"] == str(recent.pk)
        assert latest["name"] == "new"
        assert latest["status"] == "RUNNING"
        assert latest["created_time"]

    def test_latest_none_when_empty(self, superuser):
        assert record_stats(ImportRecord.objects.all(), superuser)["latest"] is None


class TestViewSetStats:
    @pytest.mark.parametrize(
        "viewset,url,model",
        [
            (ExportRecordViewSet, "/api/system/exports/stats", ExportRecord),
            (ImportRecordViewSet, "/api/system/imports/stats", ImportRecord),
            (TaskExecutionViewSet, "/api/system/tasks/executions/stats", TaskExecution),
        ],
    )
    def test_unified_payload(self, viewset, url, model, superuser):
        """三个 stats 接口返回结构一致（抽屉按同一套键渲染，不允许各自发挥）。"""
        _make(model, superuser, "r1", "RUNNING")
        _make(model, superuser, "r2", "FAILURE")
        response = _stats(viewset, url, superuser)
        assert response.status_code == 200
        data = stats_data(response)
        assert STATS_KEYS <= set(data)
        assert data["total"] == 2
        assert data["in_progress"] == 1
        assert data["failed"] == 1

    def test_short_cache_and_no_cache_bypass(self, superuser):
        """10s 短缓存：新增记录后仍返回旧值；`?no_cache=1` 旁路读与写。"""
        _make(ExportRecord, superuser, "first", "SUCCESS")
        first = _stats(ExportRecordViewSet, "/api/system/exports/stats", superuser)
        assert stats_data(first)["total"] == 1

        _make(ExportRecord, superuser, "second", "SUCCESS")
        cached = _stats(ExportRecordViewSet, "/api/system/exports/stats", superuser)
        assert stats_data(cached)["total"] == 1

        bypassed = _stats(ExportRecordViewSet, "/api/system/exports/stats", superuser, query="?no_cache=1")
        assert stats_data(bypassed)["total"] == 2
