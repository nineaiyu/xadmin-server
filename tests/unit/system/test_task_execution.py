# -*- coding: utf-8 -*-
"""TaskExecution 执行记录：信号记账 + run action + log action 单元测试。"""

import json
import uuid
from datetime import timedelta
from unittest import mock

import pytest
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django_celery_beat.models import CrontabSchedule, PeriodicTask
from rest_framework.test import APIRequestFactory, force_authenticate

from common.celery.utils import CELERY_LOG_MAGIC_MARK, get_celery_task_log_path
from identity.models.user import UserInfo
from system import tasks as system_tasks
from task.models.task import TaskExecution
from task.serializers.task import CrontabScheduleSerializer, TaskExecutionSerializer
from task.services import cleanup as cleanup_impl
from task.signal_task_execution import (
    task_execution_on_finish,
    task_execution_on_publish,
    task_execution_on_revoked,
    task_execution_on_start,
)
from task.views.task import PeriodicTaskViewSet, TaskExecutionViewSet

pytestmark = pytest.mark.django_db


def _make_user():
    # run/log action 受按钮权限控制（非超管需菜单配置按钮），单测用超管聚焦业务逻辑
    return UserInfo.objects.create_superuser(username="taskrunner", password="x")


def _allow_runnable_tasks(monkeypatch, tasks):
    """放宽可手动执行白名单（默认拒绝）：run/batch-run 执行侧拦截的用例白名单。"""
    from common.core.config import SysConfig

    monkeypatch.setattr(type(SysConfig), "MANUAL_RUNNABLE_TASKS", property(lambda self: list(tasks)), raising=False)


def _make_periodic_task():
    crontab = CrontabSchedule.objects.create(minute="0", hour="4", day_of_week="*", day_of_month="*", month_of_year="*")
    return PeriodicTask.objects.create(
        name="test-periodic-job",
        task="system.tasks.auto_clean_operation_job",
        crontab=crontab,
        args=json.dumps([]),
        kwargs=json.dumps({}),
    )


