#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""审批流路由：经 system/urls.py 以 ``path("", include("approval.urls"))`` 挂载。

URL 前缀保持 ``/api/system/...``（Menu.path 权限点、前端路由、模块裁剪
ModuleSpec 的 routes 正则均以此为键，拆分不改路径）；不设 app_name，
视图名继续落在 system 命名空间下，与拆分前完全一致。
"""

from rest_framework.routers import SimpleRouter

from approval.views.approval import ApprovalRequestViewSet
from approval.views.approval_delegation import ApprovalDelegationViewSet
from approval.views.approval_flow import ApprovalFlowViewSet, ApprovalInstanceViewSet
from approval.views.approval_rule import ApprovalRuleViewSet
from approval.views.leave import LeaveViewSet

router = SimpleRouter(False)
router.register("approvals", ApprovalRequestViewSet, basename="approval_request")
router.register("approval-rules", ApprovalRuleViewSet, basename="approval_rule")
router.register("approval-flows", ApprovalFlowViewSet, basename="approval_flow")
router.register("approval-instances", ApprovalInstanceViewSet, basename="approval_instance")
router.register("approval-delegations", ApprovalDelegationViewSet, basename="approval_delegation")
router.register("leaves", LeaveViewSet, basename="leave")

urlpatterns = router.urls
