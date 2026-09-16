#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""Celery 任务指标采集（Prometheus，随 metrics 能力可选启用）。

- `task_prerun` / `task_postrun` 信号接线：按终态（SUCCESS / FAILURE / REVOKED …）
  计数 + 任务耗时直方图；
- 指标为旁路能力：prometheus-client 缺失或未启用时为 no-op，任何异常不影响任务执行；
- 使用方（SLO）：任务成功率 = SUCCESS / total（口径见 docs/ops/observability.md）。

装载点：common/apps.py ready()（与 failure_handler 同批）。
"""

import time

from celery.signals import task_postrun, task_prerun

from common.metrics import record_task_result

# task_id -> 开始时间（worker 进程内字典；prerun 未记录时仅计数不记耗时）
_start_times = {}


@task_prerun.connect
def on_task_prerun(task_id=None, **kwargs):
    _start_times[task_id] = time.time()


@task_postrun.connect
def on_task_postrun(sender=None, task_id=None, task=None, state=None, **kwargs):
    started = _start_times.pop(task_id, None)
    duration = (time.time() - started) if started else None
    name = getattr(task, "name", None) or getattr(sender, "name", "unknown")
    record_task_result(name, str(state or "UNKNOWN"), duration)
