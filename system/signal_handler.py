#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : signal_handler.py
# author : ly_13
# date : 12/15/2023

from django.contrib.auth import user_logged_out
from django.db.models.signals import post_save, pre_delete, m2m_changed
from django.dispatch import receiver

from common.base.magic import cache_response, MagicCacheData
from common.core.config import SysConfig
from common.utils import get_logger
from system.models import Menu, UserRole, UserInfo, DeptInfo, DataPermission, SystemConfig
from system.signal import invalid_user_cache_signal

logger = get_logger(__name__)



# def invalid_userinfo_view_cache(user_pk):
#     cache_response.invalid_cache(f'UserInfoViewSet_retrieve_{user_pk}')


def invalid_menu_view_cache(user_pk):
    cache_response.invalid_cache(f'MenuViewSet_list_{user_pk}')


def invalid_route_view_cache(user_pk):
    cache_response.invalid_cache(f'UserRoutesAPIView_get_{user_pk}')


def invalid_user_permission_data_cache(user_pk):
    MagicCacheData.invalid_cache(f'get_user_permission_{user_pk}')  # 清理权限


@receiver([invalid_user_cache_signal, user_logged_out])
def invalid_user_cache(**kwargs):
    user_pk = kwargs.get('user_pk', None)
    user = kwargs.get('user', None)
    if user is not None:
        user_pk = user.pk
    if user_pk is None:
        return
    # invalid_userinfo_view_cache(user_pk)
    invalid_route_view_cache(user_pk)
    invalid_menu_view_cache(f'{user_pk}_*')

    invalid_user_permission_data_cache(user_pk)
    MagicCacheData.invalid_cache(f'get_user_field_queryset_{user_pk}')  # 清理权限


@receiver(m2m_changed)
def clean_m2m_cache_handler(sender, instance, **kwargs):
    if kwargs.get('action') in ['post_add', 'pre_remove']:

        if isinstance(instance, UserInfo):  # 分配用户角色，需要同时清理用户路由和用户信息
            invalid_user_cache(user_pk=instance.pk)

        if isinstance(instance, DeptInfo):  # 分配用户角色，需要同时清理用户路由和用户信息
            for dept in DeptInfo.objects.filter(pk__in=DeptInfo.recursion_dept_info(instance.pk)).all():
                if dept.userinfo_set.count():
                    invalid_roles_cache(instance)

        if isinstance(instance, UserRole):
            invalid_roles_cache(instance)
            invalid_dept_caches(instance)


def invalid_dept_caches(instance):
    for dept in instance.deptinfo_set.all().distinct():
        if dept.userinfo_set.count():
            invalid_roles_cache(dept)


def invalid_superuser_cache():
    for pk in UserInfo.objects.filter(is_superuser=True).values_list('pk', flat=True):
        invalid_user_cache(user_pk=pk)


def invalid_roles_cache(instance):
    for pk in instance.userinfo_set.values_list('pk', flat=True).distinct():
        invalid_user_cache(user_pk=pk)


@receiver([post_save, pre_delete])
def clean_cache_handler(sender, instance, **kwargs):
    update_fields = kwargs.get('update_fields', [])

    if issubclass(sender, DataPermission):
        invalid_roles_cache(instance)
        invalid_dept_caches(instance)
        logger.info(f"invalid cache {sender}")

    if issubclass(sender, UserRole):
        invalid_roles_cache(instance)
        logger.info(f"invalid cache {sender}")

    if issubclass(sender, UserInfo):
        if update_fields is None or {'roles', 'rules', 'dept', 'mode_type'} & set(update_fields):
            invalid_user_cache(user_pk=instance.pk)
        # else:
        #     invalid_userinfo_view_cache(instance.pk)
        logger.info(f"invalid cache {sender}")

    if issubclass(sender, SystemConfig):
        SysConfig.invalid_config_cache(instance.key)


@receiver([pre_delete])
def clean_cache_handler_pre_delete(sender, instance, **kwargs):
    if issubclass(sender, DeptInfo):
        if instance.userinfo_set.count():
            invalid_roles_cache(instance)
            logger.info(f"invalid cache {sender}")


@receiver([post_save, pre_delete], sender=Menu)
def clean_cache_handler(sender, instance, **kwargs):
    invalid_menu_view_cache('*')
    invalid_superuser_cache()
    invalid_route_view_cache('*')
    invalid_user_permission_data_cache('*')
    for obj in DeptInfo.objects.filter(roles__menu=instance).distinct():
        invalid_roles_cache(obj)
    logger.info(f"invalid cache {sender}")
