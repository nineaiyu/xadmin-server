#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""审批委托视图集（自 approval_flow.py 拆出，仅因文件行数门禁；行为与拆分前一致）。

委托只影响「待办归属」（生效委托用代理人替换原审批人），不改变节点定义；
解析语义见 system/utils/approval_flow/conditions.py::resolve_assignee_pairs 的委托展开。
"""

from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import OrderingFilter

from common.core.filter import BaseFilterSet
from common.core.modelset import BaseModelSet, SuggestionsAction
from system.models.approval import ApprovalDelegation
from system.serializers.approval_delegation import ApprovalDelegationSerializer


class ApprovalDelegationFilter(BaseFilterSet):
    class Meta:
        model = ApprovalDelegation
        fields = ["is_active", "delegator", "delegate"]


class ApprovalDelegationViewSet(BaseModelSet, SuggestionsAction):
    """审批委托（审批流三期）：委托人 × 代理人 × 生效时段 × 流程范围（空 = 全部流程）。

    只影响「待办归属」（生效委托用代理人替换原审批人），不改变节点定义。
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
