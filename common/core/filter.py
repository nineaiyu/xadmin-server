#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : server
# filename : filter
# author : ly_13
# date : 6/2/2023
import datetime
import json
import logging

from django.db.models import Q, QuerySet
from django.forms.utils import from_current_timezone
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django_filters import rest_framework as filters
from rest_framework.exceptions import NotAuthenticated
from rest_framework.filters import BaseFilterBackend

from common.core.config import SysConfig
from common.core.db.utils import RelatedManager
from system.models import UserInfo, DataPermission, ModeTypeAbstract, DeptInfo, ModelLabelField

logger = logging.getLogger(__name__)


def get_filter_q_base(model, permission, user_obj=None, dept_obj=None):
    results = []
    for obj in permission:
        rules = []
        if len(obj.rules) == 1:
            obj.mode_type = ModeTypeAbstract.ModeChoices.OR
        for rule in obj.rules:
            if rule.get('table') in [model._meta.label_lower, "*"]:
                if rule.get('type') == ModelLabelField.KeyChoices.ALL:
                    if obj.mode_type == ModeTypeAbstract.ModeChoices.AND:  # 且模式，存在*，则忽略该规则
                        continue
                    else:  # 或模式，存在* 则该规则表仅*生效
                        rules = [rule]
                        break
                rules.append(rule)
        if rules:
            results.append({'mode': obj.mode_type, 'rules': rules})
    or_qs = []
    if not results:
        return Q(id=0)
    for result in results:
        for rule in result.get('rules'):
            f_type = rule.get('type')
            if f_type == ModelLabelField.KeyChoices.OWNER:
                if user_obj:
                    rule['value'] = user_obj.id
                else:
                    rule['value'] = '0'
            elif f_type == ModelLabelField.KeyChoices.OWNER_DEPARTMENT:
                if user_obj:
                    rule['value'] = user_obj.dept_id
                else:
                    rule['value'] = '0'
            elif f_type == ModelLabelField.KeyChoices.OWNER_DEPARTMENTS:
                rule['match'] = 'in'
                if dept_obj:
                    rule['value'] = DeptInfo.recursion_dept_info(dept_obj.pk)
                else:
                    rule['value'] = []
            elif f_type == ModelLabelField.KeyChoices.DEPARTMENTS:
                rule['match'] = 'in'
                if dept_obj:
                    rule['value'] = DeptInfo.recursion_dept_info(json.loads(rule['value']))
                else:
                    rule['value'] = []
            elif f_type == ModelLabelField.KeyChoices.ALL:
                rule['match'] = 'all'
                if ModeTypeAbstract.ModeChoices.OR == result.get('mode'):
                    if (dept_obj and dept_obj.mode_type == ModeTypeAbstract.ModeChoices.OR) or not dept_obj:
                        logger.warning(f"{model._meta.label_lower} : all queryset")
                        return Q()  # 全部数据直接返回 queryset
            elif f_type == ModelLabelField.KeyChoices.DATE:
                val = json.loads(rule['value'])
                if val < 0:
                    rule['value'] = timezone.now() - datetime.timedelta(seconds=-val)
                else:
                    rule['value'] = timezone.now() + datetime.timedelta(seconds=val)
            elif f_type == ModelLabelField.KeyChoices.DATETIME_RANGE:
                if isinstance(rule['value'], list) and len(rule['value']) == 2:
                    rule['value'] = [from_current_timezone(parse_datetime(rule['value'][0])),
                                     from_current_timezone(parse_datetime(rule['value'][1]))]
            elif f_type == ModelLabelField.KeyChoices.DATETIME:
                if isinstance(rule['value'], str):
                    rule['value'] = from_current_timezone(parse_datetime(rule['value']))
            elif f_type in [ModelLabelField.KeyChoices.JSON, ModelLabelField.KeyChoices.TABLE_USER,
                            ModelLabelField.KeyChoices.TABLE_MENU, ModelLabelField.KeyChoices.TABLE_ROLE,
                            ModelLabelField.KeyChoices.TABLE_DEPT]:
                rule['value'] = json.loads(rule['value'])
            rule.pop('type', None)

        #  ((0, '或模式'), (1, '且模式'))
        qs = RelatedManager.get_filter_attrs_qs(result.get('rules'))
        q = Q()
        if result.get('mode') == ModeTypeAbstract.ModeChoices.AND:
            for a in set(qs):
                if a == Q():
                    continue
                q &= a
        else:
            for a in set(qs):
                if a == Q():
                    q = Q()
                    break
                q |= a
        or_qs.append(q)
    q1 = Q()
    if not dept_obj:
        for q in set(or_qs):
            q1 |= q
    else:
        for q in set(or_qs):
            if dept_obj.mode_type == ModeTypeAbstract.ModeChoices.AND:
                if q == Q():
                    continue
                q1 &= q
            else:
                if q == Q():
                    return q
                q1 |= q
        if dept_obj.mode_type == ModeTypeAbstract.ModeChoices.AND and q1 == Q():
            return Q(id=0)
    logger.warning(f"{model._meta.label_lower} : {q1}")
    return q1


