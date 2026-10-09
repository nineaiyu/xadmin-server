#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""岗位（Post）视图：CRUD + 成员（用户）分配 + 选人候选 + 维度预览。

成员管理口径与群成员变更一致：``{add: [...], remove: [...]}`` 增量变更（幂等），
不提供整体替换（避免前端漏传即清空）；新增只接受在用用户，移除对不存在的关联静默跳过。

选人候选（``user-options``）为框架级「与父级 list 权限同口径」的子 action
（packages/xadmin-common/common/core/permission_meta.py 的 shared_list 注册表），无需独立权限点。
"""

from typing import Any

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
from identity.models import Post
from identity.serializers.post import PostMemberSerializer, PostSerializer
from identity.utils.user_options import search_user_options
from system.services.modelset import PostPreviewAction

logger = get_logger(__name__)

#: 成员列表返回上限（名录展示用，超出按 pk 顺序截断）
MEMBER_LIMIT = 500


def _members_payload(post: Any) -> tuple[list[Any], int]:
    """岗位成员简要信息与全量在用成员数（在用用户，按 pk 顺序稳定输出）。

    成员数超出 MEMBER_LIMIT 时仅返回前 MEMBER_LIMIT 条；调用方以 total/truncated
    向前端标记截断状态，避免「列表悄悄少了人」无提示。
    """
    users = post.users.filter(is_active=True)
    total = users.count()
    rows = users.order_by("pk").only("pk", "username", "nickname")[:MEMBER_LIMIT]
    return [{"pk": row.pk, "username": row.username, "nickname": row.nickname} for row in rows], total


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

    def get_queryset(self) -> Any:
        # 成员数注解由 RelationCountMixin 按声明完成；部门名逐行读取需预取
        return super().get_queryset().select_related("dept")

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["get"], detail=True, url_path="members")  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def members(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """岗位成员（查看）：在用用户清单（≤500，超出截断）。

        total 为全量在用成员数，truncated 标记本次是否截断（向后兼容新增字段）。
        """
        post = self.get_object()
        members, total = _members_payload(post)
        return ApiResponse(data={"members": members, "total": total, "truncated": total > len(members)})

    @extend_schema(request=PostMemberSerializer, responses=get_default_response_schema())
    @action(methods=["post"], detail=True, url_path="assign")  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def assign(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """岗位成员分配：`{add, remove}` 增量变更（幂等）。

        新增只接受在用用户（失效/不存在的 pk 跳过不阻断，跳过明细经 skipped
        返回，与 tags 批量打标的 failures 同口径）；查看（members，GET）与
        分配（assign，POST）是两个独立权限点，便于「只读名录」授权。
        """
        from identity.models import UserInfo

        post = self.get_object()
        serializer = PostMemberSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        add_pks = serializer.validated_data.get("add") or []
        remove_pks = serializer.validated_data.get("remove") or []
        skipped: list[str] = []
        try:
            if add_pks:
                matched = list(UserInfo.objects.filter(pk__in=add_pks, is_active=True))
                skipped = sorted({str(pk) for pk in add_pks} - {str(user.pk) for user in matched})
                post.users.add(*matched)
            if remove_pks:
                post.users.remove(*UserInfo.objects.filter(pk__in=remove_pks))
        except (DjangoValidationError, ValueError, TypeError):
            # 非法主键形态：按可读参数错误返回（不落 500）
            return ApiResponse(code=1001, detail=_("Invalid member id"))
        members, total = _members_payload(post)
        return ApiResponse(
            data={"members": members, "total": total, "truncated": total > len(members), "skipped": skipped},
            detail=_("Members updated"),
        )

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["get"], detail=False, url_path="user-options")  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def user_options(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """成员候选：按关键字搜索在用用户（≤20 条，仅 pk/用户名/昵称）。

        与选人控件同源（identity/utils/user_options.py）；权限与 list 同口径
        （框架 shared_list 注册表，无需新增权限点）。
        """
        data = search_user_options(
            keyword=request.query_params.get("keyword", ""),
            pks=request.query_params.get("pks", ""),
        )
        return ApiResponse(data=data)
