#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""审批规则（多级审批链配置）：CRUD。

规则的消费端在 approval/services/approval/chains.py::resolve_rule —— 命中路径的请求
在建单时展开为多级审批链（逐级通知与推进）；未命中时回退全局审批人逻辑。
规则改动只影响之后新建的审批单（在途单按建单快照推进）。
"""

import re
from typing import Any

from django.db.models import Count, Q
from django.utils.translation import gettext_lazy as _
from django_filters import rest_framework as filters
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.plumbing import build_array_type, build_basic_type, build_object_type
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiRequest, extend_schema
from rest_framework.decorators import action
from rest_framework.filters import OrderingFilter

from approval.models.approval_request import ApprovalRequest
from approval.models.approval_rule import ApprovalRule
from approval.serializers.approval_rule import ApprovalRuleSerializer
from approval.services.approval.chains import resolve_rule
from common.core.filter import BaseFilterSet
from common.core.modelset import BaseModelSet
from common.core.response import ApiResponse
from common.swagger.utils import get_default_response_schema
from identity.services import Post, UserInfo, UserRole

CANDIDATE_LIMIT = 1000


def build_candidate_options(keyword: str = "", limit: int = CANDIDATE_LIMIT) -> dict[str, Any]:
    """审批人候选目录（规则页与流程设计器共用）：支持关键字远程搜索。

    无关键字返回前 limit 个用户并回传 total/truncated；带关键字时按用户名/昵称
    模糊过滤，避免 1000 人上限导致后段用户永远选不到。
    """
    users_qs = UserInfo.objects.filter(is_active=True)
    query = str(keyword or "").strip()
    if query:
        users_qs = users_qs.filter(Q(username__icontains=query) | Q(nickname__icontains=query))
    total = users_qs.count()
    users = list(
        users_qs.order_by("username").values("pk", "username", "nickname")[: max(1, min(limit, CANDIDATE_LIMIT))]
    )
    roles = list(
        UserRole.objects.filter(is_active=True, deleted_at__isnull=True).order_by("code").values("code", "name")
    )
    posts = list(Post.objects.filter(is_active=True, deleted_at__isnull=True).order_by("code").values("code", "name"))
    return {
        "users": users,
        "roles": roles,
        "posts": posts,
        "total_users": total,
        "truncated": total > len(users),
    }


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

    def get_queryset(self) -> Any:
        # level_count 走 annotate 而非逐行 count（列表 N+1）；annotate 会清掉
        # Meta.ordering，需显式补回（与审批流程定义列表同口径）
        return super().get_queryset().annotate(levels_count=Count("levels")).order_by(*ApprovalRule._meta.ordering)

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["get"], detail=False, url_path="candidate-options")  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def candidate_options(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """审批人候选目录

        启用用户 + 启用角色 + 启用岗位（审批模块自给自足，不依赖搜索模块）

        配置审批人是审批模块的核心操作：全局搜索（/api/identity/search/user）属于
        独立可裁剪模块，standard 预设下被禁用会让审批人下拉永远搜不到、退化为
        手填——故在本模块内提供目录端点（单次拉取 + 前端本地过滤，管理员低频操作）。
        岗位仅作为人员维度参与审批人解析（不参与权限判定）。
        """
        keyword = str(request.query_params.get("keyword") or "").strip()
        try:
            limit = int(request.query_params.get("limit") or CANDIDATE_LIMIT)
        except (TypeError, ValueError):
            limit = CANDIDATE_LIMIT
        return ApiResponse(data=build_candidate_options(keyword=keyword, limit=limit))

    @extend_schema(
        request=OpenApiRequest(
            build_object_type(
                properties={
                    "pk": build_basic_type(OpenApiTypes.STR),
                    "path_patterns": build_array_type(build_basic_type(OpenApiTypes.STR) or {}),
                    "methods": build_array_type(build_basic_type(OpenApiTypes.STR) or {}),
                    "priority": build_basic_type(OpenApiTypes.INT),
                    "is_active": build_basic_type(OpenApiTypes.BOOL),
                },
                description="传 pk 预览已保存规则；也可直接传草稿字段（编辑保存前预览）",
            )
        ),
        responses=get_default_response_schema(),
    )
    @action(methods=["post"], detail=False, url_path="impact-preview")  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def impact_preview(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """预览规则命中的在途审批单

        只读；历史单按建单快照，不受规则改动影响。命中口径与 resolve_rule 一致（路径 re.search + 方法，HEAD 归一 GET）；
        并给出当前生效规则，帮助管理员判断优先级是否会被更高优规则覆盖。
        """
        payload = request.data if isinstance(request.data, dict) else {}
        rule = None
        if payload.get("pk"):
            rule = self.get_queryset().filter(pk=payload["pk"]).first()
        patterns = payload.get("path_patterns")
        methods = payload.get("methods")
        if rule is not None:
            patterns = patterns if patterns is not None else rule.path_patterns
            methods = methods if methods is not None else rule.methods
        patterns = [str(item) for item in (patterns or []) if str(item).strip()]
        methods = [str(item).strip().upper() for item in (methods or []) if str(item).strip()]
        try:
            priority = int(payload.get("priority", getattr(rule, "priority", 0)) or 0)
        except (TypeError, ValueError):
            return ApiResponse(code=1004, detail=_("Operation failed. Abnormal data"))
        is_active = bool(payload.get("is_active", getattr(rule, "is_active", True)))
        exclude_pk = getattr(rule, "pk", None)
        items: list[dict[str, Any]] = []
        total = 0
        if patterns and is_active:
            queryset = ApprovalRequest.objects.filter(status=ApprovalRequest.Status.PENDING).select_related("creator")
            if methods:
                normalized = {"GET" if item == "HEAD" else item for item in methods}
                queryset = queryset.filter(method__in=normalized)
            for approval in queryset.order_by("-created_time").iterator(chunk_size=200):
                if not any(_safe_search(pattern, approval.path) for pattern in patterns):
                    continue
                total += 1
                if len(items) >= 50:
                    continue
                current = resolve_rule(approval.path, approval.method, exclude_pk=exclude_pk)
                current_priority = int(current.priority) if current is not None else -1
                draft_wins = current is None or priority > current_priority or (priority == current_priority)
                items.append(
                    {
                        "pk": str(approval.pk),
                        "path": approval.path,
                        "method": approval.method,
                        "module": approval.module or "",
                        "applicant": getattr(approval.creator, "username", "") or "",
                        "created_time": approval.created_time,
                        "current_rule": getattr(current, "name", "") or "",
                        "current_priority": current_priority,
                        "draft_wins": draft_wins,
                    }
                )
        return ApiResponse(
            data={
                "total": total,
                "items": items,
                "truncated": total > len(items),
                "rule_active": is_active,
            }
        )


def _safe_search(pattern: str, path: str) -> bool:
    try:
        return bool(re.search(pattern, path))
    except re.error:
        return False
