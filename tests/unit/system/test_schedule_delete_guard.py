# -*- coding: utf-8 -*-
"""调度（crontab / interval）删除引用预检。

django_celery_beat 的 ``PeriodicTask.crontab / interval`` 均为
``on_delete=CASCADE``：直接删除被引用的调度会**静默级联删除**周期任务。
修复后：单删被引用即 400 拒绝并列出受影响任务；批删走逐行分支，
被引用项进 failures、未引用项正常删除。
"""

import pytest
from django_celery_beat.models import CrontabSchedule, IntervalSchedule, PeriodicTask
from rest_framework.test import APIRequestFactory, force_authenticate

from system.views.task_periodic import CrontabScheduleViewSet, IntervalScheduleViewSet

pytestmark = pytest.mark.django_db


def _make_crontab(minute="0"):
    return CrontabSchedule.objects.create(minute=minute, hour="4", day_of_week="*", day_of_month="*", month_of_year="*")


def _make_interval(every=10):
    return IntervalSchedule.objects.create(every=every, period=IntervalSchedule.SECONDS)


def _make_periodic_task(name, task="system.tasks.auto_clean_operation_job", **schedule):
    return PeriodicTask.objects.create(name=name, task=task, **schedule)


def _destroy(viewset, user, instance):
    factory = APIRequestFactory()
    request = factory.delete(f"/api/system/placeholder/{instance.pk}")
    force_authenticate(request, user=user)
    return viewset.as_view({"delete": "destroy"})(request, pk=instance.pk)


def _batch_destroy(viewset, user, instances):
    factory = APIRequestFactory()
    request = factory.post("/api/system/placeholder/batch-destroy", [str(item.pk) for item in instances], format="json")
    force_authenticate(request, user=user)
    return viewset.as_view({"post": "batch_destroy"})(request)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "viewset,schedule", [(CrontabScheduleViewSet, "crontab"), (IntervalScheduleViewSet, "interval")]
)
class TestScheduleDeleteGuard:
    def test_destroy_referenced_schedule_rejected(self, superuser, viewset, schedule):
        """被周期任务引用的调度：单删 400 拒绝，任务名出现在响应中，任务不级联删除。

        真实事务档：DRF 异常处理器会 set_rollback 包裹事务，之后仍需查库断言
        「任务未被级联删除」（本用例的核心回归点）。
        """
        schedule_obj = _make_crontab() if schedule == "crontab" else _make_interval()
        periodic = _make_periodic_task("引用任务A", **{schedule: schedule_obj})
        response = _destroy(viewset, superuser, schedule_obj)
        assert response.status_code == 400
        assert "引用任务A" in str(response.data)
        assert PeriodicTask.objects.filter(pk=periodic.pk).exists()
        assert type(schedule_obj).objects.filter(pk=schedule_obj.pk).exists()

    def test_destroy_unreferenced_schedule_allowed(self, superuser, viewset, schedule):
        """无引用的调度：单删正常放行。"""
        schedule_obj = _make_crontab(minute="5") if schedule == "crontab" else _make_interval(every=30)
        response = _destroy(viewset, superuser, schedule_obj)
        assert response.status_code in (200, 204)
        assert type(schedule_obj).objects.filter(pk=schedule_obj.pk).exists() is False

    def test_batch_destroy_rowwise_with_reference(self, superuser, viewset, schedule):
        """混批：被引用项进 failures 且保留，未引用项删除（覆写 _needs_rowwise_delete 生效）。"""
        referenced = _make_crontab(minute="10") if schedule == "crontab" else _make_interval(every=40)
        free = _make_crontab(minute="20") if schedule == "crontab" else _make_interval(every=50)
        periodic = _make_periodic_task("混批引用任务", **{schedule: referenced})
        response = _batch_destroy(viewset, superuser, [referenced, free])
        assert response.data.get("code") == 1000
        data = response.data["data"]
        assert str(referenced.pk) in [item["pk"] for item in data["failures"]]
        assert str(free.pk) in data["success"]
        assert PeriodicTask.objects.filter(pk=periodic.pk).exists()
        assert type(referenced).objects.filter(pk=referenced.pk).exists()
        assert type(free).objects.filter(pk=free.pk).exists() is False
