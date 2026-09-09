# -*- coding: utf-8 -*-
"""异步导入 2.0：import-validate / import-async 提交 + 任务执行 + 错误报告 + 下载中心。"""

import datetime
import re
from unittest import mock

import pytest
from django.core.files.base import ContentFile
from django.utils import timezone

from common.celery.utils import CELERY_LOG_MAGIC_MARK, get_celery_task_log_path
from system.models.import_ import ImportRecord
from system.models.upload import UploadFile
from system.models.user import UserInfo
from system.tasks import async_import_data_task, auto_clean_import_record_job
from system.views.admin.dict import DataDictViewSet
from system.views.admin.import_ import ImportRecordViewSet

pytestmark = pytest.mark.django_db

DICT_VIEW_PATH = "system.views.admin.dict.DataDictViewSet"

# 数据字典 CSV：表头用字段名（解析链路 lowercase_fields_map 直接命中）。
# 非法行用超长 code（>64）制造——校验阶段对 DB 查重，纯校验/部分行场景不落库，
# 行间重复不会暴露，必须用字段级校验规则构造非法行
LONG_CODE = "imp-" + "x" * 100
CSV_VALID = b"code,label\nimp-color,Color\n"
CSV_MIXED = b"code,label\nimp-color,Color\n" + f"{LONG_CODE},Bad\n".encode()
CSV_ALL_BAD = b"code,label\n" + "".join(f"{LONG_CODE}{i},Bad{i}\n" for i in range(3)).encode()


def _post_import(viewset, url_path, user, csv_body, action="create", query=None):
    """与同步 import-data 同协议：文件原始 body + action 查询参数。"""
    from rest_framework.test import APIRequestFactory, force_authenticate

    factory = APIRequestFactory()
    request = factory.post(
        f"/api/system/dict/{url_path}?action={action}",
        csv_body,
        content_type="text/csv",
    )
    force_authenticate(request, user=user)
    view = viewset.as_view({"post": url_path.replace("-", "_")})
    return view(request) if not query else view(request, **query)


def test_import_validate_reports_errors_without_persist(superuser):
    """导入前校验：非法行返回字段级定位（row/field/message），且零落库。"""
    response = _post_import(DataDictViewSet, "import-validate", superuser, CSV_MIXED)
    assert response.data["code"] == 1000
    data = response.data["data"]
    assert data["total"] == 2
    assert data["valid_count"] == 1
    assert data["invalid_count"] == 1
    assert data["errors_truncated"] is False
    error = data["errors"][0]
    assert error["row"] == 2
    assert error["field"] == "code"
    assert "message" in error and error["message"]
    # 校验不落库
    from system.models.dict import DataDict

    assert not DataDict.objects.filter(code="imp-color").exists()
    assert not DataDict.objects.filter(code=LONG_CODE).exists()


def test_import_validate_truncates_error_details(superuser, monkeypatch):
    """错误明细超 IMPORT_VALIDATE_ERROR_LIMIT 截断并标记。"""
    from common.core.config import SysConfig

    monkeypatch.setattr(type(SysConfig), "IMPORT_VALIDATE_ERROR_LIMIT", property(lambda self: 1), raising=False)
    csv_body = b"code,label\n" + "".join(f"{LONG_CODE}{i},Bad{i}\n" for i in range(3)).encode()
    response = _post_import(DataDictViewSet, "import-validate", superuser, csv_body)
    data = response.data["data"]
    assert data["total"] == 3
    assert data["invalid_count"] == 3
    assert len(data["errors"]) == 1
    assert data["errors_truncated"] is True


def test_import_async_runs_task_when_eager(superuser):
    """EAGER 环境（测试/E2E）：同步执行任务，部分失败 → SUCCESS + 失败行报告落盘。"""
    response = _post_import(DataDictViewSet, "import-async", superuser, CSV_MIXED)
    assert response.data["code"] == 1000
    record = ImportRecord.objects.get(pk=response.data["data"]["record_id"])
    record.refresh_from_db()
    assert record.status == ImportRecord.Status.SUCCESS
    assert record.total == 2
    assert record.success_rows == 1
    assert record.failed_rows == 1
    # savepoint 隔离：非法行不影响合法行入库
    from system.models.dict import DataDict

    assert DataDict.objects.filter(code="imp-color", label="Color").exists()
    # 错误报告落 UploadFile(is_tmp) 且磁盘存在
    assert record.error_report_id is not None
    record.error_report.refresh_from_db()
    assert record.error_report.filesize > 0
    # 同 pk 的 TaskExecution 由任务内补建（eager 下 apply() 不发 after_task_publish）
    from system.models.task import TaskExecution

    execution = TaskExecution.objects.get(pk=record.pk)
    assert execution.name == "system.tasks.async_import_data_task"


def test_import_async_aborts_when_fail_rate_exceeded(superuser, monkeypatch):
    """失败率超 IMPORT_FAIL_RATE_LIMIT 中止：置 FAILURE 并回滚全部成功行。"""
    from common.core.config import SysConfig

    monkeypatch.setattr(type(SysConfig), "IMPORT_FAIL_RATE_LIMIT", property(lambda self: 0.3), raising=False)
    response = _post_import(DataDictViewSet, "import-async", superuser, CSV_ALL_BAD)
    record = ImportRecord.objects.get(pk=response.data["data"]["record_id"])
    record.refresh_from_db()
    assert record.status == ImportRecord.Status.FAILURE
    assert record.success_rows == 0
    assert record.error and "Aborted" in record.error
    assert record.error_report_id is not None
    from system.models.dict import DataDict

    assert not DataDict.objects.filter(code="imp-color").exists()


