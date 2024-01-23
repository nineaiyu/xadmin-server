#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : server
# filename : dept
# author : ly_13
# date : 6/16/2023
import logging

from django_filters import rest_framework as filters

from common.base.utils import get_choices_dict
from common.core.modelset import BaseModelSet
from common.core.pagination import DynamicPageNumber
from common.core.response import ApiResponse
from system.models import DeptInfo
from system.utils.modelset import ChangeRolePermissionAction
from system.utils.serializer import DeptSerializer

logger = logging.getLogger(__name__)


class DeptFilter(filters.FilterSet):
    name = filters.CharFilter(field_name='name', lookup_expr='icontains')
    description = filters.CharFilter(field_name='description', lookup_expr='icontains')
    pk = filters.CharFilter(field_name='id')

    class Meta:
        model = DeptInfo
        fields = ['pk', 'is_active', 'code', 'mode_type', 'auto_bind']


class DeptView(BaseModelSet, ChangeRolePermissionAction):
    queryset = DeptInfo.objects.all()
    serializer_class = DeptSerializer
    pagination_class = DynamicPageNumber(1000)

    ordering_fields = ['created_time', 'rank']
    filterset_class = DeptFilter

    def list(self, request, *args, **kwargs):
        data = super().list(request, *args, **kwargs).data
        return ApiResponse(**data, choices_dict=get_choices_dict(DeptInfo.ModeChoices.choices))