def get_filter_queryset(queryset: QuerySet, user_obj: UserInfo):
    """
    1.获取所有数据权限规则
    2.循环判断规则
    a.循环判断最内层规则，根据模式和全部数据进行判断【如果规则数量为一个，则模式该规则链为或模式】
        如果模式为或模式，并存在全部数据，则该规则链其他规则失效，仅保留该规则
        如果模式为且模式，并且存在全部数据，则该改则失效
    b.判断外层规则 【如果规则数量为一个，则模式该规则链为或模式】
        若模式为或模式，并存在全部数据，则直接返回queryset
        若模式为且模式，则 返回queryset.filter(规则)
    """
    if not SysConfig.PERMISSION_DATA:
        return queryset

    if user_obj.is_superuser:
        logger.debug(f"superuser: {user_obj.username}. return all queryset {queryset.model._meta.label_lower}")
        return queryset

    # table = f'*'
    dept_obj = user_obj.dept
    q = Q()
    dq = Q(menu__isnull=True) | Q(menu__isnull=False, menu__pk=getattr(user_obj, 'menu', None))
    has_dept = False
    if dept_obj:
        dept_pks = DeptInfo.recursion_dept_info(dept_obj.pk, is_parent=True)
        for p_dept_obj in DeptInfo.objects.filter(pk__in=dept_pks, is_active=True):
            permission = DataPermission.objects.filter(is_active=True).filter(deptinfo=p_dept_obj).filter(dq)
            q &= get_filter_q_base(queryset.model, permission, user_obj, dept_obj)
            has_dept = True
        if not has_dept and q == Q():
            q = Q(id=0)
    permission = DataPermission.objects.filter(is_active=True).filter(userinfo=user_obj).filter(dq)
    if not permission.count():
        return queryset.filter(q)
    q1 = get_filter_q_base(queryset.model, permission, user_obj, dept_obj)
    if q1 == Q():
        q = q1
    else:
        q |= q1
    return queryset.filter(q)


class OwnerUserFilter(BaseFilterBackend):

    def filter_queryset(self, request, queryset, view):
        if request.user and request.user.is_authenticated:
            return queryset.filter(owner=request.user)
        raise NotAuthenticated('未授权认证')


class CreatorUserFilter(BaseFilterBackend):

    def filter_queryset(self, request, queryset, view):
        if request.user and request.user.is_authenticated:
            return queryset.filter(creator=request.user)
        raise NotAuthenticated('未授权认证')


class BaseDataPermissionFilter(BaseFilterBackend):
    def filter_queryset(self, request, queryset, view):
        return get_filter_queryset(queryset, request.user)


class BaseFilterSet(filters.FilterSet):
    pk = filters.NumberFilter(field_name='id')
    creator = filters.NumberFilter(field_name='creator')
    modifier = filters.NumberFilter(field_name='modifier')
    dept_belong = filters.UUIDFilter(field_name='dept_belong')
    created_time = filters.DateTimeFromToRangeFilter(field_name='created_time')
    updated_time = filters.DateTimeFromToRangeFilter(field_name='updated_time')
    description = filters.CharFilter(field_name='description', lookup_expr='icontains')
