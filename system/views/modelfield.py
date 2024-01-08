#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : modelfield
# author : ly_13
# date : 1/5/2024

import logging

from django.apps import apps
from django_filters import rest_framework as filters
from rest_framework.decorators import action

from common.base.utils import get_choices_dict
from common.core.modelset import OnlyListModelSet
from common.core.pagination import DynamicPageNumber
from common.core.response import ApiResponse
from system.models import ModelLabelField
from system.utils.serializer import ModelLabelFieldSerializer

logger = logging.getLogger(__name__)


class ModelLabelFieldFilter(filters.FilterSet):
    name = filters.CharFilter(field_name='name', lookup_expr='icontains')
    label = filters.CharFilter(field_name='label', lookup_expr='icontains')
    description = filters.CharFilter(field_name='description', lookup_expr='icontains')
    pk = filters.NumberFilter(field_name='id')
    parent = filters.NumberFilter(field_name='parent', method='get_parent')

    def get_parent(self, queryset, name, value):
        if value == 0:
            return queryset.filter(parent=None)
        return queryset.filter(parent__id=value)

    class Meta:
        model = ModelLabelField
        fields = ['pk', 'label', 'field_type']


class ModelLabelFieldView(OnlyListModelSet):
    queryset = ModelLabelField.objects.all()
    serializer_class = ModelLabelFieldSerializer
    pagination_class = DynamicPageNumber(1000)

    ordering_fields = ['created_time']
    filterset_class = ModelLabelFieldFilter

    def list(self, request, *args, **kwargs):
        data = super().list(request, *args, **kwargs).data
        disabled_choices = [
            ModelLabelField.KeyChoices.TEXT,
            ModelLabelField.KeyChoices.JSON,
            ModelLabelField.KeyChoices.DATE,
            ModelLabelField.KeyChoices.DEPARTMENTS
        ]
        return ApiResponse(**data, choices_dict=get_choices_dict(ModelLabelField.KeyChoices.choices,
                                                                 disabled_choices=disabled_choices))

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
                        return ApiResponse(data={'results': mf.get_class_lookups().keys()})
        return ApiResponse(code=1001, detail="查询失败")
