# -*- coding: utf-8 -*-
"""任务健康度聚合（collect_task_health）：状态归集 / 成功率 / 健康色三档 / 高频任务。"""

import uuid

import pytest
from django.utils import timezone

from system.models.task import TaskExecution
from system.utils.metrics import TASK_HEALTH_MIN_SAMPLE, collect_task_health

pytestmark = pytest.mark.django_db


def _make(name, status, created_offset_days=0):
    now = timezone.now()
    instance = TaskExecution.objects.create(
        id=uuid.uuid4(),
        name=name,
        status=status,
        date_start=now - timezone.timedelta(days=created_offset_days),
        date_finished=now - timezone.timedelta(days=created_offset_days),
    )
    if created_offset_days:
        # created_time 是 auto_now_add：显式传值会被忽略，用 update 回填模拟历史数据
        TaskExecution.objects.filter(pk=instance.pk).update(
            created_time=now - timezone.timedelta(days=created_offset_days)
        )
    return instance


class TestCollectTaskHealth:
    def test_empty_is_healthy(self):
        """无执行记录：空态恒 healthy，成功率为 None。"""
        data = collect_task_health()
        assert data["total"] == 0
        assert data["success_rate"] is None
        assert data["state"] == "healthy"

    def test_status_counts_and_rate(self):
        for _ in range(98):
            _make("t", TaskExecution.Status.SUCCESS)
        _make("t", TaskExecution.Status.FAILURE)
        _make("t", TaskExecution.Status.REVOKED)
        _make("t", TaskExecution.Status.RUNNING)
        _make("t", TaskExecution.Status.PENDING)

        data = collect_task_health()
        assert data["total"] == 102
        assert data["success"] == 98
        assert data["failure"] == 1
        assert data["revoked"] == 1
        assert data["running"] == 1
        assert data["pending"] == 1
        # 成功率按终态计算：98 / (98+1+1)
        assert data["success_rate"] == 0.98
        assert data["state"] == "degraded"

    def test_state_thresholds(self):
        # 高成功率 → healthy（达到最小样本）
        for _ in range(TASK_HEALTH_MIN_SAMPLE):
            _make("ok", TaskExecution.Status.SUCCESS)
        assert collect_task_health()["state"] == "healthy"

        # 样本不足 → 不误报（2 条全失败仍 healthy）
        TaskExecution.objects.all().delete()
        _make("few", TaskExecution.Status.FAILURE)
        _make("few", TaskExecution.Status.FAILURE)
        assert collect_task_health()["state"] == "healthy"

    def test_failing_state(self):
        for _ in range(10):
            _make("bad", TaskExecution.Status.FAILURE)
        _make("bad", TaskExecution.Status.SUCCESS)
        assert collect_task_health()["state"] == "failing"

    def test_window_filters_old_executions(self):
        _make("old", TaskExecution.Status.FAILURE, created_offset_days=3)
        _make("new", TaskExecution.Status.SUCCESS)
        data = collect_task_health(days=1)
        assert data["total"] == 1
        assert data["failure"] == 0

    def test_per_task_top_and_rate(self):
        for _ in range(9):
            _make("high", TaskExecution.Status.SUCCESS)
        _make("high", TaskExecution.Status.FAILURE)
        _make("low", TaskExecution.Status.SUCCESS)

        data = collect_task_health()
        top = {item["name"]: item for item in data["per_task"]}
        assert top["high"]["total"] == 10
        assert top["high"]["success_rate"] == 0.9
        assert top["low"]["success_rate"] == 1.0

    def test_recent_failures_listed(self):
        _make("ok-task", TaskExecution.Status.SUCCESS)
        _make("bad-task", TaskExecution.Status.FAILURE)
        data = collect_task_health()
        names = [item["name"] for item in data["recent_failures"]]
        assert names == ["bad-task"]
