# -*- coding: utf-8 -*-
"""任务中心后端单测：统一视图 + 协作式取消 + 白名单重跑。

覆盖：三类记录聚合与数据域（超管全量 / 普通用户仅本人）、过滤与分页、取消的
三种状态语义（PENDING 立即终态 / RUNNING 协作点 / 已终态幂等）、取消标记与
任务安全点检查、重跑的解析与派发（重放链路用 stub 记录，不真跑任务）。
"""

import pytest
from django.utils import timezone

from system.models.export import ExportRecord
from system.models.import_ import ImportRecord
from system.models.task import TaskExecution
from system.utils import task_center
from system.utils.task_center import (
    TaskCancelled,
    cancel_record,
    clear_cancel,
    ensure_not_cancelled,
    is_cancel_requested,
    request_cancel,
    rerun_record,
    resolve_view_path,
    unified_rows,
)

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _no_broker(monkeypatch):
    """revoke 打桩（不依赖 broker），并记录调用。"""
    calls = []
    from server.celery import app

    monkeypatch.setattr(app.control, "revoke", lambda task_id, terminate=False: calls.append(task_id))
    return calls


@pytest.fixture(autouse=True)
def _clean_records():
    ExportRecord.objects.all().delete()
    ImportRecord.objects.all().delete()
    TaskExecution.objects.all().delete()
    yield
    ExportRecord.objects.all().delete()
    ImportRecord.objects.all().delete()
    TaskExecution.objects.all().delete()


def _export(user, **kwargs):
    defaults = {
        "name": "用户导出",
        "module": "用户",
        "path": "/api/system/user/export-async",
        "params": {"type": "xlsx"},
    }
    return ExportRecord.objects.create(creator=user, **{**defaults, **kwargs})


class TestUnifiedRows:
    def test_owner_scope(self, superuser, normal_user):
        _export(superuser, name="超管导出")
        _export(normal_user, name="用户导出")
        TaskExecution.objects.create(pk="1" * 32, name="system.tasks.demo", creator=normal_user)

        rows, total = unified_rows(superuser)
        assert total == 3 and {row["type"] for row in rows} == {"task", "export"}
        scoped, scoped_total = unified_rows(normal_user)
        assert scoped_total == 2
        assert all(row["creator"] == normal_user.username for row in scoped)

    def test_filters_and_pagination(self, superuser):
        for index in range(3):
            _export(
                superuser,
                name=f"批量导出{index}",
                status=ExportRecord.Status.FAILURE if index else ExportRecord.Status.SUCCESS,
            )
        rows, total = unified_rows(superuser, keyword="批量导出")
        assert total == 3
        failed, failed_total = unified_rows(superuser, status="FAILURE")
        assert failed_total == 2 and all(row["status"] == "FAILURE" for row in failed)
        page, page_total = unified_rows(superuser, page=1, size=1)
        assert page_total == 3 and len(page) == 1
        assert unified_rows(superuser, types=["task"])[1] == 0

    def test_rows_expose_actions_and_flags(self, superuser):
        running = _export(superuser, name="运行中", status=ExportRecord.Status.RUNNING, progress=30)
        done = _export(superuser, name="已完成", status=ExportRecord.Status.SUCCESS, progress=100)
        rows = {row["name"]: row for row in unified_rows(superuser)[0]}
        assert rows["运行中"]["can_cancel"] is True and rows["运行中"]["can_rerun"] is False
        assert rows["运行中"]["progress"] == 30
        assert rows["已完成"]["can_cancel"] is False and rows["已完成"]["can_rerun"] is True
        assert rows["已完成"]["created_time"] and rows["已完成"]["finished_time"]
        assert str(running.pk) in {row["pk"] for row in rows.values()}
        assert str(done.pk) in {row["pk"] for row in rows.values()}

    def test_import_row_progress_and_counts(self, superuser):
        record = ImportRecord.objects.create(
            creator=superuser,
            name="用户导入",
            module="用户",
            path="/api/system/user/import-data",
            action=ImportRecord.Action.CREATE,
            status=ImportRecord.Status.SUCCESS,
            progress=100,
            total=10,
            success_rows=9,
            failed_rows=1,
        )
        row = unified_rows(superuser, types=["import"])[0][0]
        assert row["type"] == "import" and row["total"] == 10 and row["failed_rows"] == 1
        assert str(record.pk) == row["pk"]


