#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""代码生成方案：CRUD + 个人取值域（本人 ∪ 共享）。

- 个人级：``creator`` = 当前用户，非本人不可改 / 删；
- 共享方案：``is_shared=True`` 时其他用户可见并只读应用；
- 同名保存覆盖：同一用户重复保存同名方案时原地更新，不产生重复行
  （沿用前端「同名覆盖」语义，故模型不加唯一约束、由本视图收敛）。
"""

from typing import Any

from django.db.models import Q
from django.utils.translation import gettext_lazy as _
from rest_framework.exceptions import PermissionDenied
from rest_framework.filters import OrderingFilter
from rest_framework.viewsets import GenericViewSet

from common.core.modelset import (
    BaseViewSet,
    CreateAction,
    DestroyAction,
    ListAction,
    UpdateAction,
)
from common.core.response import ApiResponse
from system.models import CodegenPlan
from system.serializers.codegen_plan import CodegenPlanSerializer


class CodegenPlanViewSet(
    BaseViewSet,
    CreateAction,
    UpdateAction,
    ListAction,
    DestroyAction,
    GenericViewSet,
):
    """代码生成方案"""

    queryset = CodegenPlan.objects.select_related("creator").all()
    serializer_class = CodegenPlanSerializer
    ordering = ["-updated_time"]
    # 个人偏好资源：剥离默认数据权限过滤（默认拒绝会让本人方案不可见），
    # 取值域由 get_queryset 收口为「本人 + 共享」
    filter_backends = (OrderingFilter,)

    def get_queryset(self) -> Any:
        queryset = super().get_queryset()
        user = getattr(self.request, "user", None)
        if user is None or not getattr(user, "is_authenticated", False):
            return queryset.none()
        # 超管带 all=1 时看全量（管理排查用），否则只看本人 + 共享
        if getattr(user, "is_superuser", False) and self.request.query_params.get("all") == "1":
            return queryset
        return queryset.filter(Q(creator=user) | Q(is_shared=True))

    def _assert_owner(self, instance: Any) -> None:
        user = self.request.user
        if instance.creator_id != user.pk and not user.is_superuser:
            raise PermissionDenied(_("You can only modify your own codegen plans"))

    def create(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = request.user
        name = serializer.validated_data.get("name") or ""
        existing = CodegenPlan.objects.filter(creator=user, name=name).first()
        if existing is not None:
            serializer.instance = existing
            serializer.save(modifier=user)
        else:
            serializer.save(creator=user, modifier=user)
        return ApiResponse(data=self.get_serializer(serializer.instance).data, detail=_("Saved successfully"))

    def update(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        partial = kwargs.pop("partial", True)
        instance = self.get_object()
        self._assert_owner(instance)
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        serializer.save(modifier=request.user)
        return ApiResponse(data=self.get_serializer(serializer.instance).data, detail=_("Saved successfully"))

    def destroy(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        instance = self.get_object()
        self._assert_owner(instance)
        instance.delete()
        return ApiResponse(detail=_("Deleted successfully"))
