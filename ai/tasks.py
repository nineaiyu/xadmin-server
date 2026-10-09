#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""AI 平台周期任务（自 system/tasks/__init__.py 与 system/utils/task/ctasks.py 随域迁出）。"""

from typing import Any

from celery import shared_task

from common.celery.decorator import register_as_period_task
from common.utils import get_logger

logger = get_logger(__name__)


@shared_task  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
@register_as_period_task(crontab="12 3 * * *")  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
def auto_clean_ai_usage_job() -> None:
    """AI 用量账本保留期清理（保留期随 MONITOR_RETENTION_DAYS）。"""
    from ai.utils.ai_usage import auto_clean_ai_usage

    auto_clean_ai_usage()


@shared_task(bind=True, verbose_name="Build knowledge embeddings")  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
def build_embeddings_task(self: Any, document_pk: Any = "", force: bool = False) -> Any:
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


@shared_task(bind=True, verbose_name="Sync repository knowledge documents")  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
def sync_repo_task(self: Any) -> Any:
    """异步重扫仓库文档并全量重建分块（自请求线程内同步拆出）。

    状态机：视图先取单飞锁并置 running（上一轮旧终态就地清除），任务内重置
    running 并写终态（含异常）后释放锁——锁 TTL 兜底 worker 崩溃，同步入口
    不会永久卡死。同步摘要随状态通道保留 1 小时，供知识库页经 sync-repo/status
    轮询。
    """
    from ai.utils.ai_knowledge import sync_knowledge
    from ai.utils.sync_progress import mark_finished, mark_running, release_lock

    mark_running()
    try:
        summary = sync_knowledge()
        mark_finished(summary, ok=True)
        return summary
    except Exception as exc:  # noqa: BLE001 任务级兜底：异常也落终态摘要（前端可读）
        mark_finished({}, ok=False, detail=str(exc)[:255])
        raise
    finally:
        release_lock()
