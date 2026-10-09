#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""通知投递异步任务：全量扇出（公告/群发/多人提醒）脱离请求线程。

站内信已落库（MessageContent + notice_user），WS 推送只是「在线即时提醒」；
在线人数可达数千时，在请求线程内逐人 ``group_send`` 串行执行会拖住响应
（数千条 Redis 命令），统一改由 worker 执行——投递失败只记日志，
页面刷新仍可从库里补看。
"""

import json
from typing import Any

from celery import shared_task

from common.utils import get_logger

logger = get_logger(__name__)


def json_safe(value: Any) -> Any:
    """任务参数 JSON 化（UUID / gettext_lazy / Decimal 等转原生字符串，结构保持）。

    Celery 默认 JSON 序列化对非原生类型直接抛 EncodeError（历史上 gettext_lazy
    代理曾让审批通知任务整体失败），投递前统一清洗。
    """
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


@shared_task  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
def push_messages_job(user_pks: Any, message: Any, message_type: Any = "push_message") -> Any:
    """批量 WS 推送（worker 内一次桥接，逐人 group_send）。

    返回目标人数，便于任务日志核对；推送异常吞掉（站内信已持久化，
    WS 推送是即时提醒，失败不影响业务语义）。
    """
    from message.utils import push_messages

    pks = list(user_pks)
    if not pks:
        return 0
    try:
        push_messages(pks, message, message_type)
    except Exception:  # noqa: BLE001 推送失败不影响落库结果
        logger.warning("push messages job failed", exc_info=True)
    return len(pks)
