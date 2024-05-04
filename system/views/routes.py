#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : routes
# author : ly_13
# date : 4/21/2024

from rest_framework.views import APIView

from common.base.magic import cache_response
from common.base.utils import menu_list_to_tree, format_menu_data
from common.core.permission import get_user_menu_queryset
from common.core.response import ApiResponse
from system.models import Menu
from system.utils.serializer import RouteSerializer


class UserRoutesView(APIView):
    """获取菜单路由"""

    def get_cache_key(self, view_instance, view_method, request, args, kwargs):
        func_name = f'{view_instance.__class__.__name__}_{view_method.__name__}'
        return f"{func_name}_{request.user.pk}"

    @cache_response(timeout=3600 * 24 * 7, key_func='get_cache_key')
    def get(self, request):
        route_list = []
        user_obj = request.user
        menu_type = [Menu.MenuChoices.DIRECTORY, Menu.MenuChoices.MENU]
        if user_obj.is_superuser:
            route_list = RouteSerializer(Menu.objects.filter(is_active=True, menu_type__in=menu_type).order_by('rank'),
                                         many=True, context={'user': request.user}, all_fields=True).data

            return ApiResponse(data=format_menu_data(menu_list_to_tree(route_list)))
        else:
            menu_queryset = get_user_menu_queryset(user_obj)
            if menu_queryset:
                route_list = RouteSerializer(
                    menu_queryset.filter(menu_type__in=menu_type).distinct().order_by('rank'), many=True,
                    context={'user': request.user}, all_fields=True).data

        return ApiResponse(data=format_menu_data(menu_list_to_tree(route_list)))
