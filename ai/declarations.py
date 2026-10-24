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
)
