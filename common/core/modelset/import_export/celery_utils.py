#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""导入导出：Celery 异步分发与错误展开工具。"""

import itertools
import json
import math
import uuid

from django.utils.translation import gettext_lazy as _

from common.core.response import ApiResponse
from common.tasks import background_task_view_set_job
from common.utils import get_logger

logger = get_logger(__name__)

# 活跃 worker 探测结果的短缓存：inspect().active() 是 broker 广播（最长 ~1s 阻塞），
# 每次导入请求都在请求线程内做一遍会明显拖慢主流程；短窗口内复用同一探测结果
CELERY_WORKER_PROBE_CACHE_KEY = "celery_active_worker_probe"
CELERY_WORKER_PROBE_CACHE_TIMEOUT = 15

# 异步导入分片大小；自关联数据经拓扑排序后必须按依赖顺序整体执行，不能分片
CELERY_IMPORT_DEFAULT_BATCH = 100
CELERY_IMPORT_SINGLE_BATCH = 99999999


def has_active_celery_worker():
    """探测是否存在活跃 Celery worker（结果短缓存，避免请求线程内反复广播阻塞）。"""
    from django.core.cache import cache

    from server.celery import app

    cached = cache.get(CELERY_WORKER_PROBE_CACHE_KEY)
    if cached is not None:
        return cached
    try:
        active_workers = app.control.inspect().active()
        result = bool(active_workers)
    except Exception as e:
        # 探测失败（broker 不可达等）视为无 worker，走同步降级
        logger.warning(f"probe active celery worker failed: {e}")
        result = False
    cache.set(CELERY_WORKER_PROBE_CACHE_KEY, result, CELERY_WORKER_PROBE_CACHE_TIMEOUT)
    return result


def _flatten_row_errors(row, ser_errors, limit):
    """把 DRF serializer.errors 展开为字段级条目 [{row, field, message}]，最多 limit 条。

    嵌套序列化器（dict 值）无法定位单一字段，整体 JSON 序列化进 message。
    """
    items = []
    for field, msgs in (ser_errors or {}).items():
        if not isinstance(msgs, list):
            msgs = [msgs]
        for msg in msgs:
            if isinstance(msg, dict):
                msg = json.dumps(msg, ensure_ascii=False, default=str)
            items.append({"row": row, "field": field, "message": str(msg)[:200]})
            if len(items) >= limit:
                return items
    return items


def run_view_by_celery_task(view, request, kwargs, data, batch_length=100):
    """把导入/批量操作分发到 Celery，返回 ``ApiResponse`` 或 ``None``。

    - 返回 ``ApiResponse``：任务已提交，调用方直接返回该响应；
    - 返回 ``None``：调用方应改为同步执行。

    三种情形共用 ``None`` 语义（调用方处理方式一致，均落到同步分支）：
    1. ``task`` 参数为 false（调用方显式要求同步）；
    2. 无活跃 worker（自动降级）；
    3. 任务提交异常（兜底降级，错误已记日志）。
    """
    task = kwargs.get(
        "task", request.query_params.get("task", "true").lower() in ["true", "1", "yes"]
    )  # 默认为任务异步导入
    if task:
        view_str = f"{view.__class__.__module__}.{view.__class__.__name__}"
        meta = request.META
        task_id = uuid.uuid4()
        if isinstance(data, dict):
            data = [data]
        meta["task_count"] = math.ceil(len(data) / batch_length)
        meta["action"] = view.action
        # 分片任务显式携带提交者身份：任务内据此构造请求（ForcedAuthentication 直通），
        # 不依赖 META 里的 cookie/令牌（排队超过 access token 寿命时后者会失效）
        meta["user_pk"] = getattr(request.user, "pk", None)
        try:
            # 检查Celery是否可用，如果不可用则直接执行任务（探测结果带短缓存，避免请求内广播阻塞）
            if not has_active_celery_worker():
                # 没有活跃的worker，直接执行任务
                logger.warning("No active Celery workers found, executing task directly")
                return None  # 返回None表示需要直接执行
            for index, batch in enumerate(itertools.batched(data, batch_length, strict=False)):
                meta["task_id"] = f"{task_id}_{index}"
                meta["task_index"] = index
                res = background_task_view_set_job.apply_async(
                    args=(view_str, meta, json.dumps(batch), view.action_map), task_id=meta["task_id"]
                )
                logger.info(f"add {view_str} task success. {res}")
            return ApiResponse(detail=_("Task add success"))
        except Exception as e:
            logger.error(f"Celery task submission failed: {e}, executing task directly")
            return None  # 如果提交任务失败，也返回None表示需要直接执行
    return None  # 如果task参数为false，直接执行