class TestCancel:
    def test_pending_cancelled_immediately(self, superuser, _no_broker):
        record = _export(superuser, status=ExportRecord.Status.PENDING)
        result = cancel_record(superuser, "export", str(record.pk))
        record.refresh_from_db()
        assert result["ok"] is True and record.status == ExportRecord.Status.REVOKED
        assert _no_broker == [str(record.pk)]

    def test_running_requests_cooperative_stop(self, superuser, _no_broker):
        record = _export(superuser, status=ExportRecord.Status.RUNNING)
        result = cancel_record(superuser, "export", str(record.pk))
        record.refresh_from_db()
        assert result["ok"] is True
        assert record.status == ExportRecord.Status.RUNNING  # 协作式：任务在安全点收敛
        assert is_cancel_requested(record.pk) is True
        clear_cancel(record.pk)

    def test_finished_task_idempotent(self, superuser):
        record = _export(superuser, status=ExportRecord.Status.SUCCESS)
        assert cancel_record(superuser, "export", str(record.pk))["ok"] is True

    def test_foreign_record_invisible(self, superuser, normal_user):
        record = _export(superuser)
        assert cancel_record(normal_user, "export", str(record.pk))["ok"] is False

    def test_unknown_kind(self, superuser):
        assert cancel_record(superuser, "unknown", "x")["ok"] is False

    def test_safety_point_raises(self):
        request_cancel("safety-point-1")
        with pytest.raises(TaskCancelled):
            ensure_not_cancelled("safety-point-1")
        clear_cancel("safety-point-1")
        ensure_not_cancelled("safety-point-1")


class TestRerun:
    def test_resolve_view_path(self):
        assert resolve_view_path("/api/system/user/export-async").endswith("UserViewSet")
        assert resolve_view_path("/no/such/path") == ""
        assert resolve_view_path("") == ""

    def test_export_rerun_clones_and_dispatches(self, superuser, monkeypatch):
        dispatched = []
        monkeypatch.setattr(
            task_center, "_dispatch", lambda task, args=None, kwargs=None, task_id=None: dispatched.append(args)
        )
        record = _export(superuser, status=ExportRecord.Status.SUCCESS)
        result = rerun_record(superuser, "export", str(record.pk))
        assert result["ok"] is True
        clone = ExportRecord.objects.exclude(pk=record.pk).get()
        assert clone.status == ExportRecord.Status.PENDING and clone.params == record.params
        assert dispatched and dispatched[0][0] == str(clone.pk)
        assert dispatched[0][1].endswith("UserViewSet")

    def test_import_rerun_requires_source_file(self, superuser):
        record = ImportRecord.objects.create(
            creator=superuser,
            name="导入",
            module="用户",
            path="/api/system/user/import-data",
            action=ImportRecord.Action.CREATE,
            status=ImportRecord.Status.FAILURE,
        )
        result = rerun_record(superuser, "import", str(record.pk))
        assert result["ok"] is False  # 缺源文件 → 不可重放

    def test_running_record_not_rerunnable(self, superuser):
        record = _export(superuser, status=ExportRecord.Status.RUNNING)
        assert rerun_record(superuser, "export", str(record.pk))["ok"] is False

    def test_task_execution_rerun_unsupported(self, superuser):
        TaskExecution.objects.create(pk="2" * 32, name="system.tasks.demo", creator=superuser)
        assert rerun_record(superuser, "task", "2" * 32)["ok"] is False

    def test_report_rerun_without_report(self, superuser):
        record = _export(
            superuser,
            name="报表-20260922",
            module="Report",
            params={"report_id": "00000000-0000-0000-0000-000000000000"},
            status=ExportRecord.Status.SUCCESS,
        )
        result = rerun_record(superuser, "export", str(record.pk))
        assert result["ok"] is False

    def test_foreign_record_not_rerunnable(self, superuser, normal_user):
        record = _export(superuser, status=ExportRecord.Status.SUCCESS)
        assert rerun_record(normal_user, "export", str(record.pk))["ok"] is False


class TestExecutionRevokeMarking:
    def test_mark_execution_revoked_sets_terminal(self, superuser):
        TaskExecution.objects.create(pk="3" * 32, name="system.tasks.demo", creator=superuser, status="RUNNING")
        task_center.mark_execution_revoked("3" * 32)
        row = TaskExecution.objects.get(pk="3" * 32)
        assert row.status == "REVOKED" and row.date_finished is not None
        assert row.date_finished <= timezone.now()
