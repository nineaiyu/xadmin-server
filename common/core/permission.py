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
from system.models import Menu


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
        return Menu.objects.filter(is_active=True).filter(q).all()


@MagicCacheData.make_cache(timeout=3600 * 24 * 7, key_func=lambda x: x.pk)
def get_user_permission(user_obj):
    menu = []
    menu_queryset = get_user_menu_queryset(user_obj)
    if menu_queryset:
        menu = menu_queryset.filter(menu_type=2).values('path', 'component').distinct()
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
                    return True
            permission_data = get_user_permission(request.user)
            for p_data in permission_data:
                if p_data.get('component') == request.method and re.match(f"/{p_data.get('path')}", url):
                    return True
            raise PermissionDenied('权限不足')
        else:
            raise NotAuthenticated('未授权认证')
