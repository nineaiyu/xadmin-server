#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""MCP 客户端调用审计（与 mcp_client 拆开，控制单文件行数）。"""

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
    落库统一走 ``audit.services.record``（序列化 / 截断 / creator 解析单源）；
    ``object_pk`` 写服务器主键（调用流水报表按服务器过滤的契约）。
    """
    from audit.services import OperationLog, record
    from common.core.oplog_recorder import desensitize_payload

    record(
        module=MCP_AUDIT_MODULE,
        object_pk=str(getattr(server, "pk", "")),
        creator=user,
        auth_type=OperationLog.AuthType.AI,
        status_code=API_SUCCESS_CODE if ok else 1001,
        response_code=API_SUCCESS_CODE if ok else 1001,
        changes={
            # server 可能已被删除（动态目录后服务器被删的 fail-closed 路径）：安全取值
            "server": getattr(server, "name", "") or "",
            "tool": tool,
            "arguments": desensitize_payload(arguments or {}),
            "status": "ok" if ok else "failed",
            "detail": detail,
            **(extra or {}),
        },
    )
