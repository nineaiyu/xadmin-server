#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : tasks
# author : ly_13
# date : 9/15/2024
from typing import Any

from celery import shared_task

from captcha.models import CaptchaLog, CaptchaStore
from common.celery.decorator import register_as_period_task
from common.utils import get_logger

logger = get_logger(__name__)


@shared_task  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
@register_as_period_task(crontab="12 2 * * *")  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
def auto_clean_expired_captcha_job() -> None:
    CaptchaStore.remove_expired()


@shared_task  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
def flush_captcha_logs(events: list[dict[str, Any]]) -> int:
    """验证码观测事件批量落库（telemetry 单点投递，失败不重试不抛出）。

    遥测完整性服从主链路安全：异常只记日志返回 0，避免验证流量因观测面
    故障被反向拖挂（Celery 死信堆积）。
    """
    from captcha import telemetry

    try:
        rows = [telemetry.build_log_row(event) for event in events if isinstance(event, dict)]
        if rows:
            CaptchaLog.objects.bulk_create(rows, ignore_conflicts=True)
        return len(rows)
    except Exception:  # noqa: BLE001 观测面 catch-all：不拖挂 worker
        logger.warning("captcha log flush failed (%s events dropped)", len(events), exc_info=True)
        return 0


@shared_task  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
@register_as_period_task(crontab="23 3 * * *")  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
def purge_captcha_logs_job() -> int:
    """按保留期清理验证码观测日志（默认 30 天，全 0=只记录不清理）。"""
    return CaptchaLog.purge_expired_logs()
