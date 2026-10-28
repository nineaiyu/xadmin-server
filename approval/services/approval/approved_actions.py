#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""敏感操作审批：通过后自动执行动作（注册表 + 快照 + 落库）。"""

import json
import re
from typing import Any

from common.base.crypto import signer
from common.utils import get_logger

from .constants import APPROVAL_PAYLOAD_MAX_SIZE

logger = get_logger(__name__)

# 请求体快照的密封键：审批通过后的自动执行需要**可逆**还原（原值要落库），
# 因此不能像 params 那样单向哈希；落库形态用字段级加密（AES-256-GCM）收敛，
# 明文只在进程内存中出现（旧明文行无密封标记，按兼容路径原样返回）
PAYLOAD_SEAL_KEY = "__sealed__"


def seal_payload(data: dict[str, Any]) -> dict[str, Any]:
    """请求体快照加密落库（空载荷返回空 dict）。"""
    if not data:
        return {}
    return {PAYLOAD_SEAL_KEY: signer.encrypt(json.dumps(data, ensure_ascii=False, default=str)).decode("utf-8")}


def unseal_payload(stored: Any) -> dict[str, Any]:
    """还原请求体快照；兼容升级前的明文行（无密封标记时原样返回）。"""
    if not isinstance(stored, dict):
        return {}
    sealed = stored.get(PAYLOAD_SEAL_KEY)
    if not sealed:
        return stored
    try:
        restored = json.loads(signer.decrypt(str(sealed)))
    except Exception:  # noqa: BLE001 主密钥轮换/密文损坏：自动执行降级为申请人手动重放
        logger.warning("approval payload unseal failed", exc_info=True)
        return {}
    return restored if isinstance(restored, dict) else {}


# 通过后动作注册表：{请求路径正则: handler(approval, user) -> (ok, detail)}
# 审批通过后按路径命中自动执行业务落库，省去申请人手动重试；未注册的路径行为不变
ON_APPROVED_HANDLERS: dict[str, Any] = {}


def register_on_approved(path_pattern: str, handler: Any) -> None:
    """注册「审批通过后自动执行」的动作（键为请求路径正则）。"""
    ON_APPROVED_HANDLERS[path_pattern] = handler


def snapshot_payload(request: Any) -> dict[str, Any]:
    """请求体快照：仅 JSON 且小体积时保留，供审批通过后自动执行业务落库。

    multipart（文件上传等）与超大 body 一律留空——服务端不做大 body 重放。
    """
    if not str(getattr(request, "content_type", "") or "").startswith("application/json"):
        return {}
    try:
        data = request.data
    except Exception:
        # request.data 解析失败（如非法 JSON）：按无可比较载荷处理（指纹降级为空）
        return {}
    if not isinstance(data, dict):
        return {}
    try:
        if len(json.dumps(data, default=str)) > APPROVAL_PAYLOAD_MAX_SIZE:
            return {}
    except (TypeError, ValueError):
        return {}
    return seal_payload(data)


def run_on_approved(approval: Any, user: Any) -> None:
    """审批通过后自动执行业务：命中注册路径且带快照时执行，成功即标记自动完成。

    失败只记日志：审批结果已生效，落库失败仍保留申请人手动重放兜底。
    """
    if approval.auto_completed or approval.consume_time or not approval.payload:
        return
    payload = unseal_payload(approval.payload)
    if not payload:
        logger.warning("approval payload unavailable, skip auto execute. approval:%s", approval.pk)
        return
    # 处理器沿用 approval.payload 的既有读取面：先解封再回填内存对象
    approval.payload = payload
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
