#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""数据字典管理：类型/字典项两级维护 + 状态/排序/缓存维护 + items 消费接口。"""

from django.db.models import Case, IntegerField, Value, When
from django.utils.translation import gettext_lazy as _
from django_filters import rest_framework as filters
from drf_spectacular.plumbing import build_array_type, build_basic_type, build_object_type
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, OpenApiRequest, extend_schema
from rest_framework import serializers
from rest_framework.decorators import action

from common.core.modelset import (
    BaseModelSet,
    BatchPartialUpdateAction,
    ImpactPreviewAction,
    ImportExportDataAction,
    RelationCountMixin,
)
from common.core.response import ApiResponse
from common.swagger.utils import get_default_response_schema
from system.models.dict import DataDict
from system.serializers.dict import DataDictSerializer
from system.utils.dict import get_dict_items, invalid_dict_cache


class DataDictFilter(filters.FilterSet):
    """字典过滤：code/label 模糊 + 类型（父编码或父主键）+ 启用状态。"""

    code = filters.CharFilter(field_name="code", lookup_expr="icontains")
    label = filters.CharFilter(field_name="label", lookup_expr="icontains")
    parent_code = filters.CharFilter(field_name="parent__code", label=_("Parent code"))
    # 只看字典类型（parent 为空）/ 只看字典项：前端「全部类型」切换与子项下钻用
    is_type = filters.BooleanFilter(field_name="parent", lookup_expr="isnull", label=_("Is dict type"))

    class Meta:
        model = DataDict
        fields = ["code", "label", "parent", "parent_code", "is_active", "is_type"]


class DataDictViewSet(
    BatchPartialUpdateAction,
    RelationCountMixin,
    BaseModelSet,
    ImpactPreviewAction,
    ImportExportDataAction,
):
    """数据字典管理"""

    # F-1 批量更新白名单：批量启停用
    batch_update_fields = ("is_active",)

    queryset = DataDict.objects.all()
    serializer_class = DataDictSerializer
    filterset_class = DataDictFilter
    ordering_fields = ["sort", "created_time", "updated_time"]

    def perform_destroy(self, instance):
        """内置字典（被代码按 code 引用）禁止删除，避免业务字段选项凭空消失。"""
        if instance.is_locked:
            raise serializers.ValidationError({"is_locked": _("Locked dict cannot be deleted")})
        return super().perform_destroy(instance)

    @extend_schema(
        request=OpenApiRequest(
            build_object_type(
                properties={"pks": build_array_type(build_basic_type(OpenApiTypes.STR))},
                required=["pks"],
            )
        ),
        responses=get_default_response_schema(),
    )
    @action(methods=["post"], detail=False, url_path="batch-destroy")
    def batch_destroy(self, request, *args, **kwargs):
        """批量删除：内置字典静默排除，不因单条受保护而整批失败。

        ⚠️ 覆写基类 `BatchDestroyAction.batch_destroy` 必须保留 `@action`
        装饰器（DRF 靠其 `.mapping` 注册路由），否则端点丢失、请求 405。
        """
        self.queryset = self.queryset.filter(is_locked=False)
        return super().batch_destroy(request, *args, **kwargs)

    @extend_schema(
        parameters=[OpenApiParameter(name="code", required=True, type=OpenApiTypes.STR)],
        responses=get_default_response_schema(
            {
                "results": build_array_type(
                    build_object_type(
                        properties={
                            "label": build_basic_type(OpenApiTypes.STR),
                            "value": build_basic_type(OpenApiTypes.STR),
                            "color": build_basic_type(OpenApiTypes.STR),
                        }
                    )
                )
            }
        ),
    )
    @action(methods=["get"], detail=False, url_path="items")
    def items(self, request, *args, **kwargs):
        """按字典类型 code 取启用字典项"""
        code = request.query_params.get("code")
        if not code:
            return ApiResponse(code=400, detail=_("Dict code is required"))
        return ApiResponse(data={"results": get_dict_items(code)})

    @extend_schema(
        request=build_object_type(
            properties={
                "pks": build_array_type(build_basic_type(OpenApiTypes.STR)),
                "is_active": build_basic_type(OpenApiTypes.BOOL),
            }
        ),
        responses=get_default_response_schema(),
    )
    @action(methods=["post"], detail=False, url_path="batch-active")
    def batch_active(self, request, *args, **kwargs):
        """批量启用或停用字典

        body: {"pks": [...], "is_active": bool}；is_active 省略时按各行当前状态取反。
        逐个 save 而非 queryset.update：字典缓存失效挂在 post_save 信号上，批量
        update 会绕过信号，导致消费端最长 5 分钟拿不到新状态。
        """
        data = request.data if isinstance(request.data, dict) else {}
        pks = data.get("pks") or []
        is_active = data.get("is_active")
        queryset = self.filter_queryset(self.get_queryset()).filter(pk__in=pks)
        count = 0
        for instance in queryset:
            instance.is_active = (not instance.is_active) if is_active is None else bool(is_active)
            instance.save()
            count += 1
        return ApiResponse(detail=_("Batch update submitted: {} success").format(count))

    @extend_schema(
        request=build_object_type(
            properties={
                "direction": build_basic_type(OpenApiTypes.STR),
            },
            required=["direction"],
        ),
        responses=get_default_response_schema(),
    )
    @action(methods=["post"], detail=True, url_path="move")
    def move(self, request, *args, **kwargs):
        """同层内上移/下移一位
        （重排同层 sort，列表与消费端均按 sort 升序）
        body: {"direction": "up" | "down"}。已在边界时不做变更并正常返回（幂等）。
        """
        instance = self.get_object()
        direction = (request.data or {}).get("direction") if isinstance(request.data, dict) else None
        if direction not in ("up", "down"):
            return ApiResponse(code=1001, detail=_("Operation failed. Abnormal data"))
        pks = list(
            DataDict.objects.filter(parent=instance.parent_id)
            .order_by("sort", "created_time")
            .values_list("pk", flat=True)
        )
        index = pks.index(instance.pk)
        target = index - 1 if direction == "up" else index + 1
        if 0 <= target < len(pks):
            pks[index], pks[target] = pks[target], pks[index]
            # Case/When 单条 SQL 落库（同 RankAction）；不触发信号，缓存手动失效
            DataDict.objects.filter(pk__in=pks).update(
                sort=Case(
                    *[When(pk=pk, then=Value(order)) for order, pk in enumerate(pks)],
                    output_field=IntegerField(),
                )
            )
            invalid_dict_cache(instance.parent.code if instance.parent_id else None)
        return ApiResponse(detail=_("Sorting saved successfully"))

    @extend_schema(request=None, responses=get_default_response_schema())
    @action(methods=["post"], detail=False, url_path="refresh-cache")
    def refresh_cache(self, request, *args, **kwargs):
        """清空全部字典缓存
        直连改库等绕过信号的场景手动触发立即生效。
        """
        invalid_dict_cache()
        return ApiResponse(detail=_("Operation successful"))
