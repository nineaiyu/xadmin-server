#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : signal_handler.py
# author : ly_13
# date : 12/15/2023
import itertools

from django.contrib.auth import user_logged_out
from django.db.models.signals import post_save, pre_delete
from django.dispatch import receiver

from common.base.magic import cache_response, MagicCacheData
from common.core.config import SysConfig
from common.utils import get_logger
from system.models import Menu, UserRole, UserInfo, DeptInfo, SystemConfig
from system.signal import invalid_user_cache_signal

logger = get_logger(__name__)


def get_cache_data_keys(pks):
    for pk in pks:
        for method in ["GET", "PUT", "DELETE", "POST", "PATCH"]:
            yield f'get_user_permission_{pk}_{method}'


def get_cache_response_keys(pks):
    for pk in pks:
        yield f'UserRoutesAPIView_get_{pk}'


def batch_invalid_cache(pks, batch_length=1000):
    cleans = [
        (MagicCacheData.invalid_caches, get_cache_data_keys(pks)),
        (cache_response.invalid_caches, get_cache_response_keys(pks))
    ]
    for keys in cleans:
        for data in itertools.batched(keys[1], batch_length):
            keys[0](data)


@receiver([post_save, pre_delete], sender=Menu)
def clean_cache_handler(sender, instance, **kwargs):
    batch_invalid_cache(UserInfo.objects.filter(is_superuser=True).values_list('pk', flat=True))
    pk1 = UserRole.objects.filter(menu=instance, userinfo__isnull=False).values_list('userinfo', flat=True).distinct()
    pk2 = DeptInfo.objects.filter(roles__menu=instance).values_list('dept_query', flat=True).distinct()
    batch_invalid_cache(set(pk1) | set(pk2))
    logger.info(f"invalid cache {instance}")


@receiver([post_save, pre_delete], sender=SystemConfig)
def invalid_config_cache_handler(sender, instance, **kwargs):
    SysConfig.invalid_config_cache(instance.key)
    logger.info(f"invalid cache {instance}")


@receiver([post_save, pre_delete], sender=UserRole)
def invalid_role_cache_handler(sender, instance, **kwargs):
    pk1 = instance.userinfo_set.values_list('pk', flat=True).distinct()
    pk2 = DeptInfo.objects.filter(roles=instance).values_list('dept_query', flat=True).distinct()
    batch_invalid_cache(set(pk1) | set(pk2))
    logger.info(f"invalid cache {instance}")


@receiver([post_save, pre_delete], sender=DeptInfo)
def invalid_dept_cache_handler(sender, instance, **kwargs):
    batch_invalid_cache(instance.userinfo_set.values_list('pk', flat=True).distinct())
    logger.info(f"invalid cache {instance}")


@receiver([post_save, pre_delete], sender=UserInfo)
def invalid_user_cache_handler(sender, instance, **kwargs):
    batch_invalid_cache([instance.pk])
    logger.info(f"invalid cache {instance}")


# 清理用户相关缓存，用户登出会自动清理
@receiver([invalid_user_cache_signal, user_logged_out])
def invalid_user_cache(sender, **kwargs):
    user_pk = kwargs.get('user_pk', None)
    user = kwargs.get('user', None)
    if isinstance(user, UserInfo):
        user_pk = user.pk
    if user_pk is None:
        return

    batch_invalid_cache([user_pk])
