#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : signal_handler.py
# author : ly_13
# date : 12/15/2023

import logging

from django.db.models.signals import post_save, pre_delete, m2m_changed
from django.dispatch import receiver

from common.base.magic import cache_response, MagicCacheData
from system.models import Menu, NoticeMessage, UserRole, UserInfo, NoticeUserRead, DeptInfo, DataPermission

logger = logging.getLogger(__name__)


@receiver(m2m_changed)
def clean_m2m_cache_handler(sender, instance, **kwargs):
    if kwargs.get('action') in ['post_add', 'pre_remove']:
        if issubclass(sender, NoticeUserRead):
            for pk in kwargs.get('pk_set', []):
                invalid_notify_cache(pk)

        if isinstance(instance, UserInfo):  # 分配用户角色，需要同时清理用户路由和用户信息
            invalid_user_cache(instance.pk)

        if isinstance(instance, DeptInfo):  # 分配用户角色，需要同时清理用户路由和用户信息
            for dept in DeptInfo.objects.filter(pk__in=DeptInfo.recursion_dept_info(instance.pk)).all():
                if dept.userinfo_set.count():
                    invalid_roles_cache(instance)

        if isinstance(instance, NoticeMessage):
            invalid_notify_caches(instance, kwargs.get('pk_set', []))

        if isinstance(instance, UserRole):
            invalid_roles_cache(instance)
            invalid_dept_caches(instance)


def invalid_dept_caches(instance):
    for dept in instance.deptinfo_set.all().distinct():
        if dept.userinfo_set.count():
            invalid_roles_cache(dept)

def invalid_notify_caches(instance, pk_set):
    pks = []
    if instance.notice_type == NoticeMessage.NoticeChoices.USER:
        pks = pk_set
    if instance.notice_type == NoticeMessage.NoticeChoices.ROLE:
        pks = UserInfo.objects.filter(roles__in=pk_set).values_list('pk', flat=True)
    if instance.notice_type == NoticeMessage.NoticeChoices.DEPT:
        pks = UserInfo.objects.filter(dept__in=pk_set).values_list('pk', flat=True)
    if pks:
        for pk in set(pks):
            invalid_notify_cache(pk)


def invalid_user_cache(user_pk):
    cache_response.invalid_cache(f'UserInfoView_retrieve_{user_pk}')
    cache_response.invalid_cache(f'UserRoutesView_get_{user_pk}')
    MagicCacheData.invalid_cache(f'get_user_permission_{user_pk}')  # 清理权限
    cache_response.invalid_cache(f'MenuView_list_{user_pk}_*')


def invalid_notify_cache(pk):
    cache_response.invalid_cache(f'UserNoticeMessage_unread_{pk}_*')
    cache_response.invalid_cache(f'UserNoticeMessage_list_{pk}_*')


def invalid_roles_cache(instance):
    for pk in instance.userinfo_set.values_list('pk', flat=True).distinct():
        invalid_user_cache(pk)


@receiver([post_save, pre_delete])
def clean_cache_handler(sender, instance, **kwargs):
    if issubclass(sender, Menu):
        cache_response.invalid_cache('MenuView_list_*')
        queryset = instance.userrole_set.values_list('userinfo', flat=True)
        if queryset.count() > 100:
            cache_response.invalid_cache('UserRoutesView_get_*')
        else:
            for pk in set(queryset):
                cache_response.invalid_cache(f'UserRoutesView_get_{pk}')
                MagicCacheData.invalid_cache(f'get_user_permission_{pk}')  # 清理权限
        for obj in DeptInfo.objects.filter(roles__menu=instance).distinct():
            invalid_roles_cache(obj)
        logger.info(f"invalid cache {sender}")

    if issubclass(sender, DataPermission):
        invalid_roles_cache(instance)
        invalid_dept_caches(instance)
        logger.info(f"invalid cache {sender}")

    if issubclass(sender, UserRole):
        invalid_roles_cache(instance)
        logger.info(f"invalid cache {sender}")

    if issubclass(sender, NoticeMessage):
        pk_set = None
        if instance.notice_type == NoticeMessage.NoticeChoices.NOTICE:
            invalid_notify_cache('*')
        elif instance.notice_type == NoticeMessage.NoticeChoices.DEPT:
            pk_set = instance.notice_dept.values_list('pk', flat=True)
        elif instance.notice_type == NoticeMessage.NoticeChoices.ROLE:
            pk_set = instance.notice_role.values_list('pk', flat=True)
        else:
            pk_set = instance.notice_user.values_list('pk', flat=True)
        if pk_set:
            invalid_notify_caches(instance, pk_set)
        logger.info(f"invalid cache {sender}")

    if issubclass(sender, UserInfo):
        invalid_user_cache(instance.pk)
        logger.info(f"invalid cache {sender}")

    if issubclass(sender, NoticeUserRead):
        invalid_notify_cache(instance.owner.pk)


@receiver([pre_delete])
def clean_cache_handler_pre_delete(sender, instance, **kwargs):
    if issubclass(sender, DeptInfo):
        if instance.userinfo_set.count():
            invalid_roles_cache(instance)
            logger.info(f"invalid cache {sender}")
