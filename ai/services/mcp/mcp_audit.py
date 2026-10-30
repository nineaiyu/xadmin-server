#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""MCP 客户端调用审计（与 mcp_client 拆开，控制单文件行数）。"""

import json
from typing import Any

from common.core.response import API_SUCCESS_CODE

MCP_AUDIT_MODULE = "AI:mcp:client"


def audit_mcp_call(
    user: Any,
    server: Any,
    tool: str,
    ok: bool,
    detail: str = "",
    arguments: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    """MCP 调用语义审计：落 OperationLog(module=AI:mcp:client)。

    ``extra``：补充对账维度——AI 动作链路传 ``channel=mcp_tool``，与
    同请求落下的 AI:action 审计行按调用参数对上双模块记录。
    """
    import logging

    from audit.services import OperationLog
    from common.core.oplog_recorder import desensitize_payload

    logger = logging.getLogger(__name__)
    try:
        OperationLog.objects.create(
            module=MCP_AUDIT_MODULE,
            object_pk=str(getattr(server, "pk", "")),
            creator=user if getattr(user, "pk", None) else None,
            auth_type=OperationLog.AuthType.AI,
            status_code=API_SUCCESS_CODE if ok else 1001,
            response_code=API_SUCCESS_CODE if ok else 1001,
            changes=json.dumps(
                {
                    "server": server.name,
                    "tool": tool,
                    "arguments": desensitize_payload(arguments or {}),
                    "status": "ok" if ok else "failed",
                    "detail": detail,
                    **(extra or {}),
                },
                ensure_ascii=False,
                default=str,
            )[:4096],
        )
    except Exception:  # noqa: BLE001 审计失败不影响业务
        logger.warning("write MCP client audit failed", exc_info=True)
