#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : signal_handler.py
# author : ly_13
# date : 12/15/2023

import logging

from django.db.models.signals import pre_save, pre_delete, m2m_changed
from django.dispatch import receiver
from rest_framework.exceptions import PermissionDenied
from system.models import *
logger = logging.getLogger(__name__)

def raise_error(instance):
    if instance._meta.app_label in ['system']:
        raise PermissionDenied("演示模式，禁止操作")


@receiver(m2m_changed)
def clean_m2m_cache_handler(sender, instance, **kwargs):
    if kwargs.get('action') in ['pre_add', 'pre_remove']:
        raise_error(instance)


@receiver([pre_save, pre_delete])
def clean_cache_handler_pre_delete(sender, instance, **kwargs):
    if isinstance(instance, OperationLog):
        return
    if isinstance(instance, UserInfo) and kwargs.get('update_fields', {}) == {'last_login'}:
        return
    raise_error(instance)
