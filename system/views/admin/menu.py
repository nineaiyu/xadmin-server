#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : server
# filename : menu
# author : ly_13
# date : 6/6/2023
from hashlib import md5

from django.db.models import Q
from django_filters import rest_framework as filters
from rest_framework.decorators import action
from rest_framework.views import APIView

from common.base.magic import cache_response
from common.base.utils import menu_list_to_tree, format_menu_data
from common.core.filter import BaseFilterSet
from common.core.modelset import BaseModelSet, RankAction
from common.core.pagination import DynamicPageNumber
from common.core.permission import get_user_menu_queryset
from common.core.response import ApiResponse
from common.core.utils import get_all_url_dict
from system.models import Menu
from system.utils.serializer import MenuSerializer, RouteSerializer, MenuPermissionSerializer


class MenuFilter(BaseFilterSet):
    name = filters.CharFilter(field_name='name', lookup_expr='icontains')
    component = filters.CharFilter(field_name='component', lookup_expr='icontains')
    title = filters.CharFilter(field_name='meta__title', lookup_expr='icontains')
    path = filters.CharFilter(field_name='path', lookup_expr='icontains')

    class Meta:
        model = Menu
        fields = ['name']


class MenuView(BaseModelSet, RankAction):
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

    @action(methods=['get'], detail=False, url_path='api-url')
    def api_url(self, request, *args, **kwargs):
        return ApiResponse(results=get_all_url_dict(''))

class UserRoutesView(APIView):

    def get_cache_key(self, view_instance, view_method, request, args, kwargs):
        func_name = f'{view_instance.__class__.__name__}_{view_method.__name__}'
        return f"{func_name}_{request.user.pk}"

    @cache_response(timeout=3600 * 24 * 7, key_func='get_cache_key')
    def get(self, request):
        menu_list = []
        user_obj = request.user
        menu_type = [Menu.MenuChoices.DIRECTORY, Menu.MenuChoices.MENU]
        if user_obj.is_superuser:
            menu_list = RouteSerializer(Menu.objects.filter(is_active=True, menu_type__in=menu_type).order_by('rank'),
                                        many=True, context={'user': request.user}, init=True).data

            return ApiResponse(data=format_menu_data(menu_list_to_tree(menu_list)))
        else:
            menu_queryset = get_user_menu_queryset(user_obj)
            if menu_queryset:
                menu_list = RouteSerializer(
                    menu_queryset.filter(menu_type__in=menu_type).distinct().order_by('rank'), many=True,
                    context={'user': request.user}, init=True).data

        return ApiResponse(data=format_menu_data(menu_list_to_tree(menu_list)))
