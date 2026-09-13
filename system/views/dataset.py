#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""数据集与仪表盘视图（ADR-020）。

数据集：CRUD + `columns`（设计器元数据）/ `execute`（行数据，数据权限过滤）/
`aggregate`（聚合序列，图表数据源）。执行动作必须经权限菜单节点访问
（非超管的 user.menu 上下文由 IsAuthenticated 写入）。

仪表盘：CRUD，可见性两档——personal 仅创建者，shared 全员只读；
更新/删除仅创建者与超管（对象级校验）。
"""

from django.core.exceptions import ValidationError
from django.db.models import Q
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import OrderingFilter
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.decorators import action

from common.core.modelset import BaseModelSet
from common.core.response import ApiResponse
from common.swagger.utils import get_default_response_schema
from common.utils import get_logger
from system.models.dataset import Dashboard, Dataset
from system.serializers.dataset import DashboardSerializer, DatasetSerializer
from system.utils.dataset import aggregate_dataset, available_fields, available_models, execute_dataset

logger = get_logger(__name__)


class DatasetViewSet(BaseModelSet):
    """数据集"""

    queryset = Dataset.objects.all()
    serializer_class = DatasetSerializer
    ordering = ["-created_time"]
    # 定义类资源不做行级数据权限过滤（可见性语义 = 创建者/共享，见 get_queryset）；
    # 数据权限作用于数据集执行的**业务数据**，而非数据集定义本身
    filter_backends = [DjangoFilterBackend, OrderingFilter]

    def get_queryset(self):
        queryset = super().get_queryset()
        user = self.request.user
        if getattr(user, "is_superuser", False):
            return queryset
        # 两档可见性：personal 仅创建者；shared 全员（含创建者）可读
        return queryset.filter(Q(visibility=Dataset.Visibility.SHARED) | Q(creator=user))

    def _perform_write_guard(self, serializer):
        """非创建者不得修改/删除他人数据集（共享只读）。"""
        instance = getattr(serializer, "instance", None)
        user = self.request.user
        if instance and not getattr(user, "is_superuser", False) and instance.creator_id != user.pk:
            return ApiResponse(code=1003, detail=_("Only the creator can modify a dataset"))
        return None

    def perform_create(self, serializer):
        serializer.save(creator=self.request.user, modifier=self.request.user)

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        guarded = self._perform_write_guard(serializer)
        if guarded:
            return guarded
        self.perform_update(serializer)
        return ApiResponse(data=serializer.data)

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        if not getattr(request.user, "is_superuser", False) and instance.creator_id != request.user.pk:
            return ApiResponse(code=1003, detail=_("Only the creator can modify a dataset"))
        return super().destroy(request, *args, **kwargs)

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["get"], detail=False, url_path="meta")
    def meta(self, request, *args, **kwargs):
        """设计器元数据：模型白名单与字段清单。"""
        models = available_models()
        return ApiResponse(
            data={
                "models": models,
                "fields": {name: available_fields(name) for name in models},
            }
        )

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["post"], detail=True, url_path="execute")
    def execute(self, request, *args, **kwargs):
        """执行数据集（行级数据权限随调用者过滤，fail-closed）。"""
        dataset = self.get_object()
        try:
            result = execute_dataset(dataset, request.user)
        except ValidationError as exc:
            return ApiResponse(code=1001, detail="; ".join(exc.messages), status_code=status.HTTP_400_BAD_REQUEST)
        return ApiResponse(data=result)

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["post"], detail=True, url_path="aggregate")
    def aggregate(self, request, *args, **kwargs):
        """聚合序列（图表数据源）：group_by + metric(+value_field) + date_trunc。"""
        dataset = self.get_object()
        try:
            result = aggregate_dataset(
                dataset,
                request.user,
                group_by=request.data.get("group_by"),
                metric=request.data.get("metric", "count"),
                date_trunc=request.data.get("date_trunc"),
                value_field=request.data.get("value_field"),
            )
        except ValidationError as exc:
            return ApiResponse(code=1001, detail="; ".join(exc.messages), status_code=status.HTTP_400_BAD_REQUEST)
        return ApiResponse(data=result)


class DashboardViewSet(BaseModelSet):
    """仪表盘"""

    queryset = Dashboard.objects.all()
    serializer_class = DashboardSerializer
    ordering = ["-created_time"]
    # 同 Dataset：可见性语义替代行级数据权限
    filter_backends = [DjangoFilterBackend, OrderingFilter]

    def get_queryset(self):
        queryset = super().get_queryset()
        user = self.request.user
        if getattr(user, "is_superuser", False):
            return queryset
        return queryset.filter(Q(visibility=Dashboard.Visibility.SHARED) | Q(creator=user))

    def perform_create(self, serializer):
        serializer.save(creator=self.request.user, modifier=self.request.user)

    def _ownership_guard(self, instance):
        user = self.request.user
        if instance and not getattr(user, "is_superuser", False) and instance.creator_id != user.pk:
            # 共享仪表盘对非创建者只读
            return ApiResponse(code=1003, detail=_("Only the creator can modify a dashboard"))
        return None

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        guarded = self._ownership_guard(serializer.instance)
        if guarded:
            return guarded
        self.perform_update(serializer)
        return ApiResponse(data=serializer.data)

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        guarded = self._ownership_guard(instance)
        if guarded:
            return guarded
        return super().destroy(request, *args, **kwargs)
