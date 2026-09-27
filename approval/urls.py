#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""审批流路由：独立前缀挂载（server/urls.py ``^api/approval/``）。

URL 前缀与 app 对齐（ADR-059）：``/api/approval/...``；Menu.path 权限点、
前端 API 层、模块裁剪 ModuleSpec 的 routes 正则已同步平移。
"""

from rest_framework.routers import SimpleRouter

from approval.views.approval import ApprovalRequestViewSet
from approval.views.approval_delegation import ApprovalDelegationViewSet
from approval.views.approval_flow import ApprovalFlowViewSet, ApprovalInstanceViewSet
from approval.views.approval_rule import ApprovalRuleViewSet
from approval.views.leave import LeaveViewSet

app_name = "approval"

router = SimpleRouter(False)
router.register("approvals", ApprovalRequestViewSet, basename="approval_request")
router.register("approval-rules", ApprovalRuleViewSet, basename="approval_rule")
router.register("approval-flows", ApprovalFlowViewSet, basename="approval_flow")
router.register("approval-instances", ApprovalInstanceViewSet, basename="approval_instance")
router.register("approval-delegations", ApprovalDelegationViewSet, basename="approval_delegation")
router.register("leaves", LeaveViewSet, basename="leave")

urlpatterns = router.urls
