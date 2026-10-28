#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""审批委托视图集（自 approval_flow.py 拆出，仅因文件行数门禁；行为与拆分前一致）。

委托只影响「待办归属」（生效委托用代理人替换原审批人），不改变节点定义；
解析语义见 approval/services/approval_flow/conditions.py::resolve_assignee_pairs 的委托展开。
"""

from typing import Any

from django.db.models import Q
from django.utils.translation import gettext_lazy as _
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.filters import OrderingFilter

from approval.models.approval import ApprovalDelegation
from approval.models.approval_instance import ApprovalInstance, ApprovalNodeTask
from approval.serializers.approval_delegation import ApprovalDelegationSerializer
from common.core.filter import BaseFilterSet
from common.core.modelset import BaseModelSet, SuggestionsAction
from common.core.permission import user_has_permission
from common.core.response import ApiResponse
from common.swagger.utils import get_default_response_schema

#: 「查看全部委托记录」管理视角的权限点 path（无独立路由的功能授权，登记于 loadjson/menu.json）
ALL_DELEGATIONS_PERMISSION_PATH = "api/approval/approval-delegations/all$"


class ApprovalDelegationFilter(BaseFilterSet):
    class Meta:
        model = ApprovalDelegation
        fields = ["is_active", "delegator", "delegate"]


class ApprovalDelegationViewSet(BaseModelSet, SuggestionsAction):
    """审批委托

    维度：委托人 × 代理人 × 生效时段 × 流程范围（空 = 全部流程）。
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

    def perform_destroy(self, instance: Any) -> Any:
        """销毁归属护栏：委托记录属个人审批权处置，仅委托人本人或超管可删。

        「查看全部委托」授权（all 码）只放大读取面（get_queryset 的可见域），
        写/删仍收敛到本人——否则持管理视角码的角色可硬删他人生效委托，
        静默拆掉他人的代审权。更新路径已有序列化器 delegator 归属校验，
        销毁不走序列化器，在此单独收敛。
        """
        user = self.request.user
        if not getattr(user, "is_superuser", False) and instance.delegator_id != user.pk:
            raise PermissionDenied(_("Only the delegator can delete a delegation"))
        super().perform_destroy(instance)

    def _needs_rowwise_delete(self) -> Any:
        """批量删除逐行：perform_destroy 的归属护栏只在逐行分支可达（覆写契约）。"""
        return True

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["get"], detail=True, url_path="impact")  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def impact(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """委托停用影响面

        在途流程实例当前节点挂在该委托人或代理人名下的待办任务（与委托展开同一实体：
        引擎的唯一消费点是流程节点候选人替换，见 conditions.py::_expand_delegations）。
        停用委托不会自动改派既有待办（建单/进节点时已按快照分配），这里只把「哪些在途
        待办会受影响」显式列出，供停用前确认；代理人名下只统计经本委托人代审而来的任务
        （delegate_from = 委托人）。
        """
        delegation = self.get_object()
        pending = ApprovalNodeTask.objects.filter(
            status=ApprovalNodeTask.Status.PENDING,
            instance__status=ApprovalInstance.Status.PENDING,
        ).filter(Q(assignee=delegation.delegator) | Q(assignee=delegation.delegate, delegate_from=delegation.delegator))
        total = pending.count()
        rows = pending.select_related("instance", "instance__flow", "instance__creator").order_by("-created_time")[:20]
        return ApiResponse(
            data={
                "total": total,
                "delegator_pending": pending.filter(assignee=delegation.delegator).count(),
                "delegate_pending": pending.filter(assignee=delegation.delegate).count(),
                "items": [
                    {
                        "pk": str(task.instance_id),
                        "task_pk": str(task.pk),
                        "node_name": task.node_name,
                        "title": task.instance.title or "",
                        "flow": task.instance.flow.name if task.instance.flow_id else "",
                        "applicant": task.instance.creator.username if task.instance.creator_id else "",
                    }
                    for task in rows
                ],
                "note": str(_("Existing assignments stay unchanged after disabling")),
            }
        )
