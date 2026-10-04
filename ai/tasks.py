#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""AI 平台周期任务（自 system/tasks/__init__.py 与 system/utils/task/ctasks.py 随域迁出）。"""

from celery import shared_task

from common.celery.decorator import register_as_period_task
from common.utils import get_logger

logger = get_logger(__name__)


@shared_task
@register_as_period_task(crontab="12 3 * * *")
def auto_clean_ai_usage_job():
    """AI 用量账本保留期清理（保留期随 MONITOR_RETENTION_DAYS）。"""
    from ai.utils.ai_usage import auto_clean_ai_usage

    auto_clean_ai_usage()


@shared_task(bind=True, verbose_name="Build knowledge embeddings")
def build_embeddings_task(self, document_pk="", force: bool = False):
    """异步构建知识库向量（7.3）：进度经缓存通道上报，供知识库页轮询。

    状态机：视图先取单飞锁并置 running，任务内推进批次进度，终态（含异常）
    写 done/error 摘要并释放锁——锁 TTL 兜底 worker 崩溃，构建入口不会永久卡死。
    """
    from ai.models.ai import AiKnowledgeDocument
    from ai.utils.ai_embeddings import build_embeddings
    from ai.utils.embedding_progress import mark_finished, progress_callback, release_lock

    try:
        document = None
        if document_pk:
            document = AiKnowledgeDocument.objects.filter(pk=document_pk).first()
        summary = build_embeddings(document=document, force=bool(force), progress_cb=progress_callback())
        mark_finished(summary, ok=bool(summary.get("ok")))
        return summary
    except Exception as exc:  # noqa: BLE001 任务级兜底：异常也落终态摘要（前端可读）
        mark_finished({"ok": False, "detail": str(exc)[:255]}, ok=False)
        raise
    finally:
        release_lock()
