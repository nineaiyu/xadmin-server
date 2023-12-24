#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : server
# filename : audio
# author : ly_13
# date : 6/16/2023
import logging

from django.apps import apps
from django.conf import settings
from django_filters import rest_framework as filters
from rest_framework.decorators import action

from common.base.utils import get_choices_dict
from common.core.models import DbAuditModel
from common.core.modelset import BaseModelSet
from common.core.pagination import MenuPageNumber
from common.core.response import ApiResponse
from system.models import DataPermission
from system.utils.serializer import DataPermissionSerializer

logger = logging.getLogger(__name__)


class DataPermissionFilter(filters.FilterSet):
    name = filters.CharFilter(field_name='name', lookup_expr='icontains')
    description = filters.CharFilter(field_name='description', lookup_expr='icontains')
    pk = filters.NumberFilter(field_name='id')

    class Meta:
        model = DataPermission
        fields = ['pk', 'mode_type']


class DataPermissionView(BaseModelSet):
    queryset = DataPermission.objects.all()
    serializer_class = DataPermissionSerializer
    pagination_class = MenuPageNumber

    ordering_fields = ['created_time']
    filterset_class = DataPermissionFilter

    def list(self, request, *args, **kwargs):
        data = super().list(request, *args, **kwargs).data
        return ApiResponse(**data, choices_dict=get_choices_dict(DataPermission.mode_type_choices))

    @action(methods=['get'], detail=False)
    def fields(self, request, *args, **kwargs):
        results = [{
            'value': '*',
            'model_fields': [],
            'name': f"全部表-*"
        }]
        for app in apps.get_models():
            if app._meta.app_label not in settings.PERMISSION_DATA_AUTH_APPS:
                continue
            model_fields = []
            for field in app._meta.fields:
                model_fields.append({'value': field.name, 'name': f"{field.verbose_name}_{field.name}"})
            if not results[0].get('model_fields') and app._meta.model_name == 'userinfo':
                results[0]['model_fields'].append({
                    'value': '*',
                    'name': f"全部字段-*"
                })
                for rule in model_fields:
                    if rule.get('value') in [x.name for x in DbAuditModel._meta.fields]:
                        results[0]['model_fields'].append(rule)
            results.append({
                'value': f"{app._meta.app_label}.{app._meta.model_name}",
                'model_fields': model_fields,
                'name': f"{app._meta.verbose_name}-{app._meta.app_label}.{app._meta.model_name}"
            })

        values = [
            {
                'label': "文本",
                'key': 'value.text',
                'value_show': True
            }, {
                'label': "非文本",
                'key': 'value.json',
                'value_show': True
            },
            {
                'label': "全部数据",
                'key': 'value.all',
                'value_show': False
            }, {
                'label': "距离当前时间多少秒",
                'key': 'value.date',
                'value_show': True
            },
            {
                'label': "本人ID",
                'key': 'value.user.id',
                'value_show': False

            },
            {
                'label': "本部门ID",
                'key': 'value.user.dept.id',
                'value_show': False

            },
            {
                'label': "本部门ID及部门以下数据ID",
                'key': 'value.user.dept.ids',
                'value_show': False

            },
            {
                'label': "部门ID及部门以下数据ID",
                'key': 'value.dept.ids',
                'value_show': True
            },
        ]
        return ApiResponse(data={'results': results, 'values': values})

    @action(methods=['get'], detail=False)
    def lookups(self, request, *args, **kwargs):
        table = request.query_params.get('table')
        field = request.query_params.get('field')
        if table and field:
            if table == '*':
                table = 'system.userinfo'
            mt = apps.get_model(table)
            if mt:
                mf = mt._meta.get_field(field)
                if mf:
                    return ApiResponse(data={'results': mf.get_class_lookups().keys()})
        return ApiResponse(code=1001, detail="查询失败")
