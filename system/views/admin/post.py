#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""岗位（Post）视图：CRUD + 成员（用户）分配 + 选人候选 + 维度预览。

成员管理口径与群成员变更一致：``{add: [...], remove: [...]}`` 增量变更（幂等），
不提供整体替换（避免前端漏传即清空）；新增只接受在用用户，移除对不存在的关联静默跳过。

选人候选（``user-options``）为框架级「与父级 list 权限同口径」的子 action
（common/core/permission_meta.py 的 shared_list 注册表），无需独立权限点。
"""

from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils.translation import gettext_lazy as _
from django_filters import rest_framework as filters
from drf_spectacular.utils import extend_schema
from rest_framework.decorators import action

from common.core.filter import BaseFilterSet
from common.core.modelset import (
    BaseModelSet,
    BatchPartialUpdateAction,
    RecycleBinAction,
    RelationCountMixin,
)
from common.core.response import ApiResponse
from common.swagger.utils import get_default_response_schema
from common.utils import get_logger
from system.models import Post
from system.serializers.post import PostMemberSerializer, PostSerializer
from system.utils.identity.user_options import search_user_options
from system.utils.platform.modelset import PostPreviewAction

logger = get_logger(__name__)

#: 成员列表返回上限（名录展示用，超出按 pk 顺序截断）
MEMBER_LIMIT = 500


def _members_payload(post) -> list:
    """岗位成员简要信息（在用用户，按 pk 顺序稳定输出）。"""
    rows = post.users.filter(is_active=True).order_by("pk").only("pk", "username", "nickname")[:MEMBER_LIMIT]
    return [{"pk": row.pk, "username": row.username, "nickname": row.nickname} for row in rows]


class PostFilter(BaseFilterSet):
    name = filters.CharFilter(field_name="name", lookup_expr="icontains")
    code = filters.CharFilter(field_name="code", lookup_expr="icontains")

    class Meta:
        model = Post
        fields = ["name", "code", "is_active", "dept"]


class PostViewSet(RelationCountMixin, PostPreviewAction, BatchPartialUpdateAction, RecycleBinAction, BaseModelSet):
    """岗位"""

    queryset = Post.objects.all()
    serializer_class = PostSerializer
    filterset_class = PostFilter
    # 批量更新白名单：仅批量启停用（低风险字段，与部门同口径）
    batch_update_fields = ("is_active",)
    ordering_fields = ["rank", "name", "created_time"]

    def get_queryset(self):
        # 成员数注解由 RelationCountMixin 按声明完成；部门名逐行读取需预取
        return super().get_queryset().select_related("dept")

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["get"], detail=True, url_path="members")
    def members(self, request, *args, **kwargs):
        """岗位成员（查看）：在用用户清单（≤500）。"""
        post = self.get_object()
        return ApiResponse(data={"members": _members_payload(post)})

    @extend_schema(request=PostMemberSerializer, responses=get_default_response_schema())
    @action(methods=["post"], detail=True, url_path="assign")
    def assign(self, request, *args, **kwargs):
        """岗位成员分配：`{add, remove}` 增量变更（幂等）。

        新增只接受在用用户（失效/不存在的 pk 静默跳过，避免「部分失败」的半成品状态）；
        查看（members，GET）与分配（assign，POST）是两个独立权限点，便于「只读名录」授权。
        """
        from system.models import UserInfo

        post = self.get_object()
        serializer = PostMemberSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        add_pks = serializer.validated_data.get("add") or []
        remove_pks = serializer.validated_data.get("remove") or []
        try:
            if add_pks:
                post.users.add(*UserInfo.objects.filter(pk__in=add_pks, is_active=True))
            if remove_pks:
                post.users.remove(*UserInfo.objects.filter(pk__in=remove_pks))
        except (DjangoValidationError, ValueError, TypeError):
            # 非法主键形态：按可读参数错误返回（不落 500）
            return ApiResponse(code=1001, detail=_("Invalid member id"))
        return ApiResponse(data={"members": _members_payload(post)}, detail=_("Members updated"))

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["get"], detail=False, url_path="user-options")
    def user_options(self, request, *args, **kwargs):
        """成员候选：按关键字搜索在用用户（≤20 条，仅 pk/用户名/昵称）。

        与选人控件同源（system/utils/identity/user_options.py）；权限与 list 同口径
        （框架 shared_list 注册表，无需新增权限点）。
        """
        data = search_user_options(
            keyword=request.query_params.get("keyword", ""),
            pks=request.query_params.get("pks", ""),
        )
        return ApiResponse(data=data)
