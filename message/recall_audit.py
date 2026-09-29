#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""聊天撤回审计快照：原文本体从消息行清空，但**留一份到操作日志**。

合规口径：撤回不再等于「不可审计的物理删除」——超管可在操作日志按
``module=chat:recall`` 检索到被撤回的原文（随日志保留期与冷归档管理）。
审计写入失败不影响撤回本身（消息内容仍按撤回语义清空）。
"""

import json

from common.utils import get_logger

logger = get_logger(__name__)

#: 操作日志 module 值（审计检索与测试断言共用）
RECALL_AUDIT_MODULE = "chat:recall"
#: 快照正文截断长度（超长只留前缀，另标 truncated）
RECALL_SNAPSHOT_MAX = 2000


def write_recall_snapshot(message, user) -> None:
    """写入撤回审计快照（异常只告警：撤回主流程不受影响）。"""
    try:
        from system.services import OperationLog

        OperationLog.objects.create(
            module=RECALL_AUDIT_MODULE,
            path=f"/api/chat/message/{message.pk}/recall",
            method="POST",
            object_pk=str(message.pk),
            body=json.dumps(
                {
                    "message_type": message.message_type,
                    "content": (message.content or "")[:RECALL_SNAPSHOT_MAX],
                    "truncated": len(message.content or "") > RECALL_SNAPSHOT_MAX,
                },
                ensure_ascii=False,
            ),
            status_code=1000,
            response_result="recalled-content-snapshot",
            creator=user if getattr(user, "pk", None) else None,
        )
    except Exception:  # noqa: BLE001 审计失败不影响撤回（内容仍会被清空）
        logger.warning("write chat recall audit snapshot failed", exc_info=True)