def test_publish_creates_pending_execution():
    task_id = str(uuid.uuid4())
    task_execution_on_publish(
        headers={"id": task_id, "task": "common.tasks.foo", "periodic_task_name": "some-periodic"},
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
    task_execution_on_publish(headers={"id": str(execution.pk), "task": "x.tasks.y"}, body=([], {}))
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


def test_run_action_creates_execution_and_publishes(monkeypatch, django_capture_on_commit_callbacks):
    # 生产投递分支：eager 关闭 → on_commit send_task（settings_base 默认 eager，需按用例还原）
    _allow_runnable_tasks(monkeypatch, ["system.tasks.auto_clean_operation_job"])
    monkeypatch.setattr(settings, "CELERY_TASK_ALWAYS_EAGER", False)
    user = _make_user()
    instance = _make_periodic_task()
    factory = APIRequestFactory()
    request = factory.post(f"/api/task/periodic/{instance.pk}/run")
    force_authenticate(request, user=user)
    view = PeriodicTaskViewSet.as_view({"post": "run"})
    with mock.patch("task.views.task_periodic.app.send_task") as send_task:
        with django_capture_on_commit_callbacks(execute=True):
            response = view(request, pk=str(instance.pk))
    assert response.data["code"] == 1000
    execution = TaskExecution.objects.get(pk=response.data["data"]["task_id"])
    assert execution.creator == user
    assert execution.periodic_task == instance
    assert execution.status == TaskExecution.Status.PENDING
    send_task.assert_called_once()
    assert send_task.call_args.kwargs["task_id"] == str(execution.pk)


def test_run_action_eager_applies_synchronously(monkeypatch):
    """E2E/测试：eager 下 send_task 无效（AlwaysEagerIgnored），改走 apply 同步执行，
    执行记录应流转到 SUCCESS 且带耗时。"""
    _allow_runnable_tasks(monkeypatch, ["system.tasks.auto_clean_operation_job"])
    user = _make_user()
    instance = _make_periodic_task()
    factory = APIRequestFactory()
    request = factory.post(f"/api/task/periodic/{instance.pk}/run")
    force_authenticate(request, user=user)
    view = PeriodicTaskViewSet.as_view({"post": "run"})
    response = view(request, pk=str(instance.pk))
    assert response.data["code"] == 1000
    execution = TaskExecution.objects.get(pk=response.data["data"]["task_id"])
    assert execution.status == TaskExecution.Status.SUCCESS
    assert execution.date_start is not None
    assert execution.date_finished is not None
    assert execution.time_cost is not None


def test_run_action_rejects_unregistered_task():
    user = _make_user()
    instance = _make_periodic_task()
    PeriodicTask.objects.filter(pk=instance.pk).update(task="no.exist.task")
    instance.refresh_from_db()
    factory = APIRequestFactory()
    request = factory.post(f"/api/task/periodic/{instance.pk}/run")
    force_authenticate(request, user=user)
    view = PeriodicTaskViewSet.as_view({"post": "run"})
    response = view(request, pk=str(instance.pk))
    assert response.data["code"] != 1000
    assert TaskExecution.objects.filter(periodic_task=instance).exists() is False


def test_log_action_reads_file(monkeypatch, tmp_path):
    execution = TaskExecution.objects.create(name="x.tasks.log")
    log_file = tmp_path / f"{execution.pk}.log"
    log_file.write_bytes(b"hello\n" + CELERY_LOG_MAGIC_MARK)
    monkeypatch.setattr(settings, "CELERY_LOG_DIR", str(tmp_path))

    user = _make_user()
    factory = APIRequestFactory()
    request = factory.get(f"/api/task/executions/{execution.pk}/log")
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
    request = factory.get(f"/api/task/executions/{execution.pk}/log")
    force_authenticate(request, user=user)
    view = TaskExecutionViewSet.as_view({"get": "log"})
    response = view(request, pk=str(execution.pk))
    assert response.data["data"]["content"] == ""
    assert response.data["data"]["finished"] is False


def test_execution_serializer_related_fields_display():
    """执行历史关联字段序列化为 {pk,label}：列表页直接可读，不展示裸数字主键。"""
    user = _make_user()
    instance = _make_periodic_task()
    execution = TaskExecution.objects.create(name="x.tasks.display", periodic_task=instance, creator=user)
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
    request = factory.get("/api/task/periodic/registered")
    force_authenticate(request, user=user)
    view = PeriodicTaskViewSet.as_view({"get": "registered"})
    response = view(request)
    names = [item["name"] for item in response.data["data"]]
    assert "system.tasks.auto_clean_operation_job" in names
    assert not any(name.startswith("celery.") for name in names)
    item = next(i for i in response.data["data"] if i["name"] == "system.tasks.auto_clean_operation_job")
    assert isinstance(item["verbose_name"], str)


def test_ensure_tasks_registered_scans_once_per_process(monkeypatch):
    """全量 autodiscover 开销大：进程内只扫一次，后续调用直接复用已注册任务。"""
    from task.views import task_periodic

    monkeypatch.setattr(task_periodic, "_autodiscovered", False)
    with mock.patch.object(task_periodic.app, "autodiscover_tasks") as autodiscover:
        task_periodic.ensure_tasks_registered()
        task_periodic.ensure_tasks_registered()
        autodiscover.assert_called_once_with(force=True)


def test_ensure_tasks_registered_force_rescans(monkeypatch):
    from task.views import task_periodic

    monkeypatch.setattr(task_periodic, "_autodiscovered", True)
    with mock.patch.object(task_periodic.app, "autodiscover_tasks") as autodiscover:
        task_periodic.ensure_tasks_registered(force=True)
        autodiscover.assert_called_once_with(force=True)


def test_registered_action_reuses_scanned_tasks_by_default(monkeypatch):
    """缺省（不带 refresh）复用进程内已注册任务，不再重复全量扫描。"""
    from task.views import task_periodic

    monkeypatch.setattr(task_periodic, "_autodiscovered", True)
    user = _make_user()
    factory = APIRequestFactory()
    request = factory.get("/api/task/periodic/registered")
    force_authenticate(request, user=user)
    view = PeriodicTaskViewSet.as_view({"get": "registered"})
    with mock.patch.object(task_periodic.app, "autodiscover_tasks") as autodiscover:
        response = view(request)
    assert response.data["code"] == 1000
    autodiscover.assert_not_called()


def test_registered_action_refresh_forces_rescan(monkeypatch):
    """refresh=1 强制重新扫描任务模块（新装 app 后立即可见的逃生口）。"""
    from task.views import task_periodic

    monkeypatch.setattr(task_periodic, "_autodiscovered", True)
    user = _make_user()
    factory = APIRequestFactory()
    request = factory.get("/api/task/periodic/registered?refresh=1")
    force_authenticate(request, user=user)
    view = PeriodicTaskViewSet.as_view({"get": "registered"})
    with mock.patch.object(task_periodic.app, "autodiscover_tasks") as autodiscover:
        response = view(request)
    assert response.data["code"] == 1000
    autodiscover.assert_called_once_with(force=True)


def test_periodic_task_args_must_be_json_list():
    # payload 必须携带合法 crontab，否则模型 clean 的 schedule 缺失校验先行 500，
    # 测不到 args JSON 校验本身
    crontab = CrontabSchedule.objects.create(minute="0", hour="4", day_of_week="*", day_of_month="*", month_of_year="*")
    user = _make_user()
    factory = APIRequestFactory()
    request = factory.post(
        "/api/task/periodic",
        data={
            "name": "bad-args",
            "task": "system.tasks.auto_clean_operation_job",
            "crontab": crontab.pk,
            "args": "{not-json}",
            "kwargs": "{}",
        },
        format="json",
    )
    force_authenticate(request, user=user)
    view = PeriodicTaskViewSet.as_view({"post": "create"})
    response = view(request)
    assert response.data["code"] != 1000


def test_crontab_serializer_rejects_bad_expression():
    serializer = CrontabScheduleSerializer(
        data={"minute": "abc", "hour": "*", "day_of_week": "*", "day_of_month": "*", "month_of_year": "*"}
    )
    assert not serializer.is_valid()
    assert "minute" in serializer.errors


def test_auto_clean_task_execution():
    old = TaskExecution.objects.create(name="x.tasks.old")
    TaskExecution.objects.filter(pk=old.pk).update(created_time=timezone.now() - timedelta(days=40))
    TaskExecution.objects.create(name="x.tasks.new")
    with mock.patch.object(cleanup_impl.settings, "TASK_EXECUTION_KEEP_DAYS", 30):
        removed = system_tasks.auto_clean_task_execution_job.run()
    assert removed >= 1
    assert TaskExecution.objects.filter(name="x.tasks.old").exists() is False
    assert TaskExecution.objects.filter(name="x.tasks.new").exists() is True


def _make_log_consumer(execution_pk):
    """直构 TaskLogNotify，捕获 send_base_json 输出（参照心跳测试做法）。"""
    from asgiref.sync import async_to_sync

    from task.ws import TaskLogNotify

    consumer = TaskLogNotify()
    consumer.pk = str(execution_pk)
    consumer.offset = 0
    consumer.disconnected = False
    captured = []

    async def fake_send_base_json(action, data=None, mid=None, code=1000, detail=None, close=False, **kwargs):
        captured.append({"action": action, "data": data})

    consumer.send_base_json = fake_send_base_json
    return consumer, captured, async_to_sync


def test_ws_push_once_streams_until_mark(monkeypatch, tmp_path):
    consumer, captured, async_to_sync = _make_log_consumer("0" * 32)
    log_file = tmp_path / f"{'0' * 32}.log"
    log_file.write_bytes(b"hello\nworld\n" + CELERY_LOG_MAGIC_MARK)
    monkeypatch.setattr(settings, "CELERY_LOG_DIR", str(tmp_path))

    finished = async_to_sync(consumer.push_once)(get_celery_task_log_path(consumer.pk))
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
    TaskExecution.objects.filter(pk=execution.pk).update(date_finished=timezone.now())
    finished = async_to_sync(consumer.push_once)(path)
    assert finished is True


def test_batch_run_action_dispatches_selected(monkeypatch, django_capture_on_commit_callbacks):
    _allow_runnable_tasks(monkeypatch, ["system.tasks.auto_clean_operation_job"])
    monkeypatch.setattr(settings, "CELERY_TASK_ALWAYS_EAGER", False)
    user = _make_user()
    instance = _make_periodic_task()
    factory = APIRequestFactory()
    request = factory.post("/api/task/periodic/batch-run", data=[str(instance.pk)], format="json")
    force_authenticate(request, user=user)
    view = PeriodicTaskViewSet.as_view({"post": "batch_run"})
    with (
        mock.patch("task.views.task_periodic.app.send_task") as send_task,
        mock.patch("task.views.task_periodic.app.autodiscover_tasks"),
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
    request = factory.post("/api/task/periodic/batch-run", data=[str(instance.pk)], format="json")
    force_authenticate(request, user=user)
    view = PeriodicTaskViewSet.as_view({"post": "batch_run"})
    with mock.patch("task.views.task_periodic.app.autodiscover_tasks"):
        response = view(request)
    assert response.data["code"] == 1000
    assert response.data["data"]["success"] == 0
    assert len(response.data["data"]["failed"]) == 1
    assert "no.exist.task" in response.data["data"]["failed"][0]["detail"]


def test_destroy_execution_removes_log_file(monkeypatch, tmp_path):
    execution = TaskExecution.objects.create(name="x.tasks.del")
    log_file = tmp_path / f"{execution.pk}.log"
    log_file.write_bytes(b"content" + CELERY_LOG_MAGIC_MARK)
    monkeypatch.setattr(settings, "CELERY_LOG_DIR", str(tmp_path))

    user = _make_user()
    factory = APIRequestFactory()
    request = factory.delete(f"/api/task/executions/{execution.pk}")
    force_authenticate(request, user=user)
    view = TaskExecutionViewSet.as_view({"delete": "destroy"})
    response = view(request, pk=str(execution.pk))
    assert response.data["code"] == 1000
    assert not log_file.exists()
    assert TaskExecution.objects.filter(pk=execution.pk).exists() is False


def test_clean_orphan_periodic_tasks():
    from task.signal_task_execution import clean_orphan_periodic_tasks

    _make_periodic_task()  # 已注册任务：保留
    PeriodicTask.objects.create(
        name="orphan-periodic-job",
        task="dead.tasks.nope",
        crontab=CrontabSchedule.objects.create(
            minute="0", hour="4", day_of_week="*", day_of_month="*", month_of_year="*"
        ),
        args="[]",
        kwargs="{}",
    )

    class FakeApp:
        tasks = {"system.tasks.auto_clean_operation_job": object()}

    with (
        mock.patch("task.signal_task_execution.app", FakeApp()),
        mock.patch("task.signal_task_execution.cache") as cache_mock,
        mock.patch("task.signal_task_execution.PeriodicTasks.update_changed") as update_changed,
    ):
        # 模型 save/delete 已触发过 update_changed，这里只统计 handler 期间的调用
        update_changed.reset_mock()
        cache_mock.get.return_value = 0
        clean_orphan_periodic_tasks()

    assert PeriodicTask.objects.filter(name="orphan-periodic-job").exists() is False
    assert PeriodicTask.objects.filter(name="test-periodic-job").exists() is True
    update_changed.assert_called()


def test_clean_orphan_periodic_tasks_cache_guard():
    from task.signal_task_execution import clean_orphan_periodic_tasks

    orphan = PeriodicTask.objects.create(
        name="orphan-periodic-job-2",
        task="dead.tasks.nope",
        crontab=CrontabSchedule.objects.create(
            minute="0", hour="4", day_of_week="*", day_of_month="*", month_of_year="*"
        ),
        args="[]",
        kwargs="{}",
    )
    with (
        mock.patch("task.signal_task_execution.cache") as cache_mock,
        mock.patch("task.signal_task_execution.app.tasks", new={"x.tasks.alive": object()}),
    ):
        cache_mock.get.return_value = 1  # 其他 worker 已执行过清理，本次直接返回
        clean_orphan_periodic_tasks()

    assert PeriodicTask.objects.filter(pk=orphan.pk).exists() is True
    cache_mock.set.assert_not_called()


def test_ws_log_permission_owner_and_superuser(normal_user, superuser):
    """守护：WS 日志读取按归属判定——本人/超管可读，他人与未知 pk 拒绝。"""
    from task.ws import can_read_task_log

    execution = TaskExecution.objects.create(name="x.tasks.perm", creator=normal_user)
    assert can_read_task_log(normal_user, str(execution.pk)) is True
    assert can_read_task_log(superuser, str(execution.pk)) is True
    other = UserInfo.objects.create_user(username="ws-other", password="x")
    assert can_read_task_log(other, str(execution.pk)) is False
    assert can_read_task_log(other, "0" * 32) is False


def test_ws_log_permission_export_record_owner(normal_user, superuser):
    """守护：导出记录日志同口径（本人/超管可读），与 HTTP download 归属过滤一致。"""
    from task.models.export import ExportRecord
    from task.ws import can_read_task_log

    record = ExportRecord.objects.create(name="x", file_format="csv", creator=normal_user)
    assert can_read_task_log(normal_user, str(record.pk)) is True
    assert can_read_task_log(superuser, str(record.pk)) is True
    other = UserInfo.objects.create_user(username="ws-other-2", password="x")
    assert can_read_task_log(other, str(record.pk)) is False


def test_execution_list_exposes_product_info(superuser):
    """列表按 pk 带出产物信息：导出/导入任务与执行记录共用主键，一行即有类型/业务名/进度/重跑能力。"""
    from task.models.export import ExportRecord

    export = ExportRecord.objects.create(
        name="用户导出-20260924",
        module="用户",
        status=ExportRecord.Status.SUCCESS,
        progress=100,
        stage="渲染内容",
        creator=superuser,
    )
    TaskExecution.objects.create(
        pk=export.pk,
        name="dataset.analysis_tasks.run_export",
        creator=superuser,
        status=TaskExecution.Status.SUCCESS,
    )
    running = TaskExecution.objects.create(
        pk=str(uuid.uuid4()),
        name="common.tasks.foo",
        creator=superuser,
        status=TaskExecution.Status.RUNNING,
    )

    request = APIRequestFactory().get("/api/task/executions")
    force_authenticate(request, user=superuser)
    viewset = TaskExecutionViewSet()
    viewset.request = request
    viewset.action = "list"
    queryset = viewset.get_queryset()

    product_row = TaskExecutionSerializer(queryset.get(pk=export.pk)).data
    assert product_row["product_type"] == "export"
    assert product_row["product_name"] == "用户导出-20260924"
    assert product_row["product_progress"] == 100
    assert product_row["product_stage"] == "渲染内容"
    assert product_row["product_has_file"] is False
    assert product_row["can_cancel"] is False
    assert product_row["can_rerun"] is True

    plain_row = TaskExecutionSerializer(queryset.get(pk=running.pk)).data
    assert plain_row["product_type"] == ""
    assert plain_row["product_name"] == ""
    assert plain_row["can_cancel"] is True
    assert plain_row["can_rerun"] is False


def test_product_type_filter(superuser):
    """记录类型过滤（导出/导入/任务）与列表注解同源：按产物表同 pk 记录（相关 Exists）判定。"""
    from task.models.export import ExportRecord
    from task.models.import_ import ImportRecord
    from task.views.task import TaskExecutionFilter

    export = ExportRecord.objects.create(name="导出记录", creator=superuser)
    TaskExecution.objects.create(pk=export.pk, name="system.tasks.run_export", creator=superuser)
    imported = ImportRecord.objects.create(name="导入记录", creator=superuser)
    TaskExecution.objects.create(pk=imported.pk, name="system.tasks.run_import", creator=superuser)
    plain = TaskExecution.objects.create(name="common.tasks.foo", creator=superuser)

    queryset = TaskExecution.objects.all()
    export_rows = TaskExecutionFilter({"product_type": "export"}, queryset=queryset).qs
    assert {str(row.pk) for row in export_rows} == {str(export.pk)}
    import_rows = TaskExecutionFilter({"product_type": "import"}, queryset=queryset).qs
    assert {str(row.pk) for row in import_rows} == {str(imported.pk)}
    task_rows = TaskExecutionFilter({"product_type": "task"}, queryset=queryset).qs
    assert {str(row.pk) for row in task_rows} == {str(plain.pk)}


def test_product_has_file_matches_record_tables(superuser):
    """产物文件有无注解与记录表逐行判定语义一致：导出看 file、导入看错误报告。"""
    from file.models import UploadFile
    from task.models.export import ExportRecord
    from task.models.import_ import ImportRecord

    upload = UploadFile.objects.create(
        filename="result.xlsx",
        filesize=10,
        mime_type="application/octet-stream",
        md5sum="m" * 32,
        creator=superuser,
        is_tmp=True,
    )
    export_with_file = ExportRecord.objects.create(name="带产物导出", creator=superuser, file=upload)
    import_with_report = ImportRecord.objects.create(name="带错误报告导入", creator=superuser, error_report=upload)
    export_without_file = ExportRecord.objects.create(name="无产物导出", creator=superuser)
    for record in (export_with_file, import_with_report, export_without_file):
        TaskExecution.objects.create(pk=record.pk, name="system.tasks.run", creator=superuser)
    plain = TaskExecution.objects.create(name="common.tasks.foo", creator=superuser)

    request = APIRequestFactory().get("/api/task/executions")
    force_authenticate(request, user=superuser)
    viewset = TaskExecutionViewSet()
    viewset.request = request
    viewset.action = "list"
    queryset = viewset.get_queryset()

    assert queryset.get(pk=export_with_file.pk).product_has_file is True
    assert queryset.get(pk=import_with_report.pk).product_has_file is True
    assert queryset.get(pk=export_without_file.pk).product_has_file is False
    assert queryset.get(pk=plain.pk).product_has_file is False


def test_execution_detail_without_annotation_stays_safe(superuser):
    """详情动作不带产物注解：序列化仍可降级为空值，不得抛错。"""
    execution = TaskExecution.objects.create(name="common.tasks.foo", creator=superuser)

    request = APIRequestFactory().get(f"/api/task/executions/{execution.pk}")
    force_authenticate(request, user=superuser)
    viewset = TaskExecutionViewSet()
    viewset.request = request
    viewset.action = "retrieve"

    row = TaskExecutionSerializer(viewset.get_queryset().get(pk=execution.pk)).data
    assert row["product_type"] == "" and row["product_progress"] == 0
    # 默认 PENDING 属活跃态（可取消），非产物任务不可重跑
    assert row["can_cancel"] is True and row["can_rerun"] is False


# ---------------------------------------------------------------------------
# 定时任务一致性回归：interval 落库复用 / 克隆名后缀 / batch-enable 失败明细 /
# task 路径存在性校验
# ---------------------------------------------------------------------------


def _make_interval_crontab():
    return CrontabSchedule.objects.create(minute="0", hour="4", day_of_week="*", day_of_month="*", month_of_year="*")


def _make_named_periodic_task(name):
    return PeriodicTask.objects.create(
        name=name,
        task="system.tasks.auto_clean_operation_job",
        crontab=_make_interval_crontab(),
        args="[]",
        kwargs="{}",
    )


def test_interval_create_reuses_existing_schedule_row():
    """同 (every, period) 二次落库复用既有行：不产生重复的间隔调度。"""
    from django_celery_beat.models import IntervalSchedule

    from task.serializers.task import IntervalScheduleSerializer

    first = IntervalSchedule.objects.create(every=9, period="minutes")
    instance = IntervalScheduleSerializer().create({"every": 9, "period": "minutes"})
    assert instance.pk == first.pk
    assert IntervalSchedule.objects.filter(every=9, period="minutes").count() == 1


def test_interval_create_requeries_after_write_conflict():
    """并发兜底：查重通过后他人先落库且本次写入撞车（IntegrityError）时回查复用既有行。"""
    from django.db import IntegrityError
    from django_celery_beat.models import IntervalSchedule

    from task.serializers.task import IntervalScheduleSerializer

    existing = IntervalSchedule.objects.create(every=9, period="minutes")
    # get_or_create 内部调用查询集的 get/create，管理器实例上的 mock 拦截不到，
    # 须在查询集类上模拟「首次查询未命中 → 写入撞车 → 回查命中」的竞态序列
    queryset_class = type(IntervalSchedule.objects.none())
    with (
        mock.patch.object(queryset_class, "get", side_effect=[IntervalSchedule.DoesNotExist, existing]),
        mock.patch.object(queryset_class, "create", side_effect=IntegrityError),
    ):
        instance = IntervalScheduleSerializer().create({"every": 9, "period": "minutes"})
    assert instance.pk == existing.pk
    assert IntervalSchedule.objects.filter(every=9, period="minutes").count() == 1


def test_interval_serializer_still_rejects_duplicate_combo():
    """业务侧查重校验保留：重复组合在校验阶段即被拒（用户可读报错，不落到复用分支）。"""
    from django_celery_beat.models import IntervalSchedule

    from task.serializers.task import IntervalScheduleSerializer

    IntervalSchedule.objects.create(every=9, period="minutes")
    serializer = IntervalScheduleSerializer(data={"every": 9, "period": "minutes"})
    assert serializer.is_valid() is False


def test_clone_name_computes_next_suffix_with_single_query():
    """克隆名后缀一次查询推导：既有克隆名密集时返回第一个空档，且仅发一条查询。"""
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    from task.views.task_periodic import _next_available_clone_name

    crontab = _make_interval_crontab()
    for name in ("后缀任务-copy", "后缀任务-copy-2", "后缀任务-copy-4"):
        PeriodicTask.objects.create(name=name, task="system.tasks.auto_clean_operation_job", crontab=crontab)
    with CaptureQueriesContext(connection) as ctx:
        assert _next_available_clone_name("后缀任务") == "后缀任务-copy-3"
    assert len(ctx) == 1


def test_clone_name_ignores_unnumbered_and_gapless_from_two():
    """克隆名后缀规则：非数字后缀与「-copy-1」不占位，缺省名空闲时直接用首选名。"""
    from task.views.task_periodic import _next_available_clone_name

    crontab = _make_interval_crontab()
    # 手工改名的同前缀名（非 -{数字} 后缀）不算克隆占位
    PeriodicTask.objects.create(
        name="改名任务-copy-备份", task="system.tasks.auto_clean_operation_job", crontab=crontab
    )
    assert _next_available_clone_name("改名任务") == "改名任务-copy"
    # 「-copy-1」形态不阻塞首选名（与逐次探测口径一致）
    PeriodicTask.objects.create(name="手工任务-copy-1", task="system.tasks.auto_clean_operation_job", crontab=crontab)
    assert _next_available_clone_name("手工任务") == "手工任务-copy"
    # 无任何同前缀名 → 首选「{name}-copy」
    assert _next_available_clone_name("全新任务") == "全新任务-copy"


def test_clone_periodic_task_uses_computed_name():
    """克隆走一次查询的后缀推导：密集命名下得到第一个空档且默认停用。"""
    from task.views.task_periodic import _clone_periodic_task

    source = _make_named_periodic_task("克隆源任务")
    _make_named_periodic_task("克隆源任务-copy")
    clone = _clone_periodic_task(source)
    assert clone.name == "克隆源任务-copy-2"
    assert clone.enabled is False
    assert clone.task == source.task


def test_batch_enable_reports_unmatched_and_invalid_pks():
    """批量启停的失败明细如实返回：非法主键与未命中主键进 failed，不再恒为空列表。"""
    user = _make_user()
    instance = _make_periodic_task()
    bogus = str(uuid.uuid4())
    factory = APIRequestFactory()
    request = factory.post(
        "/api/task/periodic/batch-enable",
        data={"pks": [str(instance.pk), "abc", "999999", bogus], "enabled": True},
        format="json",
    )
    force_authenticate(request, user=user)
    view = PeriodicTaskViewSet.as_view({"post": "batch_enable"})
    response = view(request)
    assert response.data["code"] == 1000
    assert response.data["data"]["success"] == 1
    failed = response.data["data"]["failed"]
    assert {item["pk"] for item in failed} == {"abc", "999999", bogus}
    assert all(item["detail"] for item in failed)
    instance.refresh_from_db()
    assert instance.enabled is True


def test_batch_enable_reports_save_failure():
    """批量启停单项保存失败不影响其余项，失败明细带 pk/名称/原因。"""
    user = _make_user()
    ok_task = _make_named_periodic_task("批量启停-成功项")
    doomed = _make_named_periodic_task("批量启停-失败项")
    real_save = PeriodicTask.save

    def _flaky_save(self, *args, **kwargs):
        if self.pk == doomed.pk:
            raise OSError("boom")
        return real_save(self, *args, **kwargs)

    factory = APIRequestFactory()
    request = factory.post(
        "/api/task/periodic/batch-enable",
        data={"pks": [str(ok_task.pk), str(doomed.pk)], "enabled": False},
        format="json",
    )
    force_authenticate(request, user=user)
    view = PeriodicTaskViewSet.as_view({"post": "batch_enable"})
    with mock.patch.object(PeriodicTask, "save", _flaky_save):
        response = view(request)
    assert response.data["code"] == 1000
    assert response.data["data"]["success"] == 1
    failed = response.data["data"]["failed"]
    assert len(failed) == 1
    assert failed[0]["pk"] == str(doomed.pk)
    assert failed[0]["name"] == "批量启停-失败项"
    assert "boom" in failed[0]["detail"]
    ok_task.refresh_from_db()
    doomed.refresh_from_db()
    assert ok_task.enabled is False
    assert doomed.enabled is True  # 保存失败不改状态


def test_periodic_create_rejects_unregistered_task_path(monkeypatch):
    """task 路径未注册时创建即 400：保存期拦截，而非执行时才失败。"""
    _allow_runnable_tasks(monkeypatch, ["no.exist.task"])
    user = _make_user()
    crontab = _make_interval_crontab()
    factory = APIRequestFactory()
    request = factory.post(
        "/api/task/periodic",
        data={
            "name": "未注册路径任务",
            "task": "no.exist.task",
            "crontab": crontab.pk,
            "args": "[]",
            "kwargs": "{}",
        },
        format="json",
    )
    force_authenticate(request, user=user)
    view = PeriodicTaskViewSet.as_view({"post": "create"})
    # 直接调用视图时 ATOMIC_REQUESTS 会把 400 的回滚标记打到测试事务上，
    # 用 savepoint 隔离，断言才能在错误响应后继续查库
    with transaction.atomic():
        response = view(request)
    assert response.data["code"] != 1000
    assert PeriodicTask.objects.filter(name="未注册路径任务").exists() is False


def test_periodic_update_rejects_unregistered_task_path(monkeypatch):
    """更新同理：改为未注册路径被 400 拒绝，原 task 字段保持不变。"""
    _allow_runnable_tasks(monkeypatch, ["system.tasks.auto_clean_operation_job", "no.exist.task"])
    user = _make_user()
    instance = _make_periodic_task()
    factory = APIRequestFactory()
    request = factory.patch(f"/api/task/periodic/{instance.pk}", data={"task": "no.exist.task"}, format="json")
    force_authenticate(request, user=user)
    view = PeriodicTaskViewSet.as_view({"patch": "partial_update"})
    # 同上：错误响应的回滚标记用 savepoint 隔离，refresh_from_db 才能继续
    with transaction.atomic():
        response = view(request, pk=str(instance.pk))
    assert response.data["code"] != 1000
    instance.refresh_from_db()
    assert instance.task == "system.tasks.auto_clean_operation_job"


def test_periodic_create_accepts_registered_task_path(monkeypatch):
    """路径存在（已注册任务）时创建通过：存在性校验与白名单并列不冲突。"""
    _allow_runnable_tasks(monkeypatch, ["system.tasks.auto_clean_operation_job"])
    user = _make_user()
    crontab = _make_interval_crontab()
    factory = APIRequestFactory()
    request = factory.post(
        "/api/task/periodic",
        data={
            "name": "已注册路径任务",
            "task": "system.tasks.auto_clean_operation_job",
            "crontab": crontab.pk,
            "args": "[]",
            "kwargs": "{}",
        },
        format="json",
    )
    force_authenticate(request, user=user)
    view = PeriodicTaskViewSet.as_view({"post": "create"})
    response = view(request)
    assert response.data["code"] == 1000, response.data
    assert PeriodicTask.objects.filter(pk=response.data["data"]["pk"]).exists() is True
