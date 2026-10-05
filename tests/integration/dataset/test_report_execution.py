# -*- coding: utf-8 -*-
"""定时报表执行任务（run_scheduled_report）：产物落库与协作式取消协议。

执行链与异步导出共用 task 域统一导出服务（产物落 UploadFile + 进度里程碑 +
REVOKED 收敛），本文件守护两条链路的协议一致性。
"""

import pytest
from django.core import mail
from django.utils import timezone

from dataset.models.dataset import Dataset, Report
from task.models.export import ExportRecord
from task.models.task import TaskExecution
from task.utils.task_center import request_cancel

pytestmark = pytest.mark.django_db

ALL_COLUMNS = ["username", "gender", "is_active"]


@pytest.fixture
def model_registry(db):
    """数据集可用字段白名单：执行侧只认已登记的数据字段（与 test_analysis_api 同款）。"""
    from system.models import ModelLabelField

    root, _ = ModelLabelField.objects.get_or_create(
        name="identity.userinfo", defaults={"field_type": ModelLabelField.FieldChoices.DATA, "label": "用户"}
    )
    for name in ALL_COLUMNS:
        ModelLabelField.objects.get_or_create(
            name=name, parent=root, defaults={"field_type": ModelLabelField.FieldChoices.DATA, "label": name}
        )
    return root


@pytest.fixture
def dataset(model_registry):
    return Dataset.objects.create(name="执行数据集", bound_model="identity.userinfo", visibility="personal")


@pytest.fixture
def report(dataset, superuser):
    return Report.objects.create(
        name=f"执行报表-{timezone.now().timestamp()}",
        dataset=dataset,
        recipients=["ops@example.com"],
        is_active=True,
        creator=superuser,
    )


def _precreate_record(report):
    from dataset.analysis_tasks import _precreate_record

    return _precreate_record(report)


def _run(report):
    from dataset.analysis_tasks import run_scheduled_report

    record_pk = _precreate_record(report)
    run_scheduled_report.apply(kwargs={"report_id": str(report.pk)}, task_id=record_pk)
    return ExportRecord.objects.get(pk=record_pk)


def test_report_run_success_persists_artifact(report, superuser):
    """成功执行：产物经统一导出服务落 UploadFile（xlsx mime），记录转 SUCCESS 并投递邮件。"""
    record = _run(report)
    record.refresh_from_db()
    assert record.status == ExportRecord.Status.SUCCESS
    assert record.progress == 100
    assert record.rows is not None
    assert record.file_id is not None
    record.file.refresh_from_db()
    assert record.file.mime_type == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert record.file.is_tmp
    report.refresh_from_db()
    assert report.last_status == "SUCCESS"
    assert len(mail.outbox) == 1
    # 同 pk 执行历史行由 postrun 信号推进终态
    execution = TaskExecution.objects.get(pk=record.pk)
    assert execution.status == TaskExecution.Status.SUCCESS


def test_report_run_cancel_converges_revoked(report, superuser):
    """协作式取消：里程碑安全点命中取消标记 → 记录 REVOKED、执行历史同口径，不投递不告警。"""
    record_pk = _precreate_record(report)
    request_cancel(record_pk)
    from dataset.analysis_tasks import run_scheduled_report

    run_scheduled_report.apply(kwargs={"report_id": str(report.pk)}, task_id=record_pk)
    record = ExportRecord.objects.get(pk=record_pk)
    assert record.status == ExportRecord.Status.REVOKED
    assert record.error
    assert record.file_id is None
    # 取消不是故障：报表运行状态不翻转、邮件不投递
    report.refresh_from_db()
    assert report.last_status == ""
    assert len(mail.outbox) == 0
    execution = TaskExecution.objects.get(pk=record_pk)
    assert execution.status == "REVOKED"
