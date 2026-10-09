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
from typing import Any

from celery.signals import task_postrun, task_prerun

from common.metrics import record_task_result

# task_id -> 开始时间（worker 进程内字典；prerun 未记录时仅计数不记耗时）
_start_times: dict[Any, float] = {}


@task_prerun.connect  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
def on_task_prerun(task_id: Any = None, **kwargs: Any) -> None:
    _start_times[task_id] = time.time()


@task_postrun.connect  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
def on_task_postrun(
    sender: Any = None, task_id: Any = None, task: Any = None, state: Any = None, **kwargs: Any
) -> None:
    started = _start_times.pop(task_id, None)
    duration = (time.time() - started) if started else None
    name = str(getattr(task, "name", None) or getattr(sender, "name", "unknown"))
    record_task_result(name, str(state or "UNKNOWN"), duration)
