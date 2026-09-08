# -*- coding: utf-8 -*-
"""TaskExecution 执行记录：信号记账 + run action + log action 单元测试。"""
import json
import uuid
from datetime import timedelta
from unittest import mock

import pytest
from django.conf import settings
from django.utils import timezone
from django_celery_beat.models import CrontabSchedule, PeriodicTask
from rest_framework.test import APIRequestFactory, force_authenticate

from common.celery.utils import CELERY_LOG_MAGIC_MARK, get_celery_task_log_path
from system import tasks as system_tasks
from system.models.task import TaskExecution
from system.models.user import UserInfo
from system.serializers.task import CrontabScheduleSerializer, TaskExecutionSerializer
from system.signal_task_execution import (
    task_execution_on_finish,
    task_execution_on_publish,
    task_execution_on_revoked,
    task_execution_on_start,
)
from system.views.task import PeriodicTaskViewSet, TaskExecutionViewSet

pytestmark = pytest.mark.django_db


def _make_user():
    # run/log action 受按钮权限控制（非超管需菜单配置按钮），单测用超管聚焦业务逻辑
    return UserInfo.objects.create_superuser(username="taskrunner", password="x")


def _make_periodic_task():
    crontab = CrontabSchedule.objects.create(
        minute="0", hour="4", day_of_week="*", day_of_month="*", month_of_year="*"
    )
    return PeriodicTask.objects.create(
        name="test-periodic-job",
        task="system.tasks.auto_clean_operation_job",
        crontab=crontab, args=json.dumps([]), kwargs=json.dumps({}),
    )


def test_publish_creates_pending_execution():
    task_id = str(uuid.uuid4())
    task_execution_on_publish(
        headers={"id": task_id, "task": "common.tasks.foo",
                 "periodic_task_name": "some-periodic"},
        body=([1, 2], {"k": "v"}),
    )
    execution = TaskExecution.objects.get(pk=task_id)
    assert execution.status == TaskExecution.Status.PENDING
    assert execution.name == "common.tasks.foo"
    assert execution.args == [1, 2]
    assert execution.kwargs == {"k": "v"}
    # periodic_task_name 不存在时不阻塞记录
    assert execution.periodic_task is None


def test_publish_keeps_manual_creator():
    """手动执行场景：投递前已建记录（带 creator），publish 信号不得覆盖。"""
    user = _make_user()
    execution = TaskExecution.objects.create(name="x.tasks.y", creator=user)
    task_execution_on_publish(
        headers={"id": str(execution.pk), "task": "x.tasks.y"}, body=([], {})
    )
    execution.refresh_from_db()
    assert execution.creator == user


def test_start_and_finish_transition():
    execution = TaskExecution.objects.create(name="x.tasks.z")
    task_execution_on_start(task_id=str(execution.pk))
    execution.refresh_from_db()
    assert execution.status == TaskExecution.Status.RUNNING
    assert execution.date_start is not None

    task_execution_on_finish(task_id=str(execution.pk), state="SUCCESS")
    execution.refresh_from_db()
    assert execution.status == TaskExecution.Status.SUCCESS
    assert execution.date_finished is not None
    assert execution.time_cost is not None


def test_finish_failure_state():
    execution = TaskExecution.objects.create(name="x.tasks.f")
    task_execution_on_start(task_id=str(execution.pk))
    task_execution_on_finish(task_id=str(execution.pk), state="FAILURE")
    execution.refresh_from_db()
    assert execution.status == TaskExecution.Status.FAILURE


def test_revoked_transition():
    execution = TaskExecution.objects.create(name="x.tasks.r")

    class _Req:
        id = str(execution.pk)

    task_execution_on_revoked(request=_Req(), terminated=True, expired=False)
    execution.refresh_from_db()
    assert execution.status == TaskExecution.Status.REVOKED


