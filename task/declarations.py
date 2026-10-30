#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""task app 的权限同步声明：任务执行历史的双权限码口径。"""

from common.core.declarations import AppDeclarations

DECLARATIONS = AppDeclarations(
    app="task",
    audit_known_duplicates=(
        # tasks/executions GET：任务页「日志」按钮(log:SystemTask) 与任务中心抽屉
        # (list:SystemTaskExecution)，各自服务不同 UI 入口
        ("api/task/executions$", "GET"),
    ),
)
