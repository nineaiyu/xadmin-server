#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""审批规则（多级审批链配置）：CRUD。

规则的消费端在 system/utils/approval/chains.py::resolve_rule —— 命中路径的请求
在建单时展开为多级审批链（逐级通知与推进）；未命中时回退全局审批人逻辑。
规则改动只影响之后新建的审批单（在途单按建单快照推进）。
"""

from django.db.models import Count
from django_filters import rest_framework as filters
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema
from rest_framework.decorators import action
from rest_framework.filters import OrderingFilter

from common.core.filter import BaseFilterSet
from common.core.modelset import BaseModelSet
from common.core.response import ApiResponse
from common.swagger.utils import get_default_response_schema
from system.models import UserInfo, UserRole
from system.models.approval_rule import ApprovalRule
from system.serializers.approval_rule import ApprovalRuleSerializer

CANDIDATE_LIMIT = 1000


class ApprovalRuleFilter(BaseFilterSet):
    name = filters.CharFilter(field_name="name", lookup_expr="icontains")
    remark = filters.CharFilter(field_name="remark", lookup_expr="icontains")

    class Meta:
        model = ApprovalRule
        fields = ["name", "is_active", "priority", "created_time"]


class ApprovalRuleViewSet(BaseModelSet):
    """审批规则"""

    queryset = ApprovalRule.objects.all()
    serializer_class = ApprovalRuleSerializer
    filterset_class = ApprovalRuleFilter
    filter_backends = (DjangoFilterBackend, OrderingFilter)
    ordering = ["-priority", "-created_time"]
    ordering_fields = ["created_time", "priority", "name"]
    select_related_fields = ("creator",)
    prefetch_related_fields = ("levels",)

    def get_queryset(self):
        # level_count 走 annotate 而非逐行 count（列表 N+1）；annotate 会清掉
        # Meta.ordering，需显式补回（与审批流程定义列表同口径）
        return super().get_queryset().annotate(levels_count=Count("levels")).order_by(*ApprovalRule._meta.ordering)

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["get"], detail=False, url_path="candidate-options")
    def candidate_options(self, request, *args, **kwargs):
        """审批人候选目录：启用用户 + 启用角色（审批模块自给自足，不依赖搜索模块）。

        配置审批人是审批模块的核心操作：全局搜索（/api/system/search/user）属于
        独立可裁剪模块，standard 预设下被禁用会让审批人下拉永远搜不到、退化为
        手填——故在本模块内提供目录端点（单次拉取 + 前端本地过滤，管理员低频操作）。
        """
        users = list(
            UserInfo.objects.filter(is_active=True)
            .order_by("username")
            .values("pk", "username", "nickname")[:CANDIDATE_LIMIT]
        )
        roles = list(
            UserRole.objects.filter(is_active=True, deleted_at__isnull=True).order_by("code").values("code", "name")
        )
        return ApiResponse(data={"users": users, "roles": roles, "truncated": len(users) >= CANDIDATE_LIMIT})
