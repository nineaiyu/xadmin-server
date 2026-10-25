#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""ai app 的权限同步声明：MCP 端点的审计豁免口径。"""

from common.core.declarations import AppDeclarations

DECLARATIONS = AppDeclarations(
    app="ai",
    # api/ai/mcp：MCP 端点是无 actions 的 APIView，审计按 route.actions 过滤方法时
    # 恒不匹配；运行期由 IsAuthenticated 权限链按 path 正则命中权限点
    # （非超管未授权即 403）
    audit_skip_prefixes=("api/ai/mcp",),
    # MCP 治理新端点的权限点归属：调用审计报表挂 AiMcpServers 页面菜单，
    # 权限码用独立后缀 McpCallLog（list:McpCallLog / retrieve:McpCallLog），
    # 与服务器配置面（list:AiMcpServers）分开授权——看配置 ≠ 看调用流水
    parent_menu_map={
        "api/ai/mcp-call-logs": ("McpCallLog", "AiMcpServers"),
    },
)