def test_run_action_creates_execution_and_publishes(django_capture_on_commit_callbacks):
    user = _make_user()
    instance = _make_periodic_task()
    factory = APIRequestFactory()
    request = factory.post(f"/api/system/tasks/periodic/{instance.pk}/run")
    force_authenticate(request, user=user)
    view = PeriodicTaskViewSet.as_view({"post": "run"})
    with mock.patch("system.views.task.app.send_task") as send_task:
        with django_capture_on_commit_callbacks(execute=True):
            response = view(request, pk=str(instance.pk))
    assert response.data["code"] == 1000
    execution = TaskExecution.objects.get(pk=response.data["data"]["task_id"])
    assert execution.creator == user
    assert execution.periodic_task == instance
    assert execution.status == TaskExecution.Status.PENDING
    send_task.assert_called_once()
    assert send_task.call_args.kwargs["task_id"] == str(execution.pk)


def test_run_action_rejects_unregistered_task():
    user = _make_user()
    instance = _make_periodic_task()
    PeriodicTask.objects.filter(pk=instance.pk).update(task="no.exist.task")
    instance.refresh_from_db()
    factory = APIRequestFactory()
    request = factory.post(f"/api/system/tasks/periodic/{instance.pk}/run")
    force_authenticate(request, user=user)
    view = PeriodicTaskViewSet.as_view({"post": "run"})
    response = view(request, pk=str(instance.pk))
    assert response.data["code"] != 1000
    assert TaskExecution.objects.filter(periodic_task=instance).exists() is False


def test_log_action_reads_file(monkeypatch, tmp_path):
    execution = TaskExecution.objects.create(name="x.tasks.log")
    log_file = tmp_path / f"{execution.pk}.log"
    log_file.write_bytes("hello\n".encode() + CELERY_LOG_MAGIC_MARK)
    monkeypatch.setattr(settings, "CELERY_LOG_DIR", str(tmp_path))

    user = _make_user()
    factory = APIRequestFactory()
    request = factory.get(f"/api/system/tasks/executions/{execution.pk}/log")
    force_authenticate(request, user=user)
    view = TaskExecutionViewSet.as_view({"get": "log"})
    response = view(request, pk=str(execution.pk))
    assert response.data["data"]["finished"] is True
    assert response.data["data"]["content"] == "hello\n"
    assert response.data["data"]["offset"] == log_file.stat().st_size


def test_log_action_missing_file(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "CELERY_LOG_DIR", str(tmp_path))
    execution = TaskExecution.objects.create(name="x.tasks.nolog")
    user = _make_user()
    factory = APIRequestFactory()
    request = factory.get(f"/api/system/tasks/executions/{execution.pk}/log")
    force_authenticate(request, user=user)
    view = TaskExecutionViewSet.as_view({"get": "log"})
    response = view(request, pk=str(execution.pk))
    assert response.data["data"]["content"] == ""
    assert response.data["data"]["finished"] is False


def test_execution_serializer_related_fields_display():
    """执行历史关联字段序列化为 {pk,label}：列表页直接可读，不展示裸数字主键。"""
    user = _make_user()
    instance = _make_periodic_task()
    execution = TaskExecution.objects.create(
        name="x.tasks.display", periodic_task=instance, creator=user
    )
    data = TaskExecutionSerializer(execution).data
    assert data["periodic_task"] == {"pk": instance.pk, "label": "test-periodic-job"}
    assert data["creator"]["pk"] == user.pk
    assert user.username in data["creator"]["label"]

    # 定时调度场景：periodic_task 关联缺失 / creator 为空，均序列化为 None
    orphan = TaskExecution.objects.create(name="x.tasks.orphan")
    orphan_data = TaskExecutionSerializer(orphan).data
    assert orphan_data["periodic_task"] is None
    assert orphan_data["creator"] is None


def test_registered_action_lists_user_tasks():
    user = _make_user()
    factory = APIRequestFactory()
    request = factory.get("/api/system/tasks/periodic/registered")
    force_authenticate(request, user=user)
    view = PeriodicTaskViewSet.as_view({"get": "registered"})
    response = view(request)
    names = [item["name"] for item in response.data["data"]]
    assert "system.tasks.auto_clean_operation_job" in names
    assert not any(name.startswith("celery.") for name in names)
    item = next(i for i in response.data["data"] if i["name"] == "system.tasks.auto_clean_operation_job")
    assert isinstance(item["verbose_name"], str)


