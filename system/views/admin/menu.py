#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : server
# filename : menu
# author : ly_13
# date : 6/6/2023
from hashlib import md5

from django.db.models import Q
from django_filters import rest_framework as filters
from drf_yasg.utils import swagger_auto_schema
from rest_framework.decorators import action

from common.base.magic import cache_response
from common.core.filter import BaseFilterSet
from common.core.modelset import BaseModelSet, RankAction
from common.core.pagination import DynamicPageNumber
from common.core.response import ApiResponse
from common.core.utils import get_all_url_dict
from system.models import Menu
from system.utils.serializer import MenuSerializer, MenuPermissionSerializer


class MenuFilter(BaseFilterSet):
    name = filters.CharFilter(field_name='name', lookup_expr='icontains')
    component = filters.CharFilter(field_name='component', lookup_expr='icontains')
    title = filters.CharFilter(field_name='meta__title', lookup_expr='icontains')
    path = filters.CharFilter(field_name='path', lookup_expr='icontains')

    class Meta:
        model = Menu
        fields = ['name']


class MenuView(BaseModelSet, RankAction):
    """菜单管理"""
    queryset = Menu.objects.order_by('rank').all()
    serializer_class = MenuSerializer
    permissions_serializer_class = MenuPermissionSerializer
    pagination_class = DynamicPageNumber(1000)
    ordering_fields = ['updated_time', 'name', 'created_time', 'rank']
    filterset_class = MenuFilter

    def get_cache_key(self, view_instance, view_method, request, args, kwargs):
        func_name = f'{view_instance.__class__.__name__}_{view_method.__name__}'
        return f"{func_name}_{request.user.pk}_{md5(request.META['QUERY_STRING'].encode('utf-8')).hexdigest()}"

    @cache_response(timeout=600, key_func='get_cache_key')
    def list(self, request, *args, **kwargs):
        data = super().list(request, *args, **kwargs).data
        return ApiResponse(**data)

    @action(methods=['get'], detail=False)
    def permissions(self, request, *args, **kwargs):
        def get_queryset():
            pks = self.filter_queryset(self.queryset).filter(menu_type=Menu.MenuChoices.PERMISSION).values_list(
                'parent', flat=True)
            return self.filter_queryset(self.queryset).filter(
                Q(menu_type=Menu.MenuChoices.PERMISSION) | Q(id__in=pks))

        self.get_queryset = get_queryset
        return super().list(request, *args, **kwargs)

    @swagger_auto_schema(ignore_params=True)
    @action(methods=['get'], detail=False, url_path='api-url')
    def api_url(self, request, *args, **kwargs):
        return ApiResponse(results=get_all_url_dict(''))
