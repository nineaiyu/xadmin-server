#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""AI 平台周期任务（自 system/tasks/__init__.py 与 system/utils/task/ctasks.py 随域迁出）。"""

from typing import Any

from celery import shared_task

from common.celery.decorator import register_as_period_task
from common.utils import get_logger

logger = get_logger(__name__)


@shared_task  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
@register_as_period_task(crontab="12 3 * * *", module="ai")  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
def auto_clean_ai_usage_job() -> None:
    """AI 用量账本保留期清理（保留期随 MONITOR_RETENTION_DAYS）。"""
    from ai.services.assistant.ai_usage import auto_clean_ai_usage

    auto_clean_ai_usage()


@shared_task  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
@register_as_period_task(crontab="31 7 * * *", description="生成 AI 站点日报（首页日报卡）", module="ai")  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
def ai_daily_report_job() -> None:
    """AI 日报定时生成（首页日报卡；开关关闭时空转）。"""
    from ai.services.daily_report import generate_daily_report

    generate_daily_report()


@shared_task(bind=True, verbose_name="Build knowledge embeddings")  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
def build_embeddings_task(self: Any, document_pk: Any = "", force: bool = False) -> Any:
    """异步构建知识库向量（7.3）：进度经缓存通道上报，供知识库页轮询。

    状态机：视图先取单飞锁并置 running，任务内推进批次进度，终态（含异常）
    写 done/error 摘要并释放锁——锁 TTL 兜底 worker 崩溃，构建入口不会永久卡死。
    """
    from ai.models.ai import AiKnowledgeDocument
    from ai.services.knowledge.ai_embeddings import build_embeddings
    from ai.services.knowledge.embedding_progress import mark_finished, progress_callback, release_lock

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
    from ai.services.knowledge.ai_knowledge import sync_knowledge
    from ai.services.knowledge.sync_progress import mark_finished, mark_running, release_lock

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


@shared_task(bind=True, verbose_name="Ingest uploaded knowledge document")  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
def ingest_upload_document_task(self: Any, document_pk: Any) -> Any:
    """异步摄取上传文档：重建分块并推进状态（失败保留可读原因）。"""
    from ai.models.ai import AiKnowledgeDocument
    from ai.services.knowledge.ai_knowledge import rebuild_chunks

    doc = AiKnowledgeDocument.objects.filter(pk=document_pk).first()
    if doc is None or doc.source_type != AiKnowledgeDocument.SourceType.UPLOAD:
        return None
    doc.ingest_status = AiKnowledgeDocument.IngestStatus.PROCESSING
    doc.save(update_fields=["ingest_status"])
    try:
        count = rebuild_chunks(doc)
        doc.chunk_count = count
        doc.ingest_status = AiKnowledgeDocument.IngestStatus.SUCCESS
        doc.ingest_error = ""
        doc.save(update_fields=["chunk_count", "ingest_status", "ingest_error", "synced_at"])
        from ai.services.knowledge.ai_embeddings import schedule_auto_rebuild

        schedule_auto_rebuild(doc)
        return {"ok": True, "chunks": count}
    except Exception as exc:  # noqa: BLE001 任务失败落状态，前端可见并可重传
        logger.warning("knowledge ingest failed. doc:%s", document_pk, exc_info=True)
        doc.ingest_status = AiKnowledgeDocument.IngestStatus.FAILED
        doc.ingest_error = str(exc)[:500]
        doc.chunk_count = 0
        doc.save(update_fields=["chunk_count", "ingest_status", "ingest_error", "synced_at"])
        return {"ok": False, "detail": str(exc)[:255]}


@shared_task(verbose_name="Test AI profile connectivity")  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
def test_ai_profile_task(profile_pk: Any) -> dict[str, Any]:
    """档案连通性探测（后台执行）。

    外呼供应商是秒级网络 IO，放在请求线程里会占住 daphne 工作线程（PERF-AI-01）；
    下沉后请求只排队，前端凭 task_id 轮询 ``test-result`` 回读。失败不外抛：
    以 ``{"ok": False, "detail": ...}`` 返回，避免把网络抖动记成任务失败（会触发告警）。
    """
    from ai.models import AiProfile
    from ai.services.assistant.ai import profile_credentials
    from integrations.sdk.ai.chat import AiSdkError, ChatCompletionsClient

    profile = AiProfile.objects.filter(pk=profile_pk).first()
    if profile is None:
        return {"ok": False, "detail": "profile not found"}
    if not profile.is_configured:
        return {"ok": False, "detail": "not configured"}
    try:
        reply = ChatCompletionsClient(profile_credentials(profile)).chat([{"role": "user", "content": "ping"}])
    except AiSdkError as exc:
        return {"ok": False, "detail": str(exc)[:255]}
    except Exception as exc:  # noqa: BLE001 任务入口兜底：任何异常都归一为可读结论
        logger.warning("AI profile test failed. profile:%s", profile_pk, exc_info=True)
        return {"ok": False, "detail": str(exc)[:255]}
    return {"ok": True, "detail": reply[:120]}
