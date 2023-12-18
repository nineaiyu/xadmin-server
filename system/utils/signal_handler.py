#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : signal_handler.py
# author : ly_13
# date : 12/15/2023

import logging

from django.db.models.signals import post_save, pre_delete, m2m_changed
from django.dispatch import receiver

from common.base.magic import cache_response
from system.models import Menu, NoticeMessage, UserRole, UserInfo, NoticeUserRead

logger = logging.getLogger(__name__)


@receiver(m2m_changed)
def clean_m2m_cache_handler(sender, instance, **kwargs):
    if kwargs.get('action') in ['post_add', 'pre_remove']:
        if issubclass(sender, NoticeUserRead):
            for pk in kwargs.get('pk_set', []):
                invalid_notify_cache(pk)

        if isinstance(instance, UserInfo):  # 分配用户角色，需要同时清理用户路由和用户信息
            cache_response.invalid_cache(f'UserInfoView_retrieve_{instance.pk}')
            cache_response.invalid_cache(f'UserRoutesView_get_{instance.pk}')


def invalid_notify_cache(pk):
    cache_response.invalid_cache(f'UserNoticeMessage_unread_{pk}')
    cache_response.invalid_cache(f'UserNoticeMessage_list_{pk}_*')


@receiver([post_save, pre_delete])
def clean_cache_handler(sender, instance, **kwargs):
    if issubclass(sender, Menu):
        cache_response.invalid_cache('MenuView_list_*')
        queryset = instance.userrole_set.values('userinfo')
        if queryset.count() > 100:
            cache_response.invalid_cache('UserRoutesView_get_*')
        else:
            for pk in set([x.get('userinfo') for x in queryset]):
                cache_response.invalid_cache(f'UserRoutesView_get_{pk}')
        logger.info(f"invalid cache {sender}")

    if issubclass(sender, UserRole):
        for pk in instance.userinfo_set.values('pk').all().distinct():
            cache_response.invalid_cache(f'UserRoutesView_get_{pk.get("pk")}')
        logger.info(f"invalid cache {sender}")

    if issubclass(sender, NoticeMessage):
        if instance.notice_type == 3:
            cache_response.invalid_cache(f'UserNoticeMessage_unread_*')
        else:
            for pk in instance.owner.values('pk').distinct():
                invalid_notify_cache(pk.get("pk"))
        logger.info(f"invalid cache {sender}")

    if issubclass(sender, UserInfo):
        cache_response.invalid_cache(f'UserInfoView_retrieve_{instance.pk}')
        logger.info(f"invalid cache {sender}")

    if issubclass(sender, NoticeUserRead):
        invalid_notify_cache(instance.owner.pk)
