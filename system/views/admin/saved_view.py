#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""列表「我的视图」：CRUD + 默认视图互斥 + 共享可见。

- 个人级：`owner` = 当前用户，非本人（且非超管）不可改/删；
- 共享视图：`is_shared=True` 时同页其他用户可见（只读应用，避免改名冲突）；
- 默认视图：每人每页至多一个，保存 / 更新时互斥；
- 视图只存条件，应用时仍按当前用户权限裁剪（不越权）。
"""

from django.db.models import Q
from django.utils.translation import gettext_lazy as _
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.exceptions import PermissionDenied
from rest_framework.filters import OrderingFilter
from rest_framework.viewsets import GenericViewSet

from common.core.filter import BaseFilterSet
from common.core.modelset import (
    BaseViewSet,
    CreateAction,
    DestroyAction,
    DetailAction,
    ListAction,
    SearchColumnsAction,
    SearchFieldsAction,
    UpdateAction,
)
from common.core.response import ApiResponse
from system.models import SavedListView
from system.serializers.saved_view import SavedListViewSerializer


class SavedListViewFilter(BaseFilterSet):
    class Meta:
        model = SavedListView
        fields = ["page_key", "name", "is_default", "is_shared"]


class SavedListViewSet(
    BaseViewSet,
    CreateAction,
    UpdateAction,
    DetailAction,
    ListAction,
    SearchFieldsAction,
    SearchColumnsAction,
    DestroyAction,
    GenericViewSet,
):
    """列表视图"""

    queryset = SavedListView.objects.select_related("owner").all()
    serializer_class = SavedListViewSerializer
    filterset_class = SavedListViewFilter
    ordering = ["page_key", "-is_default", "created_time"]
    # 个人偏好资源：剥离默认数据权限过滤（默认拒绝会让本人视图不可见），
    # 取值域由 get_queryset 收口为「本人 + 共享」（同 PAT 个人凭证口径）
    filter_backends = (DjangoFilterBackend, OrderingFilter)

    def get_queryset(self):
        queryset = super().get_queryset()
        user = getattr(self.request, "user", None)
        if user is None or not getattr(user, "is_authenticated", False):
            return queryset.none()
        # 超管带 all=1 时看全量（管理排查用），否则只看本人 + 共享
        if getattr(user, "is_superuser", False) and self.request.query_params.get("all") == "1":
            return queryset
        return queryset.filter(Q(owner=user) | Q(is_shared=True))

    def _clear_other_defaults(self, user, page_key, keep_pk=None):
        queryset = SavedListView.objects.filter(owner=user, page_key=page_key, is_default=True)
        if keep_pk:
            queryset = queryset.exclude(pk=keep_pk)
        queryset.update(is_default=False)

    def perform_create(self, serializer):
        user = self.request.user
        page_key = serializer.validated_data.get("page_key") or ""
        if serializer.validated_data.get("is_default"):
            self._clear_other_defaults(user, page_key)
        serializer.save(owner=user, creator=user)

    def perform_update(self, serializer):
        instance = serializer.instance
        user = self.request.user
        if instance.owner_id != user.pk and not user.is_superuser:
            raise PermissionDenied(_("You can only modify your own views"))
        is_default = serializer.validated_data.get("is_default", instance.is_default)
        if is_default:
            self._clear_other_defaults(
                user, serializer.validated_data.get("page_key", instance.page_key), keep_pk=instance.pk
            )
        serializer.save(modifier=user)

    def perform_destroy(self, instance):
        user = self.request.user
        if instance.owner_id != user.pk and not user.is_superuser:
            raise PermissionDenied(_("You can only delete your own views"))
        return instance.delete()

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        self.perform_destroy(instance)
        return ApiResponse(detail=_("Deleted successfully"))

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        return ApiResponse(data=self.get_serializer(serializer.instance).data, detail=_("Saved successfully"))

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", True)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)
        return ApiResponse(data=self.get_serializer(serializer.instance).data, detail=_("Saved successfully"))
