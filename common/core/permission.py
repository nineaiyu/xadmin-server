#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : server
# filename : permission
# author : ly_13
# date : 6/6/2023
import re

from django.conf import settings
from rest_framework.exceptions import PermissionDenied, NotAuthenticated
from rest_framework.permissions import BasePermission

from system.models import Menu


def get_user_permission(user_obj):
    menu = []
    if user_obj.roles:
        menu_obj = Menu.objects.filter(userrole__in=user_obj.roles.all()).distinct()
        menu = menu_obj.filter(is_active=True, menu_type=2).values('path', 'component').all().distinct()
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
