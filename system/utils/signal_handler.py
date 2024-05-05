#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : signal_handler.py
# author : ly_13
# date : 12/15/2023
import logging

from django.conf import settings
from django.db import transaction
from django.db.models.signals import post_save, pre_delete, m2m_changed, post_migrate
from django.dispatch import receiver
from django.utils import timezone

from common.base.magic import cache_response, MagicCacheData
from common.core.config import SysConfig
from common.core.models import DbAuditModel
from common.core.serializers import get_sub_serializer_fields
from system.models import Menu, NoticeMessage, UserRole, UserInfo, NoticeUserRead, DeptInfo, DataPermission, \
    SystemConfig, ModelLabelField
from system.utils.notify import push_notice_messages

logger = logging.getLogger(__name__)


@receiver(post_migrate)
@transaction.atomic
def post_migrate_handler(sender, **kwargs):
    if not UserInfo.objects.filter(pk=1).first():
        return
    label = sender.label
    delete = False
    now = timezone.now()
    if label not in settings.PERMISSION_DATA_AUTH_APPS:
        return
    field_type = ModelLabelField.FieldChoices.DATA
    obj, _ = ModelLabelField.objects.update_or_create(name=f"*", field_type=field_type, defaults={'label': "全部表"},
                                                      parent=None)
    ModelLabelField.objects.update_or_create(name=f"*", field_type=field_type, parent=obj,
                                             defaults={'label': "全部字段"})
    for field in DbAuditModel._meta.fields:
        ModelLabelField.objects.update_or_create(name=field.name, field_type=field_type, parent=obj,
                                                 defaults={'label': getattr(field, 'verbose_name', field.name)})
    for model in sender.models.values():
        delete = True
        model_name = model._meta.model_name
        verbose_name = model._meta.verbose_name
        if 'relationship' in verbose_name and '_' in model_name:
            continue
        obj, _ = ModelLabelField.objects.update_or_create(name=f"{label}.{model_name}", field_type=field_type,
                                                          parent=None, defaults={'label': verbose_name})
        # for field in model._meta.get_fields():
        for field in model._meta.fields:
            ModelLabelField.objects.update_or_create(name=field.name, parent=obj, field_type=field_type,
                                                     defaults={'label': field.verbose_name})
            # defaults={'label': getattr(field, 'verbose_name', field.through._meta.verbose_name)})
    if delete:
        deleted, _rows_count = ModelLabelField.objects.filter(field_type=field_type, updated_time__lt=now).delete()
        logger.warning(f"auto upsert deleted {deleted} row_count {_rows_count}")

    if label == settings.PERMISSION_DATA_AUTH_APPS[0]:
        try:
            get_sub_serializer_fields()
        except Exception as e:
            logger.error(f"auto get sub serializer fields failed. {e}")


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
        if instance.publish:
            push_notice_messages(instance, set(pks))
        for pk in set(pks):
            invalid_notify_cache(pk)


def invalid_user_cache(user_pk):
    cache_response.invalid_cache(f'UserInfoView_retrieve_{user_pk}')
    cache_response.invalid_cache(f'UserRoutesView_get_{user_pk}')
    MagicCacheData.invalid_cache(f'get_user_permission_{user_pk}')  # 清理权限
    MagicCacheData.invalid_cache(f'get_user_field_queryset_{user_pk}')  # 清理权限
    cache_response.invalid_cache(f'MenuView_list_{user_pk}_*')
    invalid_notify_cache(user_pk)


def invalid_superuser_cache():
    for pk in UserInfo.objects.filter(is_superuser=True).values_list('pk', flat=True):
        invalid_user_cache(pk)


def invalid_notify_cache(pk):
    cache_response.invalid_cache(f'UserNoticeMessage_unread_{pk}_*')
    cache_response.invalid_cache(f'UserNoticeMessage_list_{pk}_*')


def invalid_roles_cache(instance):
    for pk in instance.userinfo_set.values_list('pk', flat=True).distinct():
        invalid_user_cache(pk)


@receiver([post_save, pre_delete])
def clean_cache_handler(sender, instance, **kwargs):
    update_fields = kwargs.get('update_fields', [])
    if issubclass(sender, Menu):
        cache_response.invalid_cache('MenuView_list_*')
        queryset = instance.userrole_set.values_list('userinfo', flat=True)
        invalid_superuser_cache()
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
            if instance.publish:
                push_notice_messages(instance, UserInfo.objects.values_list('pk', flat=True))
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
        if update_fields is None or {'roles', 'rules', 'dept', 'mode_type'} & set(update_fields):
            invalid_user_cache(instance.pk)
        else:
            cache_response.invalid_cache(f'UserInfoView_retrieve_{instance.pk}')
        logger.info(f"invalid cache {sender}")

    if issubclass(sender, NoticeUserRead):
        invalid_notify_cache(instance.owner.pk)

    if issubclass(sender, SystemConfig):
        SysConfig.invalid_config_cache(instance.key)
        if instance.key in ['PERMISSION_DATA', 'PERMISSION_FIELD']:
            invalid_user_cache('*')


@receiver([pre_delete])
def clean_cache_handler_pre_delete(sender, instance, **kwargs):
    if issubclass(sender, DeptInfo):
        if instance.userinfo_set.count():
            invalid_roles_cache(instance)
            logger.info(f"invalid cache {sender}")
