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


class TestTaskRedisAggregate:
    """跨进程聚合：worker 写 redis，web 端点渲染附加（SLO 任务成功率数据源，2029-12）。"""

    def test_record_writes_redis_aggregate(self):
        from django_redis import get_redis_connection

        from common.metrics import TASK_REDIS_KEY, record_task_result

        conn = get_redis_connection("default")
        field = "xadmin.redis_task|SUCCESS"
        before = int(conn.hget(TASK_REDIS_KEY, field) or 0)
        record_task_result("xadmin.redis_task", "SUCCESS", 0.1)
        assert int(conn.hget(TASK_REDIS_KEY, field) or 0) == before + 1

    def test_render_includes_redis_aggregate(self):
        from common.metrics import record_task_result, render_metrics

        record_task_result("xadmin.render_task", "FAILURE")
        payload, content_type = render_metrics()
        text = payload.decode()
        assert 'xadmin_celery_tasks_total{task="xadmin.render_task",status="FAILURE"}' in text
        assert "# TYPE xadmin_celery_tasks_total counter" in text
        assert "text/plain" in content_type

    def test_local_counters_not_exported(self):
        """进程内 Counter/Histogram 不注册进 registry：输出不出现「本地版」重复定义。"""
        from common.metrics import record_task_result, render_metrics

        record_task_result("xadmin.local_only_task", "SUCCESS")
        text = render_metrics()[0].decode()
        # 同名指标最多一处定义（跨进程聚合版；进程内版本登记时 registry=None 不导出）
        assert text.count("# TYPE xadmin_celery_tasks_total counter") <= 1
        assert text.count("# TYPE xadmin_celery_task_duration_seconds") <= 1

    def test_duration_writes_redis_buckets(self):
        from django_redis import get_redis_connection

        from common.metrics import TASK_DURATION_REDIS_KEY, record_task_result

        conn = get_redis_connection("default")
        # 先清理本次任务的字段，避免跨用例累积干扰桶计数断言
        for key in conn.hkeys(TASK_DURATION_REDIS_KEY):
            text = key.decode() if isinstance(key, bytes) else str(key)
            if text.startswith("xadmin.duration_task|"):
                conn.hdel(TASK_DURATION_REDIS_KEY, key)

        record_task_result("xadmin.duration_task", "SUCCESS", 0.42)

        def field(name):
            value = conn.hget(TASK_DURATION_REDIS_KEY, name)
            return value.decode() if isinstance(value, bytes) else value

        assert int(field("xadmin.duration_task|le:0.1") or 0) == 0
        assert int(field("xadmin.duration_task|le:0.5") or 0) == 1
        assert int(field("xadmin.duration_task|count") or 0) == 1
        assert abs(float(field("xadmin.duration_task|sum") or 0) - 0.42) < 1e-9

    def test_render_includes_duration_histogram(self):
        from django_redis import get_redis_connection

        from common.metrics import TASK_DURATION_REDIS_KEY, record_task_result, render_metrics

        conn = get_redis_connection("default")
        for key in conn.hkeys(TASK_DURATION_REDIS_KEY):
            text = key.decode() if isinstance(key, bytes) else str(key)
            if text.startswith("xadmin.render_duration|"):
                conn.hdel(TASK_DURATION_REDIS_KEY, key)

        record_task_result("xadmin.render_duration", "SUCCESS", 2.0)
        output = render_metrics()[0].decode()
        assert "# TYPE xadmin_celery_task_duration_seconds histogram" in output
        assert 'xadmin_celery_task_duration_seconds_bucket{task="xadmin.render_duration",le="5"} 1' in output
        assert 'xadmin_celery_task_duration_seconds_bucket{task="xadmin.render_duration",le="+Inf"} 1' in output
        assert 'xadmin_celery_task_duration_seconds_count{task="xadmin.render_duration"} 1' in output
        assert 'xadmin_celery_task_duration_seconds_sum{task="xadmin.render_duration"}' in output

    def test_render_degrades_without_redis(self, monkeypatch):
        """redis 不可用：端点其余指标不受影响，聚合渲染跳过。"""
        import common.metrics as metrics_module

        def _boom(*args, **kwargs):
            raise ConnectionError("redis down")

        monkeypatch.setattr("django_redis.get_redis_connection", _boom)
        payload, _ = metrics_module.render_metrics()
        assert b"xadmin_http" in payload
