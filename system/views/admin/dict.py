#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""数据字典管理：类型/字典项两级维护 + items 消费接口。"""

from django.utils.translation import gettext_lazy as _
from django_filters import rest_framework as filters
from drf_spectacular.utils import extend_schema, OpenApiParameter
from drf_spectacular.plumbing import build_array_type, build_basic_type, build_object_type
from drf_spectacular.types import OpenApiTypes
from rest_framework.decorators import action

from common.core.modelset import BaseModelSet, ImportExportDataAction
from common.core.response import ApiResponse
from common.swagger.utils import get_default_response_schema
from system.models.dict import DataDict
from system.serializers.dict import DataDictSerializer
from system.utils.dict import get_dict_items


class DataDictFilter(filters.FilterSet):
    """字典过滤：code/label 模糊 + 类型（父 code）+ 启用状态。"""

    code = filters.CharFilter(field_name="code", lookup_expr="icontains")
    label = filters.CharFilter(field_name="label", lookup_expr="icontains")
    parent_code = filters.CharFilter(field_name="parent__code", label=_("Parent code"))

    class Meta:
        model = DataDict
        fields = ["code", "label", "parent", "parent_code", "is_active"]


class DataDictViewSet(BaseModelSet, ImportExportDataAction):
    """数据字典管理"""

    queryset = DataDict.objects.all()
    serializer_class = DataDictSerializer
    filterset_class = DataDictFilter
    ordering_fields = ["sort", "created_time"]

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
        """按字典类型 code 取启用字典项（带缓存，供下拉/表单消费）"""
        code = request.query_params.get("code")
        if not code:
            return ApiResponse(code=400, detail=_("Dict code is required"))
        return ApiResponse(data={"results": get_dict_items(code)})
