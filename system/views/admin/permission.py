#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : server
# filename : permission
# author : ly_13
# date : 6/16/2023
import logging

from django_filters import rest_framework as filters

from common.base.utils import get_choices_dict
from common.core.modelset import BaseModelSet
from common.core.response import ApiResponse
from system.models import DataPermission
from system.utils.serializer import DataPermissionSerializer

logger = logging.getLogger(__name__)


class DataPermissionFilter(filters.FilterSet):
    name = filters.CharFilter(field_name='name', lookup_expr='icontains')
    description = filters.CharFilter(field_name='description', lookup_expr='icontains')
    pk = filters.CharFilter(field_name='id')

    class Meta:
        model = DataPermission
        fields = ['pk', 'mode_type', 'is_active']


class DataPermissionView(BaseModelSet):
    queryset = DataPermission.objects.all()
    serializer_class = DataPermissionSerializer

    ordering_fields = ['created_time']
    filterset_class = DataPermissionFilter

    def list(self, request, *args, **kwargs):
        data = super().list(request, *args, **kwargs).data
        return ApiResponse(**data, choices_dict=get_choices_dict(DataPermission.ModeChoices.choices))
