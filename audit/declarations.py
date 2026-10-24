#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""audit app 的权限同步声明：操作日志端点的多权限码口径。"""

from common.core.declarations import AppDeclarations

DECLARATIONS = AppDeclarations(
    app="audit",
    audit_known_duplicates=(
        # logs/operation GET：操作日志页(list:SystemOperationLog)、用户页「变更历史」
        # (changeHistory:SystemUser) 与部门页「变更历史」(changeHistory:SystemDept)
        ("api/audit/logs/operation$", "GET"),
    ),
)
