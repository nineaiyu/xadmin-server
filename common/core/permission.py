#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : server
# filename : permission
# author : ly_13
# date : 6/6/2023
import re
import uuid

from django.conf import settings
from django.db.models import Q
from django.utils.translation import gettext_lazy as _
from rest_framework.exceptions import PermissionDenied, NotAuthenticated
from rest_framework.permissions import BasePermission

from common.base.magic import MagicCacheData
from server.utils import get_current_request, set_current_request
from system.models import Menu, FieldPermission


def get_user_menu_queryset(user_obj):
    q = Q()
    has_role = False
    if user_obj.roles.exists():
        q |= (Q(userrole__in=user_obj.roles.all()) & Q(userrole__is_active=True))
        has_role = True
    if user_obj.dept:
        q |= (Q(userrole__deptinfo=user_obj.dept) & Q(userrole__deptinfo__is_active=True))
        has_role = True
    if has_role:
        # return get_filter_queryset(Menu.objects.filter(is_active=True).filter(q), user_obj)
        # 菜单通过角色控制，就不用再次通过数据权限过滤了，要不然还得两个地方都得配置
        return Menu.objects.filter(is_active=True).filter(q)


@MagicCacheData.make_cache(timeout=10, key_func=lambda *args: f"{args[0].pk}_{args[1]}")
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


@MagicCacheData.make_cache(timeout=3600 * 24, key_func=lambda x, y: f"{x.pk}_{y}")
def get_user_permission(user_obj, method):
    menus = []
    menu_queryset = get_user_menu_queryset(user_obj)
    if menu_queryset:
        filter_kwargs = {"menu_type": Menu.MenuChoices.PERMISSION, "method": method}
        menus = menu_queryset.filter(**filter_kwargs).values_list('path', 'pk', 'model').distinct()
    return dict([(menu[0], menu[1:]) for menu in menus])


def get_import_export_permission(permission_data, url):
    match_group = re.match("(?P<url>.*)/(export|import)-data$", url)
    if match_group:
        url = match_group.group('url')
        for p_data in permission_data:
            if re.match(f"/{p_data.get('path')}", url):
                return p_data


def get_menu_pk(permission_data, url):
    # 1.直接get api/system/permission$   /api/system/config/system
    p_data = permission_data.get(f"{url[1:]}$")
    if not p_data:
        for p_path, p_data in permission_data.items():
            if re.match(f"/{p_path}", url):
                return p_data
    return p_data


class IsAuthenticated(BasePermission):
    """
    Allows access only to authenticated users.
    """

    def has_permission(self, request, view):
        auth = bool(request.user and request.user.is_authenticated)
        if auth:
            request.request_uuid = getattr(get_current_request(), "request_uuid", uuid.uuid4())
            set_current_request(request)

            if request.user.is_superuser:
                request.ignore_field_permission = True
                return True
            url = request.path_info
            for w_url, method in settings.PERMISSION_WHITE_URL.items():
                if re.match(w_url, url) and ('*' in method or request.method in method):
                    request.ignore_field_permission = True
                    return True
            permission_data = get_user_permission(request.user, request.method)
            # 处理search-columns字段权限和list权限一致
            match_group = re.match("(?P<url>.*)/search-columns$", url)
            if match_group:
                url = match_group.group('url')
            p_data = p_data_new = get_menu_pk(permission_data, url)

            if p_data:
                # 导入导出功能，若未绑定模型，则使用list, create菜单
                match_group = re.match("(?P<url>.*)/(export|import)-data$", url)
                if match_group and p_data[1] is None:
                    url = match_group.group('url')
                    p_data_new = get_menu_pk(permission_data, url)
                if not p_data_new:
                    p_data_new = p_data

                request.user.menu = p_data_new[0]
                if settings.PERMISSION_FIELD_ENABLED:
                    request.fields = get_user_field_queryset(request.user, p_data_new[0])
                return True

            raise PermissionDenied(_("Permission denied"))
        else:
            raise NotAuthenticated(_("Unauthorized authentication"))
