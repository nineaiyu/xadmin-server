#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : server
# filename : permission
# author : ly_13
# date : 6/6/2023
import re

from django.conf import settings
from django.db.models import Q
from rest_framework.exceptions import PermissionDenied, NotAuthenticated
from rest_framework.permissions import BasePermission

from common.base.magic import MagicCacheData
from common.core.config import SysConfig
from system.models import Menu, FieldPermission


@MagicCacheData.make_cache(timeout=5, key_func=lambda x: x.pk)
def get_user_menu_queryset(user_obj):
    q = Q()
    has_role = False
    if user_obj.roles.count():
        q |= (Q(userrole__in=user_obj.roles.all()) & Q(userrole__is_active=True))
        has_role = True
    if user_obj.dept:
        q |= (Q(userrole__deptinfo=user_obj.dept) & Q(userrole__deptinfo__is_active=True))
        has_role = True
    if has_role:
        # return get_filter_queryset(Menu.objects.filter(is_active=True).filter(q), user_obj)
        # 菜单通过角色控制，就不用再次通过数据权限过滤了，要不然还得两个地方都得配置
        return Menu.objects.filter(is_active=True).filter(q)


@MagicCacheData.make_cache(timeout=30, key_func=lambda *args: f"{args[0].pk}_{args[1]}")
def get_user_field_queryset(user_obj, menu):
    q = Q()
    data = {}
    has_q = False
    if user_obj.roles.count():
        q |= (Q(role__in=user_obj.roles.all()) & Q(role__is_active=True))
        has_q = True
    if user_obj.dept:
        q |= (Q(role__deptinfo=user_obj.dept) & Q(role__deptinfo__is_active=True))
        has_q = True
    if has_q:
        # queryset = get_filter_queryset(FieldPermission.objects.filter(q), user_obj).filter(menu=menu)
        queryset = FieldPermission.objects.filter(q).filter(menu=menu)  # 用户查询用户权限，无需使用权限过滤
        for val in queryset.values_list('field__parent__name', 'field__name').distinct():
            info = data.get(val[0], set())
            if info:
                info.add(val[1])
            else:
                data[val[0]] = {val[1]}
    return data


@MagicCacheData.make_cache(timeout=3600 * 24 * 7, key_func=lambda x: x.pk)
def get_user_permission(user_obj):
    menu = []
    menu_queryset = get_user_menu_queryset(user_obj)
    if menu_queryset:
        menu = menu_queryset.filter(menu_type=Menu.MenuChoices.PERMISSION).values('path', 'method', 'pk').distinct()
    return menu


class IsAuthenticated(BasePermission):
    """
    Allows access only to authenticated users.
    """

    def has_permission(self, request, view):
        auth = bool(request.user and request.user.is_authenticated)
        if auth:
            if request.user.is_superuser:
                return True
            url = request.path_info
            for w_url in settings.PERMISSION_WHITE_URL:
                if re.match(w_url, url):
                    request.all_fields = True
                    return True
            permission_data = get_user_permission(request.user)
            permission_field = SysConfig.PERMISSION_FIELD
            for p_data in permission_data:
                if p_data.get('method') == request.method and re.match(f"/{p_data.get('path')}", url):
                    request.user.menu = p_data.get('pk')
                    if permission_field:
                        request.fields = get_user_field_queryset(request.user, p_data.get('pk'))
                    return True
            raise PermissionDenied('权限不足')
        else:
            raise NotAuthenticated('未授权认证')
