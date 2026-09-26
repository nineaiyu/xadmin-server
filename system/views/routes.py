#!/usr/bin/env python
# -*- coding: utf-8 -*-
# project : xadmin-server
# filename : routes
# author : ly_13
# date : 4/21/2024
import hashlib
import json

from drf_spectacular.utils import extend_schema
from rest_framework.generics import GenericAPIView

from common.base.magic import cache_response
from common.base.utils import format_menu_data, menu_list_to_tree
from common.core.modelset import CacheDetailResponseMixin
from common.core.modules import filter_menu_queryset
from common.core.permission import get_user_menu_queryset
from common.core.response import ApiResponse
from system.models import Menu
from system.serializers.route import RouteSerializer


def get_auths(user):
    if user.is_superuser:
        menu_obj = filter_menu_queryset(Menu.objects.filter(is_active=True))
    else:
        menu_obj = get_user_menu_queryset(user)
    if not menu_obj:
        menu_obj = Menu.objects.none()
    return menu_obj.filter(menu_type=Menu.MenuChoices.PERMISSION).values_list("name", flat=True).distinct()


def get_routes_version(data, auths) -> str:
    """路由 + 按钮授权快照的内容指纹：任一菜单/授权变化即变化。

    前端对本地路由快照（CachingAsyncRoutes）按此字段失效——在缓存函数内计算，
    版本与载荷随同一份缓存整体翻转，保证「版本变了 ⇒ 内容也变了」。
    """
    payload = json.dumps([data, list(auths)], ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.md5(payload.encode("utf-8")).hexdigest()


class UserRoutesAPIView(GenericAPIView, CacheDetailResponseMixin):
    """获取菜单路由"""

    @extend_schema(exclude=True)
    @cache_response(timeout=3600 * 24, key_func="get_cache_key")
    def get(self, request):
        route_list = []
        user_obj = request.user
        menu_type = [Menu.MenuChoices.DIRECTORY, Menu.MenuChoices.MENU]
        if user_obj.is_superuser:
            # 嵌套 meta（OneToOne）预取，缓存失效时不再每菜单一查
            route_list = RouteSerializer(
                filter_menu_queryset(Menu.objects.filter(is_active=True, menu_type__in=menu_type))
                .select_related("meta")
                .order_by("rank"),
                many=True,
                ignore_field_permission=True,
            ).data
            auths = get_auths(user_obj)
            return ApiResponse(
                data=format_menu_data(menu_list_to_tree(route_list)),
                auths=auths,
                version=get_routes_version(route_list, auths),
            )
        else:
            menu_queryset = get_user_menu_queryset(user_obj)
            if menu_queryset:
                route_list = RouteSerializer(
                    menu_queryset.filter(menu_type__in=menu_type).select_related("meta").distinct().order_by("rank"),
                    many=True,
                    ignore_field_permission=True,
                ).data

        auths = get_auths(user_obj)
        return ApiResponse(
            data=format_menu_data(menu_list_to_tree(route_list)),
            auths=auths,
            version=get_routes_version(route_list, auths),
        )
