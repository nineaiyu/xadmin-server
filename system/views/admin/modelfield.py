#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : modelfield
# author : ly_13
# date : 1/5/2024

from django.apps import apps
from django.core.exceptions import FieldDoesNotExist
from django_filters import rest_framework as filters
from drf_spectacular.plumbing import build_array_type, build_basic_type, build_object_type
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.decorators import action

from common.base.utils import get_choices_dict
from common.core.filter import BaseFilterSet
from common.core.modelset import ImportExportDataAction, ListDeleteModelSet
from common.core.pagination import DynamicPageNumber
from common.core.response import ApiResponse
from common.swagger.utils import get_default_response_schema
from common.utils import get_logger
from system.models import ModelLabelField
from system.serializers.field import ModelLabelFieldImportSerializer, ModelLabelFieldSerializer
from system.utils.modelfield import (
    get_extra_field_lookups,
    get_field_lookup_info,
    get_field_meta,
    sync_model_field,
)
from system.utils.rule_meta import MATCH_TEXTS, RULE_TYPE_GROUP_TEXTS, RULE_TYPE_META, RULE_TYPE_TEXTS

logger = get_logger(__name__)


class ModelLabelFieldFilter(BaseFilterSet):
    pk = filters.UUIDFilter(field_name="id")
    name = filters.CharFilter(field_name="name", lookup_expr="icontains")
    label = filters.CharFilter(field_name="label", lookup_expr="icontains")
    parent = filters.CharFilter(field_name="parent", method="get_parent")

    def get_parent(self, queryset, name, value):
        if value == "0":
            return queryset.filter(parent=None)
        return queryset.filter(parent__id=value)

    class Meta:
        model = ModelLabelField
        fields = ["pk", "name", "label", "parent", "field_type", "created_time"]


class ModelLabelFieldViewSet(ListDeleteModelSet, ImportExportDataAction):
    """模型字段"""

    queryset = ModelLabelField.objects.all()
    serializer_class = ModelLabelFieldSerializer
    pagination_class = DynamicPageNumber(1000)
    import_data_serializer_class = ModelLabelFieldImportSerializer
    ordering_fields = ["created_time", "updated_time"]
    filterset_class = ModelLabelFieldFilter

    @extend_schema(
        responses=get_default_response_schema(
            {
                "choices_dict": build_object_type(
                    properties={
                        "key": build_array_type(
                            build_object_type(
                                properties={
                                    "value": build_basic_type(OpenApiTypes.STR),
                                    "label": build_basic_type(OpenApiTypes.STR),
                                }
                            )
                        )
                    }
                )
            }
        )
    )
    @action(methods=["get"], detail=False, url_path="choices")
    def choices_dict(self, request, *args, **kwargs):
        """获取{cls}字段选择。

        规则类型的配置端元数据（值控件形态 / 是否必填 / 建议匹配符 / 分组）在此下发，
        配置页不再各自硬编码「类型 → 控件」映射，新增规则类型只需改 rule_meta。
        """
        result = get_choices_dict(ModelLabelField.KeyChoices.choices)
        for item in result:
            # 规则类型只显示「注入什么值」不够，补一条过滤语义说明供配置页展示
            item["hint"] = RULE_TYPE_TEXTS.get(item["value"], "")
            item.update(RULE_TYPE_META.get(item["value"], {}))
        # matches：匹配符中文文案（与预览解码 / 规则摘要同源）
        return ApiResponse(choices_dict={"choices": result, "groups": RULE_TYPE_GROUP_TEXTS, "matches": MATCH_TEXTS})

    @extend_schema(
        parameters=[
            OpenApiParameter(name="table", required=True, type=str),
            OpenApiParameter(name="field", required=True, type=str),
        ],
        responses=get_default_response_schema({"data": build_array_type(build_basic_type(OpenApiTypes.STR))}),
    )
    @action(methods=["get"], detail=False, queryset=ModelLabelField.objects, filterset_class=None)
    def lookups(self, request, *args, **kwargs):
        """获取{cls}的字段名。

        返回该字段可用的匹配符（与读侧编译白名单同源）与字段形态元数据
        （field_meta：internal_type / 关联模型 / 是否多值），供配置页做控件适配与兼容性提示。
        """
        table = request.query_params.get("table")
        field = request.query_params.get("field")
        if table and field:
            if table == "*":
                table = "system.userinfo"
            obj = (
                self.filter_queryset(self.get_queryset())
                .filter(name=field, parent__name=table, parent__parent=None)
                .first()
            )
            if obj:
                try:
                    mt = apps.get_model(table)
                    mf = mt._meta.get_field(field) if mt else None
                except (LookupError, FieldDoesNotExist):
                    # 未注册模型 / 字段名不存在：按无可用匹配符处理，不抛 500
                    mf = None
                if mf:
                    lookups = list(mf.get_class_lookups().keys()) + get_extra_field_lookups(mf)
                    return ApiResponse(data=get_field_lookup_info(lookups), field_meta=get_field_meta(mf))
        return ApiResponse(code=1001)

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["get"], detail=False)
    def sync(self, request, *args, **kwargs):
        """同步{cls}的字段名"""
        return ApiResponse(data=sync_model_field())