def test_periodic_task_args_must_be_json_list():
    # payload 必须携带合法 crontab，否则模型 clean 的 schedule 缺失校验先行 500，
    # 测不到 args JSON 校验本身
    crontab = CrontabSchedule.objects.create(
        minute="0", hour="4", day_of_week="*", day_of_month="*", month_of_year="*"
    )
    user = _make_user()
    factory = APIRequestFactory()
    request = factory.post(
        "/api/system/tasks/periodic",
        data={"name": "bad-args", "task": "system.tasks.auto_clean_operation_job",
              "crontab": crontab.pk, "args": "{not-json}", "kwargs": "{}"},
        format="json",
    )
    force_authenticate(request, user=user)
    view = PeriodicTaskViewSet.as_view({"post": "create"})
    response = view(request)
    assert response.data["code"] != 1000


def test_crontab_serializer_rejects_bad_expression():
    serializer = CrontabScheduleSerializer(
        data={"minute": "abc", "hour": "*", "day_of_week": "*",
              "day_of_month": "*", "month_of_year": "*"}
    )
    assert not serializer.is_valid()
    assert "minute" in serializer.errors


def test_auto_clean_task_execution():
    old = TaskExecution.objects.create(name="x.tasks.old")
    TaskExecution.objects.filter(pk=old.pk).update(
        created_time=timezone.now() - timedelta(days=40)
    )
    TaskExecution.objects.create(name="x.tasks.new")
    with mock.patch.object(system_tasks.settings, "TASK_EXECUTION_KEEP_DAYS", 30):
        removed = system_tasks.auto_clean_task_execution_job.run()
    assert removed >= 1
    assert TaskExecution.objects.filter(name="x.tasks.old").exists() is False
    assert TaskExecution.objects.filter(name="x.tasks.new").exists() is True


def _make_log_consumer(execution_pk):
    """直构 TaskLogNotify，捕获 send_base_json 输出（参照心跳测试做法）。"""
    from asgiref.sync import async_to_sync

    from system.ws import TaskLogNotify

    consumer = TaskLogNotify()
    consumer.pk = str(execution_pk)
    consumer.offset = 0
    consumer.disconnected = False
    captured = []

    async def fake_send_base_json(action, data=None, mid=None, code=1000,
                                  detail=None, close=False, **kwargs):
        captured.append({"action": action, "data": data})

    consumer.send_base_json = fake_send_base_json
    return consumer, captured, async_to_sync


def test_ws_push_once_streams_until_mark(monkeypatch, tmp_path):
    consumer, captured, async_to_sync = _make_log_consumer("0" * 32)
    log_file = tmp_path / f"{'0' * 32}.log"
    log_file.write_bytes("hello\nworld\n".encode() + CELERY_LOG_MAGIC_MARK)
    monkeypatch.setattr(settings, "CELERY_LOG_DIR", str(tmp_path))

    finished = async_to_sync(consumer.push_once)(
        get_celery_task_log_path(consumer.pk)
    )
    assert finished is True
    frame = captured[-1]
    assert frame["action"] == "task_log"
    assert frame["data"]["content"] == "hello\nworld\n"
    assert frame["data"]["finished"] is True
    assert frame["data"]["offset"] == log_file.stat().st_size


def test_ws_push_once_waits_when_file_missing(monkeypatch, tmp_path):
    execution = TaskExecution.objects.create(name="x.tasks.ws")
    consumer, captured, async_to_sync = _make_log_consumer(execution.pk)
    monkeypatch.setattr(settings, "CELERY_LOG_DIR", str(tmp_path))
    path = get_celery_task_log_path(consumer.pk)

    finished = async_to_sync(consumer.push_once)(path)
    assert finished is False
    assert captured[-1]["data"]["finished"] is False

    # 执行已结束但文件始终未落盘 → finished
    TaskExecution.objects.filter(pk=execution.pk).update(
        date_finished=timezone.now()
    )
    finished = async_to_sync(consumer.push_once)(path)
    assert finished is True


