#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : routes
# author : ly_13
# date : 4/21/2024
from drf_spectacular.utils import extend_schema
from rest_framework.generics import GenericAPIView

from common.base.magic import cache_response
from common.base.utils import menu_list_to_tree, format_menu_data
from common.core.modelset import CacheDetailResponseMixin
from common.core.permission import get_user_menu_queryset
from common.core.response import ApiResponse
from system.models import Menu
from system.serializers.route import RouteSerializer


def get_auths(user):
    if user.is_superuser:
        menu_obj = Menu.objects.filter(is_active=True)
    else:
        menu_obj = get_user_menu_queryset(user)
    if not menu_obj:
        menu_obj = Menu.objects.none()
    return menu_obj.filter(menu_type=Menu.MenuChoices.PERMISSION).values_list('name', flat=True).distinct()


class UserRoutesAPIView(GenericAPIView, CacheDetailResponseMixin):
    """获取菜单路由"""

    @extend_schema(exclude=True)
    @cache_response(timeout=3600 * 24, key_func='get_cache_key')
    def get(self, request):
        route_list = []
        user_obj = request.user
        menu_type = [Menu.MenuChoices.DIRECTORY, Menu.MenuChoices.MENU]
        if user_obj.is_superuser:
            route_list = RouteSerializer(Menu.objects.filter(is_active=True, menu_type__in=menu_type).order_by('rank'),
                                         many=True, ignore_field_permission=True).data

            return ApiResponse(data=format_menu_data(menu_list_to_tree(route_list)), auths=get_auths(user_obj))
        else:
            menu_queryset = get_user_menu_queryset(user_obj)
            if menu_queryset:
                route_list = RouteSerializer(
                    menu_queryset.filter(menu_type__in=menu_type).distinct().order_by('rank'), many=True,
                    ignore_field_permission=True).data

        return ApiResponse(data=format_menu_data(menu_list_to_tree(route_list)), auths=get_auths(user_obj))
