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
from django.utils.translation import activate
from django.utils.translation import gettext_lazy as _

from common.base.magic import cache_response, MagicCacheData
from common.core.config import SysConfig
from common.core.models import DbAuditModel
from common.core.serializers import get_sub_serializer_fields
from common.core.utils import PrintLogFormat
from system.models import Menu, UserRole, UserInfo, DeptInfo, DataPermission, SystemConfig, ModelLabelField

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
    plf = PrintLogFormat(f"App:({label})")
    activate(settings.LANGUAGE_CODE)
    field_type = ModelLabelField.FieldChoices.DATA
    obj, created = ModelLabelField.objects.update_or_create(name=f"*", field_type=field_type,
                                                            defaults={'label': _("All tables")}, parent=None)
    ModelLabelField.objects.update_or_create(name=f"*", field_type=field_type, parent=obj,
                                             defaults={'label': _("All fields")})
    for field in DbAuditModel._meta.fields:
        ModelLabelField.objects.update_or_create(name=field.name, field_type=field_type, parent=obj,
                                                 defaults={'label': getattr(field, 'verbose_name', field.name)})
    for model in sender.models.values():
        count = [0, 0]
        delete = True
        model_name = model._meta.model_name
        verbose_name = model._meta.verbose_name
        if not hasattr(model, 'Meta'):  # 虚拟 model 判断, 不包含Meta的模型，是系统生成的第三方模型，包含 relationship
            continue
        obj, created = ModelLabelField.objects.update_or_create(name=f"{label}.{model_name}", field_type=field_type,
                                                                parent=None, defaults={'label': verbose_name})
        count[int(not created)] += 1
        # for field in model._meta.get_fields():
        for field in model._meta.fields:
            _obj, created = ModelLabelField.objects.update_or_create(name=field.name, parent=obj, field_type=field_type,
                                                                     defaults={'label': field.verbose_name})
            count[int(not created)] += 1
        PrintLogFormat(f"Model:({label}.{model_name})").warning(
            f"update_or_create data permission, created:{count[0]} updated:{count[1]}")
        # defaults={'label': getattr(field, 'verbose_name', field.through._meta.verbose_name)})
    if delete:
        deleted, _rows_count = ModelLabelField.objects.filter(field_type=field_type, updated_time__lt=now,
                                                              name__startswith=f"{label}.").delete()
        plf.info(f"deleted success, deleted:{deleted} row_count {_rows_count}")
    if label == settings.PERMISSION_DATA_AUTH_APPS[-1]:
        try:
            get_sub_serializer_fields()
        except Exception as e:
            plf.error(f"auto get sub serializer fields failed. {e}")


@receiver(m2m_changed)
def clean_m2m_cache_handler(sender, instance, **kwargs):
    if kwargs.get('action') in ['post_add', 'pre_remove']:

        if isinstance(instance, UserInfo):  # 分配用户角色，需要同时清理用户路由和用户信息
            invalid_user_cache(instance.pk)

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


def invalid_user_cache(user_pk):
    cache_response.invalid_cache(f'UserInfoView_retrieve_{user_pk}')
    cache_response.invalid_cache(f'UserRoutesView_get_{user_pk}')
    MagicCacheData.invalid_cache(f'get_user_permission_{user_pk}')  # 清理权限
    MagicCacheData.invalid_cache(f'get_user_field_queryset_{user_pk}')  # 清理权限
    cache_response.invalid_cache(f'MenuView_list_{user_pk}_*')


def invalid_superuser_cache():
    for pk in UserInfo.objects.filter(is_superuser=True).values_list('pk', flat=True):
        invalid_user_cache(pk)


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

    if issubclass(sender, UserInfo):
        if update_fields is None or {'roles', 'rules', 'dept', 'mode_type'} & set(update_fields):
            invalid_user_cache(instance.pk)
        else:
            cache_response.invalid_cache(f'UserInfoView_retrieve_{instance.pk}')
        logger.info(f"invalid cache {sender}")

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
