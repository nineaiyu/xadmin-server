#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""AI 语义审计落库（自 ai_actions 拆分，行为不变）。

- ``audit_ai_action``：AI 动作执行审计（module=AI:action）；
- ``audit_ai_ask``：文档问答审计（module=AI:ask）。

与 AI:nl_query 同一采集口径（AI 观测看板的统一数据源：用量 / 成功率 / 趋势）；
审计写失败只记日志，不影响业务链路。
"""

import json
from typing import Any

from common.core.response import API_SUCCESS_CODE
from common.utils import get_logger

logger = get_logger(__name__)


def audit_ai_action(
    user: Any, action_key: str, params: Any, ok: bool, detail: str, extra: dict[str, Any] | None = None
) -> None:
    """AI 动作语义审计：落 OperationLog(module=AI:action, auth_type=ai)。"""
    from audit.services import OperationLog

    try:
        OperationLog.objects.create(
            module="AI:action",
            object_pk=str(getattr(user, "pk", "")),
            auth_type=OperationLog.AuthType.AI,
            status_code=API_SUCCESS_CODE if ok else 1001,
            response_code=API_SUCCESS_CODE if ok else 1001,
            changes=json.dumps(
                {
                    "action": action_key,
                    "params": params,
                    "status": "ok" if ok else "failed",
                    "detail": detail,
                    **(extra or {}),
                },
                ensure_ascii=False,
                default=str,
            )[:4096],
        )
    except Exception:  # noqa: BLE001 审计失败不影响业务
        logger.warning("write AI action audit failed", exc_info=True)


def audit_ai_ask(
    user_obj: Any,
    question: str,
    ok: bool,
    detail: str = "",
    usage: dict[str, Any] | None = None,
    guard: dict[str, Any] | None = None,
) -> None:
    """文档问答语义审计：落 OperationLog(module=AI:ask, auth_type=ai)。

    与 AI:action / AI:nl_query 同一采集口径（AI 观测看板的统一数据源：用量/成功率/趋势）。
    usage：LLM 供应商返回的 token 用量（成本维度观测，缺省不写）。
    guard 护栏摘要（prompt 摘要 / 注入标记 / 脱敏命中数 / 输出长度，缺省不写）。
    """
    from audit.services import OperationLog

    try:
        OperationLog.objects.create(
            module="AI:ask",
            object_pk=str(getattr(user_obj, "pk", "")),
            auth_type=OperationLog.AuthType.AI,
            status_code=API_SUCCESS_CODE if ok else 1001,
            response_code=API_SUCCESS_CODE if ok else 1001,
            changes=json.dumps(
                {
                    "question": (question or "")[:200],
                    "status": "ok" if ok else "failed",
                    "detail": (detail or "")[:200],
                    **({"usage": usage} if usage else {}),
                    **({"guard": guard} if guard else {}),
                },
                ensure_ascii=False,
            )[:4096],
        )
    except Exception:  # noqa: BLE001 审计失败不影响业务
        logger.warning("write AI ask audit failed", exc_info=True)
