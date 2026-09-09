# -*- coding: utf-8 -*-
"""异步导出：export-async 提交 + 任务执行 + 下载中心（下载/日志/清理）。"""

import datetime
from unittest import mock

import pytest
from django.conf import settings
from django.utils import timezone

from common.celery.utils import CELERY_LOG_MAGIC_MARK, get_celery_task_log_path
from system.models.export import ExportRecord
from system.models.upload import UploadFile
from system.models.user import UserInfo
from system.tasks import async_export_data_task, auto_clean_export_record_job
from system.views.admin.export import ExportRecordViewSet
from system.views.admin.user import UserViewSet

pytestmark = pytest.mark.django_db

USER_VIEW_PATH = "system.views.admin.user.UserViewSet"


def _post_export_async(user, params=None):
    from rest_framework.test import APIRequestFactory, force_authenticate

    factory = APIRequestFactory()
    request = factory.post("/api/system/user/export-async", params or {"type": "xlsx"}, format="json")
    force_authenticate(request, user=user)
    return UserViewSet.as_view({"post": "export_async"})(request)


def test_export_async_creates_record_and_dispatch(superuser, monkeypatch):
    """提交异步导出：落记录 + 返回 task_id（== 记录主键），非 EAGER 时走 apply_async 投递。"""
    # 测试环境默认 CELERY_TASK_ALWAYS_EAGER=True（同步执行分支），这里切回真实投递分支
    monkeypatch.setattr(settings, "CELERY_TASK_ALWAYS_EAGER", False)

    with mock.patch.object(async_export_data_task, "apply_async") as apply_async:
        # on_commit 回调在测试事务内不执行，改为立即执行以验证投递参数
        with mock.patch("common.core.modelset.import_export.transaction.on_commit", side_effect=lambda fn: fn()):
            response = _post_export_async(superuser)
    assert response.data["code"] == 1000
    record_id = response.data["data"]["record_id"]
    record = ExportRecord.objects.get(pk=record_id)
    assert response.data["data"]["task_id"] == record_id
    assert record.status == ExportRecord.Status.PENDING
    assert record.file_format == "xlsx"
    assert record.creator == superuser
    apply_async.assert_called_once()
    assert apply_async.call_args.kwargs["task_id"] == record_id


def test_export_async_runs_task_when_eager(superuser):
    """EAGER 环境（测试/E2E）：同步执行任务，产物落盘且记录转 SUCCESS。"""
    UserInfo.objects.create_user(username="async-export-1", password="x")
    response = _post_export_async(superuser)
    assert response.data["code"] == 1000
    record = ExportRecord.objects.get(pk=response.data["data"]["record_id"])
    record.refresh_from_db()
    assert record.status == ExportRecord.Status.SUCCESS
    assert record.rows >= 1
    assert record.progress == 100
    assert record.file_id is not None
    record.file.refresh_from_db()
    assert record.file.filesize > 0
    # 同 pk 的 TaskExecution 由任务内补建（eager 下 apply() 不触发 after_task_publish，
    # 真实投递环境则由信号 get_or_create 命中既有记录）——执行历史/增量日志双环境可用
    from system.models.task import TaskExecution

    execution = TaskExecution.objects.get(pk=record.pk)
    assert execution.name == "system.tasks.async_export_data_task"
    # 任务内补建的记录走 prerun/postrun 信号流转，终态一致
    assert execution.status == TaskExecution.Status.SUCCESS


def test_export_task_failure_records_error(superuser):
    """导出视图返回非 200 时：记录转 FAILURE 并留存错误，异常继续抛出（触发告警）。"""
    record = ExportRecord.objects.create(name="x", file_format="xlsx", params={"type": "xlsx"}, creator=superuser)
    with mock.patch.object(UserViewSet, "as_view", side_effect=ValueError("boom")):
        with pytest.raises(ValueError):
            async_export_data_task.apply(
                args=[str(record.pk), USER_VIEW_PATH, {"type": "xlsx"}, superuser.pk],
                task_id=str(record.pk),
            )
    record.refresh_from_db()
    assert record.status == ExportRecord.Status.FAILURE
    assert "boom" in record.error
    # 失败任务不伪造完成度（里程碑进度保留，绝不到 100）
    assert record.progress < 100


def test_download_action_streams_file(superuser, normal_user):
    record = ExportRecord.objects.create(name="x", file_format="csv", creator=superuser)
    upload = UploadFile(filename="x.csv", filesize=5, mime_type="text/csv", is_tmp=True, creator=superuser)
    upload.filepath.save("x.csv", _content_file(b"a,b,c\n"), save=False)
    upload.save()
    record.file = upload
    record.save(update_fields=["file", "updated_time"])

    from rest_framework.test import APIRequestFactory, force_authenticate

    factory = APIRequestFactory()
    request = factory.get(f"/api/system/exports/{record.pk}/download")
    force_authenticate(request, user=superuser)
    response = ExportRecordViewSet.as_view({"get": "download"})(request, pk=str(record.pk))
    assert response.status_code == 200
    assert b"".join(response.streaming_content) == b"a,b,c\n"
    assert "UTF-8''" in response["Content-Disposition"]

    # 普通用户不可下载他人导出：无按钮权限 403，有权限时由取值域兜底 404
    request = factory.get(f"/api/system/exports/{record.pk}/download")
    force_authenticate(request, user=normal_user)
    response = ExportRecordViewSet.as_view({"get": "download"})(request, pk=str(record.pk))
    assert response.status_code in (403, 404)


