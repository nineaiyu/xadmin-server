#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""审批委托视图集（自 approval_flow.py 拆出，仅因文件行数门禁；行为与拆分前一致）。

委托只影响「待办归属」（生效委托用代理人替换原审批人），不改变节点定义；
解析语义见 approval/utils/approval_flow/conditions.py::resolve_assignee_pairs 的委托展开。
"""

from typing import Any

from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import OrderingFilter

from approval.models.approval import ApprovalDelegation
from approval.serializers.approval_delegation import ApprovalDelegationSerializer
from common.core.filter import BaseFilterSet
from common.core.modelset import BaseModelSet, SuggestionsAction
from common.core.permission import user_has_permission

#: 「查看全部委托记录」管理视角的权限点 path（无独立路由的功能授权，登记于 loadjson/menu.json）
ALL_DELEGATIONS_PERMISSION_PATH = "api/approval/approval-delegations/all$"


class ApprovalDelegationFilter(BaseFilterSet):
    class Meta:
        model = ApprovalDelegation
        fields = ["is_active", "delegator", "delegate"]


class ApprovalDelegationViewSet(BaseModelSet, SuggestionsAction):
    """审批委托（审批流三期）：委托人 × 代理人 × 生效时段 × 流程范围（空 = 全部流程）。

    只影响「待办归属」（生效委托用代理人替换原审批人），不改变节点定义；
    取值域默认收敛为「本人作为委托人」，委托记录属个人审批权处置，他人无权查看/修改。
    """

    queryset = ApprovalDelegation.objects.all()
    serializer_class = ApprovalDelegationSerializer
    filterset_class = ApprovalDelegationFilter
    filter_backends = (DjangoFilterBackend, OrderingFilter)
    ordering = ["-created_time"]
    ordering_fields = ["created_time", "start_time", "end_time"]
    select_related_fields = ("delegator", "delegate")
    # 远程联想仅开放代理人：委托人在同表单里保持 api-search-user 弹窗选择器
    suggestion_fields = ("delegate",)

    def get_queryset(self) -> Any:
        """取值域：超管与「查看全部委托」授权角色见全部，其余仅见本人作为委托人的记录。

        越权取件（他人记录的详情/改/删）同样经本方法收敛为不可见；写入侧
        另有序列化器 delegator 归属校验，两层互不依赖。
        """
        queryset = super().get_queryset()
        user = self.request.user
        if not user or not user.is_authenticated:
            return queryset.none()
        if user.is_superuser or user_has_permission(user, ALL_DELEGATIONS_PERMISSION_PATH, "GET"):
            return queryset
        return queryset.filter(delegator=user)
