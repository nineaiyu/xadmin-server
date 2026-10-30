#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""分析域视图公共基类与可见性/归属守卫。"""

from typing import Any

from django.core.exceptions import FieldDoesNotExist, FieldError
from django.db.models import Q
from django.utils.translation import gettext_lazy as _
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import OrderingFilter

from common.core.modelset import BaseModelSet
from common.core.response import ApiResponse

_EDIT_DENY = _("Only the creator can modify it")


def _visible_queryset(viewset: Any) -> Any:
    """两档可见性：personal 仅创建者；shared 全员（superuser 绕过）。

    ``visibility`` 是数据集 / 看板 / 大屏的共享维度；报表等无该字段的资源配置
    不做可见性收敛（定义类资源本就豁免行级数据权限，写保护由 ``_creator_guard``
    承担）——不可无条件按 ``visibility`` 过滤，否则非超管访问该类资源直接 500。
    """
    queryset = viewset.queryset
    user = viewset.request.user
    if getattr(user, "is_superuser", False):
        return queryset
    if not _supports_visibility(queryset.model):
        return queryset
    return queryset.filter(Q(visibility="shared") | Q(creator=user))


def _supports_visibility(model: Any) -> bool:
    """模型是否具备可见性维度（`visibility` 字段）。

    ``_meta.get_field`` 对缺失字段抛 ``FieldDoesNotExist``（不是 ``FieldError``
    的子类），两者都要纳入兜底。
    """
    try:
        model._meta.get_field("visibility")
    except (FieldError, FieldDoesNotExist):
        return False
    return True


def _creator_guard(request: Any, instance: Any) -> Any:
    if instance and not getattr(request.user, "is_superuser", False) and instance.creator_id != request.user.pk:
        return ApiResponse(code=1003, detail=_EDIT_DENY)
    return None


class BaseAnalysisViewSet(BaseModelSet):
    """分析域视图基类

    可见性过滤 + 创建者写保护 + 定义类资源豁免行级数据权限
    """

    filter_backends = [DjangoFilterBackend, OrderingFilter]

    def get_queryset(self) -> Any:
        return _visible_queryset(self)

    def perform_create(self, serializer: Any) -> None:
        serializer.save(creator=self.request.user, modifier=self.request.user)

    def update(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        partial = kwargs.pop("partial", False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        guarded = _creator_guard(request, serializer.instance)
        if guarded:
            return guarded
        self.perform_update(serializer)
        return ApiResponse(data=serializer.data)

    def destroy(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        instance = self.get_object()
        guarded = _creator_guard(request, instance)
        if guarded:
            return guarded
        return super().destroy(request, *args, **kwargs)