def test_batch_run_action_dispatches_selected(monkeypatch, django_capture_on_commit_callbacks):
    user = _make_user()
    instance = _make_periodic_task()
    factory = APIRequestFactory()
    request = factory.post(
        "/api/system/tasks/periodic/batch-run", data=[str(instance.pk)], format="json"
    )
    force_authenticate(request, user=user)
    view = PeriodicTaskViewSet.as_view({"post": "batch_run"})
    with mock.patch("system.views.task.app.send_task") as send_task, mock.patch(
            "system.views.task.app.autodiscover_tasks"
    ):
        with django_capture_on_commit_callbacks(execute=True):
            response = view(request)
    assert response.data["code"] == 1000
    assert response.data["data"]["success"] == 1
    send_task.assert_called_once()


def test_batch_run_action_reports_unregistered(monkeypatch):
    user = _make_user()
    instance = _make_periodic_task()
    PeriodicTask.objects.filter(pk=instance.pk).update(task="no.exist.task")
    instance.refresh_from_db()
    factory = APIRequestFactory()
    request = factory.post(
        "/api/system/tasks/periodic/batch-run", data=[str(instance.pk)], format="json"
    )
    force_authenticate(request, user=user)
    view = PeriodicTaskViewSet.as_view({"post": "batch_run"})
    with mock.patch("system.views.task.app.autodiscover_tasks"):
        response = view(request)
    assert response.data["code"] == 1000
    assert response.data["data"]["success"] == 0
    assert len(response.data["data"]["failed"]) == 1
    assert "no.exist.task" in response.data["data"]["failed"][0]["detail"]


def test_destroy_execution_removes_log_file(monkeypatch, tmp_path):
    execution = TaskExecution.objects.create(name="x.tasks.del")
    log_file = tmp_path / f"{execution.pk}.log"
    log_file.write_bytes("content".encode() + CELERY_LOG_MAGIC_MARK)
    monkeypatch.setattr(settings, "CELERY_LOG_DIR", str(tmp_path))

    user = _make_user()
    factory = APIRequestFactory()
    request = factory.delete(f"/api/system/tasks/executions/{execution.pk}")
    force_authenticate(request, user=user)
    view = TaskExecutionViewSet.as_view({"delete": "destroy"})
    response = view(request, pk=str(execution.pk))
    assert response.data["code"] == 1000
    assert not log_file.exists()
    assert TaskExecution.objects.filter(pk=execution.pk).exists() is False


def test_clean_orphan_periodic_tasks():
    from system.signal_task_execution import clean_orphan_periodic_tasks

    _make_periodic_task()  # 已注册任务：保留
    PeriodicTask.objects.create(
        name="orphan-periodic-job",
        task="dead.tasks.nope",
        crontab=CrontabSchedule.objects.create(
            minute="0", hour="4", day_of_week="*", day_of_month="*", month_of_year="*"
        ),
        args="[]", kwargs="{}",
    )

    class FakeApp:
        tasks = {"system.tasks.auto_clean_operation_job": object()}

    with (
        mock.patch("system.signal_task_execution.app", FakeApp()),
        mock.patch("system.signal_task_execution.cache") as cache_mock,
        mock.patch("system.signal_task_execution.PeriodicTasks.update_changed") as update_changed,
    ):
        # 模型 save/delete 已触发过 update_changed，这里只统计 handler 期间的调用
        update_changed.reset_mock()
        cache_mock.get.return_value = 0
        clean_orphan_periodic_tasks()

    assert PeriodicTask.objects.filter(name="orphan-periodic-job").exists() is False
    assert PeriodicTask.objects.filter(name="test-periodic-job").exists() is True
    update_changed.assert_called()


def test_clean_orphan_periodic_tasks_cache_guard():
    from system.signal_task_execution import clean_orphan_periodic_tasks

    orphan = PeriodicTask.objects.create(
        name="orphan-periodic-job-2",
        task="dead.tasks.nope",
        crontab=CrontabSchedule.objects.create(
            minute="0", hour="4", day_of_week="*", day_of_month="*", month_of_year="*"
        ),
        args="[]", kwargs="{}",
    )
    with (
        mock.patch("system.signal_task_execution.cache") as cache_mock,
        mock.patch("system.signal_task_execution.app.tasks", new={"x.tasks.alive": object()}),
    ):
        cache_mock.get.return_value = 1  # 其他 worker 已执行过清理，本次直接返回
        clean_orphan_periodic_tasks()

    assert PeriodicTask.objects.filter(pk=orphan.pk).exists() is True
    cache_mock.set.assert_not_called()
