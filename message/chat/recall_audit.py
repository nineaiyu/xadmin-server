#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""聊天撤回审计快照：原文本体从消息行清空，但**留一份到操作日志**。

合规口径：撤回不再等于「不可审计的物理删除」——超管可在操作日志按
``module=chat:recall`` 检索到被撤回的原文（随日志保留期与冷归档管理）。
审计写入失败不影响撤回本身（消息内容仍按撤回语义清空）。
"""

import json
from typing import Any

from audit.services import record
from common.core.response import API_SUCCESS_CODE

#: 操作日志 module 值（审计检索与测试断言共用）
RECALL_AUDIT_MODULE = "chat:recall"
#: 快照正文截断长度（超长只留前缀，另标 truncated）
RECALL_SNAPSHOT_MAX = 2000


def write_recall_snapshot(message: Any, user: Any) -> None:
    """写入撤回审计快照（异常只告警：撤回主流程不受影响）。

    落库统一走 :func:`audit.services.record`；快照 JSON 的组装与正文截断
    语义留在本模块（message_type/content/truncated 的形状是撤回域契约）。
    """
    record(
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
        status_code=API_SUCCESS_CODE,
        response_result="recalled-content-snapshot",
        creator=user,
    )
