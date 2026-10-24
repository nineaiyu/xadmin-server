#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""approval app 的权限同步声明：审批域的审计豁免与父菜单归属。"""

from common.core.declarations import AppDeclarations

DECLARATIONS = AppDeclarations(
    app="approval",
    audit_skip_prefixes=(
        # approval-instances/ongoing：页签级权限点（scope=ongoing 管理视角，无独立端点），
        # 经 common/core/permission.user_has_permission 按 path 授权，属设计内的功能开关权限点
        "api/approval/approval-instances/ongoing",
        # approval-delegations/all：同款功能开关权限点（列表取值域「查看全部委托记录」）
        "api/approval/approval-delegations/all",
    ),
    # 委托记录端点无同源权限点，挂「审批委托」页面菜单
    parent_menu_map={"api/approval/approval-delegations": "SystemApprovalDelegation"},
)
