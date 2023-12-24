#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : server
# filename : dept
# author : ly_13
# date : 6/16/2023
import logging

from django_filters import rest_framework as filters
from rest_framework.decorators import action

from common.base.utils import get_choices_dict
from common.core.modelset import BaseModelSet
from common.core.pagination import MenuPageNumber
from common.core.response import ApiResponse
from system.models import DeptInfo, UserRole, DataPermission
from system.utils.serializer import DeptSerializer

logger = logging.getLogger(__name__)


class DeptFilter(filters.FilterSet):
    name = filters.CharFilter(field_name='name', lookup_expr='icontains')
    description = filters.CharFilter(field_name='description', lookup_expr='icontains')
    pk = filters.NumberFilter(field_name='id')

    class Meta:
        model = DeptInfo
        fields = ['pk', 'is_active', 'code', 'mode_type']


class DeptView(BaseModelSet):
    queryset = DeptInfo.objects.all()
    serializer_class = DeptSerializer
    pagination_class = MenuPageNumber

    ordering_fields = ['created_time', 'rank']
    filterset_class = DeptFilter

    def list(self, request, *args, **kwargs):
        data = super().list(request, *args, **kwargs).data
        return ApiResponse(**data, choices_dict=get_choices_dict(DeptInfo.mode_type_choices))

    @action(methods=['post'], detail=True)
    def empower(self, request, *args, **kwargs):
        instance = self.get_object()
        roles = request.data.get('roles')
        rules = request.data.get('rules')
        mode_type = request.data.get('mode_type')
        if roles or rules:
            if roles is not None:
                roles_queryset = UserRole.objects.filter(pk__in=roles).all()
                instance.roles.set(roles_queryset)
            if rules is not None:
                instance.mode_type = mode_type
                instance.modifier = request.user
                instance.save(update_fields=['mode_type', 'modifier'])
                rules_queryset = DataPermission.objects.filter(pk__in=rules).all()
                instance.rules.set(rules_queryset)
            return ApiResponse(detail="操作成功")
        return ApiResponse(code=1004, detail="数据异常")
