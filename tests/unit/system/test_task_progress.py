# -*- coding: utf-8 -*-
"""任务进度统一助手（P-2 遗留收口）测试。

口径钉死：

- 导出 / 报表：进度与阶段描述落库（``progress`` + ``stage``）；
- 导入：运行期走缓存通道（大事务内写库对下载中心不可见），终态落库；
- 协作式取消：导出 / 报表的**中间**里程碑做取消检查（安全点），终态 100 不检查；
- 未知类型只告警不抛（防新增任务类型漏登记时炸任务）。
"""

import pytest

from system.models.export import ExportRecord
from system.models.import_ import ImportRecord
from system.utils.task_center import TaskCancelled
from system.utils.task_progress import (
    KIND_EXPORT,
    KIND_IMPORT,
    KIND_REPORT,
    normalize_percent,
    update_progress,
)

pytestmark = pytest.mark.django_db


def _export_record():
    return ExportRecord.objects.create(name="用户导出", path="/api/system/user")


class TestNormalizePercent:
    @pytest.mark.parametrize(
        "value,expected",
        [(0, 0), (50, 50), (100, 100), (-5, 0), (180, 100), (None, 0), ("abc", 0), ("42", 42)],
    )
    def test_normalize(self, value, expected):
        assert normalize_percent(value) == expected


class TestUpdateProgress:
    def test_export_milestone_lands_with_stage(self):
        record = _export_record()
        assert update_progress(KIND_EXPORT, record.pk, 30, stage="统计行数") == 30
        record.refresh_from_db()
        assert record.progress == 30
        assert record.stage == "统计行数"

    def test_report_terminal_lands(self):
        record = _export_record()
        assert update_progress(KIND_REPORT, record.pk, 100) == 100
        record.refresh_from_db()
        assert record.progress == 100

    def test_out_of_range_normalized(self):
        record = _export_record()
        update_progress(KIND_EXPORT, record.pk, 180)
        record.refresh_from_db()
        assert record.progress == 100

    def test_import_running_uses_cache_channel(self):
        from system.utils.import_progress import get_import_progress

        record = ImportRecord.objects.create(name="用户导入")
        update_progress(KIND_IMPORT, record.pk, 42)
        record.refresh_from_db()
        assert record.progress == 0  # 运行期不落库（大事务未提交）
        assert get_import_progress(record.pk) == 42

    def test_import_terminal_lands(self):
        record = ImportRecord.objects.create(name="用户导入")
        update_progress(KIND_IMPORT, record.pk, 100, stage="导入完成")
        record.refresh_from_db()
        assert record.progress == 100
        assert record.stage == "导入完成"

    def test_cancel_checked_at_milestone(self, monkeypatch):
        record = _export_record()

        def _boom(record_id):
            raise TaskCancelled("用户取消")

        monkeypatch.setattr("system.utils.task_center.ensure_not_cancelled", _boom)
        with pytest.raises(TaskCancelled):
            update_progress(KIND_EXPORT, record.pk, 50)

    def test_terminal_skips_cancel_check(self, monkeypatch):
        """终态 100 不做取消检查（避免已完成的导出被翻成取消）。"""
        record = _export_record()

        def _boom(record_id):
            raise AssertionError("终态不应触发取消检查")

        monkeypatch.setattr("system.utils.task_center.ensure_not_cancelled", _boom)
        assert update_progress(KIND_EXPORT, record.pk, 100) == 100

    def test_unknown_kind_warns_without_raise(self):
        assert update_progress("unknown", 1, 50) == 50
