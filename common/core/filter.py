#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : server
# filename : filter
# author : ly_13
# date : 6/2/2023
import datetime
import json
import logging

from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import NotAuthenticated
from rest_framework.filters import BaseFilterBackend

from common.core.db.utils import RelatedManager
from system.models import UserInfo, DataPermission

logger = logging.getLogger(__name__)

class OwnerUserFilter(BaseFilterBackend):

    def filter_queryset(self, request, queryset, view):
        if request.user and request.user.is_authenticated:
            return queryset.filter(creator_id=request.user)
        raise NotAuthenticated('未授权认证')


class DataPermissionFilter(BaseFilterBackend):
    def filter_queryset(self, request, queryset, view):
        user_obj: UserInfo = request.user
        if user_obj.is_superuser:
            return queryset
        else:
            app_label = queryset.model._meta.app_label
            model_name = queryset.model._meta.model_name

        # table = f'*'
        dept_obj = user_obj.dept
        permission = DataPermission.objects.filter(Q(userinfo=user_obj) | Q(deptinfo=dept_obj))
        results = []
        for obj in permission:
            rules = []
            if obj.is_active:
                for rule in obj.rules:
                    if rule.get('table') in [f"{app_label}.{model_name}", "*"]:
                        rules.append(rule)
                if rules:
                    results.append({'mode': obj.mode_type, 'rules': rules})
        or_qs = []
        if not results:
            return queryset.none()
        for result in results:
            for rule in result.get('rules'):
                f_type = rule.get('type')
                if f_type == 'value.user.id':
                    rule['value'] = user_obj.id
                elif f_type == 'value.user.dept.id':
                    rule['value'] = user_obj.dept_id
                elif f_type == 'value.user.dept.ids':
                    rule['match'] = 'in'
                    rule['value'] = user_obj.dept.recursion_dept_info(dept_obj.pk)
                elif f_type == 'value.dept.ids':
                    rule['match'] = 'in'
                    rule['value'] = user_obj.dept.recursion_dept_info(json.loads(rule['value']))
                elif f_type == 'value.all':
                    rule['match'] = 'all'
                    if dept_obj.mode_type == 0 and result.get('mode') == 0:
                        logger.warning(f"{app_label}.{model_name} : all queryset")
                        return queryset  # 全部数据直接返回 queryset
                elif f_type == 'value.date':
                    val = json.loads(rule['value'])
                    if val < 0:
                        rule['value'] = timezone.now() - datetime.timedelta(seconds=val)
                    else:
                        rule['value'] = timezone.now() + datetime.timedelta(seconds=val)
                elif f_type == 'value.json':
                    rule['value'] = json.loads(rule['value'])
                rule.pop('type', None)

            #  ((0, '或模式'), (1, '且模式'))
            qs = RelatedManager.get_filter_attrs_qs(result.get('rules'))
            q = Q()
            if result.get('mode') == 1:
                for a in set(qs):
                    q &= a
            else:
                for a in set(qs):
                    q |= a
            or_qs.append(q)
        q1 = Q()
        for q in set(or_qs):
            if dept_obj.mode_type == 1:
                q1 &= q
            else:
                if q == Q():
                    return queryset
                q1 |= q
        logger.warning(f"{app_label}.{model_name} : {q1}")
        return queryset.filter(q1)