def test_download_action_missing_file(superuser):
    record = ExportRecord.objects.create(name="x", file_format="csv", creator=superuser)
    from rest_framework.test import APIRequestFactory, force_authenticate

    factory = APIRequestFactory()
    request = factory.get(f"/api/system/exports/{record.pk}/download")
    force_authenticate(request, user=superuser)
    response = ExportRecordViewSet.as_view({"get": "download"})(request, pk=str(record.pk))
    assert response.data["code"] != 1000


def test_log_action_reads_export_log(superuser, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "CELERY_LOG_DIR", str(tmp_path))
    record = ExportRecord.objects.create(name="x", file_format="csv", creator=superuser)
    log_path = get_celery_task_log_path(str(record.pk))
    with open(log_path, "wb") as fp:
        fp.write(b"exporting\n" + CELERY_LOG_MAGIC_MARK)

    from rest_framework.test import APIRequestFactory, force_authenticate

    factory = APIRequestFactory()
    request = factory.get(f"/api/system/exports/{record.pk}/log")
    force_authenticate(request, user=superuser)
    response = ExportRecordViewSet.as_view({"get": "log"})(request, pk=str(record.pk))
    assert response.data["data"]["content"] == "exporting\n"
    assert response.data["data"]["finished"] is True

    # 无日志文件时：finished 取记录终态
    record.status = ExportRecord.Status.SUCCESS
    record.save(update_fields=["status", "updated_time"])
    other = ExportRecord.objects.create(name="y", file_format="csv", creator=superuser)
    request = factory.get(f"/api/system/exports/{other.pk}/log")
    force_authenticate(request, user=superuser)
    response = ExportRecordViewSet.as_view({"get": "log"})(request, pk=str(other.pk))
    assert response.data["data"]["content"] == ""
    assert response.data["data"]["finished"] is False


def test_auto_clean_export_record_removes_file(superuser):
    record = ExportRecord.objects.create(name="x", file_format="csv", creator=superuser)
    upload = UploadFile(filename="x.csv", filesize=5, mime_type="text/csv", is_tmp=True, creator=superuser)
    upload.filepath.save("x.csv", _content_file(b"a,b,c\n"), save=False)
    upload.save()
    record.file = upload
    record.save(update_fields=["file", "updated_time"])
    file_path = upload.filepath.path
    ExportRecord.objects.filter(pk=record.pk).update(created_time=timezone.now() - datetime.timedelta(days=30))

    removed = auto_clean_export_record_job.run()
    assert removed == 1
    assert not ExportRecord.objects.filter(pk=record.pk).exists()
    assert not UploadFile.all_objects.filter(pk=upload.pk).exists()
    import os

    assert not os.path.exists(file_path)


def _content_file(data: bytes):
    from django.core.files.base import ContentFile

    return ContentFile(data)


def test_selected_export_fails_closed_when_spm_expired(superuser):
    """守护：spm 过期/非法时 selected 导出必须 fail-closed（400），
    绝不能静默放行为全量导出（get_spm_filter 历史行为）。"""
    from rest_framework.test import APIRequestFactory, force_authenticate

    factory = APIRequestFactory()
    request = factory.get("/api/system/user/export-data?type=xlsx&spm=expired-token")
    force_authenticate(request, user=superuser)
    response = UserViewSet.as_view({"get": "export_data"})(request)
    assert response.status_code == 400


def test_export_async_concurrency_limit(superuser, monkeypatch):
    """同用户并发限流（EXPORT_ASYNC_MAX_RUNNING，默认 3）：进行中任务达上限拒绝提交，
    终态记录不占额度；其他用户不受影响。"""
    monkeypatch.setattr(settings, "CELERY_TASK_ALWAYS_EAGER", False)
    running = [ExportRecord.objects.create(creator=superuser, status=ExportRecord.Status.RUNNING) for _ in range(3)]
    other_user = UserInfo.objects.create_superuser(username="export-limit-other", password="x")

    response = _post_export_async(superuser)
    assert response.data["code"] != 1000  # 达上限被拒
    # 其他用户不受该用户额度影响
    with (
        mock.patch.object(async_export_data_task, "apply_async"),
        mock.patch("common.core.modelset.import_export.transaction.on_commit", side_effect=lambda fn: fn()),
    ):
        response = _post_export_async(other_user)
    assert response.data["code"] == 1000

    # 任务全部结束后额度释放，可再次提交
    ExportRecord.objects.filter(pk__in=[r.pk for r in running]).update(status=ExportRecord.Status.SUCCESS)
    with (
        mock.patch.object(async_export_data_task, "apply_async"),
        mock.patch("common.core.modelset.import_export.transaction.on_commit", side_effect=lambda fn: fn()),
    ):
        response = _post_export_async(superuser)
    assert response.data["code"] == 1000


def test_export_async_limit_zero_disables_throttle(superuser, monkeypatch):
    """上限配置为 0 表示不限制。"""
    from common.core.config import SysConfig

    monkeypatch.setattr(settings, "CELERY_TASK_ALWAYS_EAGER", False)
    monkeypatch.setattr(type(SysConfig), "EXPORT_ASYNC_MAX_RUNNING", property(lambda self: 0), raising=False)
    for _ in range(5):
        ExportRecord.objects.create(creator=superuser, status=ExportRecord.Status.RUNNING)

    with (
        mock.patch.object(async_export_data_task, "apply_async"),
        mock.patch("common.core.modelset.import_export.transaction.on_commit", side_effect=lambda fn: fn()),
    ):
        response = _post_export_async(superuser)
    assert response.data["code"] == 1000
