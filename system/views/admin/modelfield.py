#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : modelfield
# author : ly_13
# date : 1/5/2024

import logging

from django.apps import apps
from django.conf import settings
from django.utils.translation import activate
from django_filters import rest_framework as filters
from drf_spectacular.plumbing import build_object_type, build_basic_type, build_array_type
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema, OpenApiParameter
from rest_framework.decorators import action

from common.base.utils import get_choices_dict
from common.core.filter import BaseFilterSet
from common.core.modelset import OnlyListModelSet
from common.core.pagination import DynamicPageNumber
from common.core.response import ApiResponse
from common.core.serializers import get_sub_serializer_fields
from common.swagger.utils import get_default_response_schema
from system.models import ModelLabelField
from system.serializers.field import ModelLabelFieldSerializer

logger = logging.getLogger(__name__)


class ModelLabelFieldFilter(BaseFilterSet):
    pk = filters.UUIDFilter(field_name='id')
    name = filters.CharFilter(field_name='name', lookup_expr='icontains')
    label = filters.CharFilter(field_name='label', lookup_expr='icontains')
    parent = filters.CharFilter(field_name='parent', method='get_parent')

    def get_parent(self, queryset, name, value):
        if value == "0":
            return queryset.filter(parent=None)
        return queryset.filter(parent__id=value)

    class Meta:
        model = ModelLabelField
        fields = ['pk', 'name', 'label', 'parent', 'field_type']


class ModelLabelFieldView(OnlyListModelSet):
    """模型字段管理"""
    queryset = ModelLabelField.objects.all()
    serializer_class = ModelLabelFieldSerializer
    pagination_class = DynamicPageNumber(1000)

    ordering_fields = ['created_time', 'updated_time']
    filterset_class = ModelLabelFieldFilter

    @extend_schema(
        description='获取字段选择',
        responses=get_default_response_schema(
            {
                'choices_dict': build_object_type(
                    properties={
                        'key': build_array_type(
                            build_object_type(
                                properties={
                                    'value': build_basic_type(OpenApiTypes.STR),
                                    'label': build_basic_type(OpenApiTypes.STR),
                                }
                            )
                        )
                    }
                )
            }
        )
    )
    @action(methods=['get'], detail=False, url_path='choices')
    def choices_dict(self, request, *args, **kwargs):
        disabled_choices = [
            ModelLabelField.KeyChoices.TEXT,
            ModelLabelField.KeyChoices.JSON,
            ModelLabelField.KeyChoices.DATE,
            ModelLabelField.KeyChoices.DEPARTMENTS
        ]
        result = get_choices_dict(ModelLabelField.KeyChoices.choices, disabled_choices=disabled_choices)
        return ApiResponse(choices_dict={'choices': result})

    @extend_schema(
        description='获取字段名',
        parameters=[
            OpenApiParameter(name='table', required=True, type=str),
            OpenApiParameter(name='field', required=True, type=str),
        ],
        responses=get_default_response_schema({'data': build_array_type(build_basic_type(OpenApiTypes.STR))})
    )
    @action(methods=['get'], detail=False, queryset=ModelLabelField.objects, filterset_class=None)
    def lookups(self, request, *args, **kwargs):
        table = request.query_params.get('table')
        field = request.query_params.get('field')
        if table and field:
            if table == '*':
                table = 'system.userinfo'
            obj = self.filter_queryset(self.get_queryset()).filter(name=field, parent__name=table,
                                                                   parent__parent=None).first()
            if obj:
                mt = apps.get_model(table)
                if mt:
                    mf = mt._meta.get_field(field)
                    if mf:
                        return ApiResponse(data=mf.get_class_lookups().keys())
        return ApiResponse(code=1001)

    @extend_schema(description='同步字段', responses=get_default_response_schema())
    @action(methods=['get'], detail=False)
    def sync(self, request, *args, **kwargs):
        activate(settings.LANGUAGE_CODE)
        get_sub_serializer_fields()
        return ApiResponse()