def test_import_task_failure_records_error(superuser):
    """源文件缺失：记录转 FAILURE 并留存错误，异常继续抛出（触发 task_failure 告警）。"""
    record = ImportRecord.objects.create(name="x", action="create", creator=superuser)
    with pytest.raises(Exception):
        from system.tasks import async_import_data_task

        async_import_data_task.apply(args=[str(record.pk), DICT_VIEW_PATH, superuser.pk], task_id=str(record.pk))
    record.refresh_from_db()
    assert record.status == ImportRecord.Status.FAILURE
    assert record.error


def test_import_async_concurrency_limit(superuser, monkeypatch):
    """同用户并发限流（IMPORT_ASYNC_MAX_RUNNING）：进行中任务达上限拒绝提交，跨用户隔离，终态释放。"""
    running = [ImportRecord.objects.create(creator=superuser, status=ImportRecord.Status.RUNNING) for _ in range(3)]
    other_user = UserInfo.objects.create_superuser(username="import-limit-other", password="x")

    response = _post_import(DataDictViewSet, "import-async", superuser, CSV_VALID)
    assert response.data["code"] != 1000  # 达上限被拒
    # 其他用户不受该用户额度影响；eager 分支阻断执行，记录停留 PENDING
    with mock.patch.object(async_import_data_task, "apply"):
        response = _post_import(DataDictViewSet, "import-async", other_user, CSV_VALID)
    assert response.data["code"] == 1000

    ImportRecord.objects.filter(pk__in=[r.pk for r in running]).update(status=ImportRecord.Status.SUCCESS)
    response = _post_import(DataDictViewSet, "import-async", superuser, CSV_VALID)
    assert response.data["code"] == 1000


def test_download_error_report_and_owner_scope(superuser, normal_user):
    """错误报告下载：本人 200 流式返回；他人 403/404。"""
    record = ImportRecord.objects.create(name="x", action="create", creator=superuser)
    upload = UploadFile(
        filename="x_errors.xlsx", filesize=5, mime_type="application/vnd.xlsx", is_tmp=True, creator=superuser
    )
    upload.filepath.save("x_errors.xlsx", ContentFile(b"error-report"), save=False)
    upload.save()
    record.error_report = upload
    record.save(update_fields=["error_report", "updated_time"])

    from rest_framework.test import APIRequestFactory, force_authenticate

    factory = APIRequestFactory()
    request = factory.get(f"/api/system/imports/{record.pk}/download")
    force_authenticate(request, user=superuser)
    response = ImportRecordViewSet.as_view({"get": "download"})(request, pk=str(record.pk))
    assert response.status_code == 200
    assert b"".join(response.streaming_content) == b"error-report"

    request = factory.get(f"/api/system/imports/{record.pk}/download")
    force_authenticate(request, user=normal_user)
    response = ImportRecordViewSet.as_view({"get": "download"})(request, pk=str(record.pk))
    assert response.status_code in (403, 404)


def test_import_log_action(superuser, monkeypatch, tmp_path):
    """增量日志读取：文件存在时返回内容 + finished；无文件时按记录终态。"""
    monkeypatch.setattr("django.conf.settings.CELERY_LOG_DIR", str(tmp_path))
    record = ImportRecord.objects.create(name="x", action="create", creator=superuser)
    log_path = get_celery_task_log_path(str(record.pk))
    with open(log_path, "wb") as fp:
        fp.write(b"importing\n" + CELERY_LOG_MAGIC_MARK)

    from rest_framework.test import APIRequestFactory, force_authenticate

    factory = APIRequestFactory()
    request = factory.get(f"/api/system/imports/{record.pk}/log")
    force_authenticate(request, user=superuser)
    response = ImportRecordViewSet.as_view({"get": "log"})(request, pk=str(record.pk))
    assert response.data["data"]["content"] == "importing\n"
    assert response.data["data"]["finished"] is True


def test_auto_clean_import_record_removes_files(superuser):
    """清理任务删除过期记录并连带删源文件与错误报告。"""
    record = ImportRecord.objects.create(name="x", action="create", creator=superuser)
    upload = UploadFile(filename="src.xlsx", filesize=5, mime_type="text/csv", is_tmp=True, creator=superuser)
    upload.filepath.save("src.xlsx", ContentFile(b"code,label\n"), save=False)
    upload.save()
    record.source_file = upload
    record.save(update_fields=["source_file", "updated_time"])
    file_path = upload.filepath.path
    ImportRecord.objects.filter(pk=record.pk).update(created_time=timezone.now() - datetime.timedelta(days=31))

    removed = auto_clean_import_record_job.run()
    assert removed == 1
    assert not ImportRecord.objects.filter(pk=record.pk).exists()
    assert not UploadFile.all_objects.filter(pk=upload.pk).exists()
    import os

    assert not os.path.exists(file_path)


def test_permission_fallback_covers_import_validate_and_async():
    """权限 fallback 正则覆盖 import-validate / import-async（未绑定模型时落 list 权限）。"""
    pattern = r"(?P<url>.*)/(export|import)-(data|async|validate)$"
    for url in (
        "/api/system/dict/import-validate",
        "/api/system/dict/import-async",
        "/api/system/dict/export-async",
    ):
        assert re.match(pattern, url), url
