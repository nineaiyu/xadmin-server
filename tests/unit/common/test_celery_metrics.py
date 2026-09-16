#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""Celery 任务指标采集（SLO 数据源）：计数与耗时记录、信号接线契约。"""


def _task_counter_value(task: str, status: str) -> float:
    from common.metrics import _TASKS

    return _TASKS.labels(task=task, status=status)._value.get()


class TestRecordTaskResult:
    def test_record_increments_counter(self):
        from common.metrics import record_task_result

        before = _task_counter_value("xadmin.demo_task", "SUCCESS")
        record_task_result("xadmin.demo_task", "SUCCESS", 0.42)
        assert _task_counter_value("xadmin.demo_task", "SUCCESS") == before + 1

    def test_failure_state_counted(self):
        from common.metrics import record_task_result

        before = _task_counter_value("xadmin.demo_task", "FAILURE")
        record_task_result("xadmin.demo_task", "FAILURE")
        assert _task_counter_value("xadmin.demo_task", "FAILURE") == before + 1

    def test_missing_duration_is_allowed(self):
        """只计数不记耗时（prerun 缺失场景）不得抛错。"""
        from common.metrics import record_task_result

        before = _task_counter_value("xadmin.no_duration_task", "UNKNOWN")
        record_task_result("xadmin.no_duration_task", "UNKNOWN")
        assert _task_counter_value("xadmin.no_duration_task", "UNKNOWN") == before + 1


class TestSignalHandlers:
    def test_postrun_records_state_and_cleans_start_time(self):
        from common.celery import metrics as celery_metrics

        class _Task:
            name = "xadmin.signal_task"

        before = _task_counter_value("xadmin.signal_task", "SUCCESS")
        celery_metrics.on_task_prerun(task_id="tid-signal-1")
        celery_metrics.on_task_postrun(task_id="tid-signal-1", task=_Task(), state="SUCCESS")
        assert _task_counter_value("xadmin.signal_task", "SUCCESS") == before + 1
        assert "tid-signal-1" not in celery_metrics._start_times, "耗时记录应清理，避免内存驻留"

    def test_postrun_without_prerun_still_counts(self):
        from common.celery import metrics as celery_metrics

        class _Task:
            name = "xadmin.orphan_task"

        before = _task_counter_value("xadmin.orphan_task", "REVOKED")
        celery_metrics.on_task_postrun(task_id="tid-orphan", task=_Task(), state="REVOKED")
        assert _task_counter_value("xadmin.orphan_task", "REVOKED") == before + 1
