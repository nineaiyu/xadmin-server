#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""敏感操作审批：通过后自动执行动作（注册表 + 快照 + 落库）。"""

import json
import re

from common.utils import get_logger

from .constants import APPROVAL_PAYLOAD_MAX_SIZE

logger = get_logger(__name__)

# 通过后动作注册表：{请求路径正则: handler(approval, user) -> (ok, detail)}
# 审批通过后按路径命中自动执行业务落库，省去申请人手动重试；未注册的路径行为不变
ON_APPROVED_HANDLERS: dict = {}


def register_on_approved(path_pattern: str, handler):
    """注册「审批通过后自动执行」的动作（键为请求路径正则）。"""
    ON_APPROVED_HANDLERS[path_pattern] = handler


def snapshot_payload(request) -> dict:
    """请求体快照：仅 JSON 且小体积时保留，供审批通过后自动执行业务落库。

    multipart（文件上传等）与超大 body 一律留空——服务端不做大 body 重放。
    """
    if not str(getattr(request, "content_type", "") or "").startswith("application/json"):
        return {}
    try:
        data = request.data
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    try:
        if len(json.dumps(data, default=str)) > APPROVAL_PAYLOAD_MAX_SIZE:
            return {}
    except (TypeError, ValueError):
        return {}
    return data


def run_on_approved(approval, user):
    """审批通过后自动执行业务：命中注册路径且带快照时执行，成功即标记自动完成。

    失败只记日志：审批结果已生效，落库失败仍保留申请人手动重放兜底。
    """
    if approval.auto_completed or approval.consume_time or not approval.payload:
        return
    from django.utils import timezone

    from approval.models.approval import ApprovalRequest

    for pattern, handler in ON_APPROVED_HANDLERS.items():
        if not re.match(pattern, approval.path or ""):
            continue
        try:
            ok, detail = handler(approval, user)
        except Exception:
            logger.exception("on approved handler raised. approval:%s", approval.pk)
            return
        if ok:
            now = timezone.now()
            ApprovalRequest.objects.filter(pk=approval.pk, auto_completed=False).update(
                auto_completed=True, consume_time=now, updated_time=now
            )
            approval.auto_completed = True
            approval.consume_time = now
            logger.info("approval auto completed. approval:%s", approval.pk)
        else:
            logger.warning("on approved handler rejected. approval:%s detail:%s", approval.pk, detail)
        return
