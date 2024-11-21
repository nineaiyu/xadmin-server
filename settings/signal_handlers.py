#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : signal_handlers.py
# author : ly_13
# date : 7/31/2024

from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils.functional import LazyObject

from common.signals import django_ready
from common.utils import get_logger
from common.utils.connection import RedisPubSub
from settings.models import Setting

logger = get_logger(__name__)


class SettingSubPub(LazyObject):
    def _setup(self):
        self._wrapped = RedisPubSub('settings')


setting_pub_sub = SettingSubPub()


@receiver(post_save, sender=Setting)
def refresh_settings_on_changed(sender, instance=None, **kwargs):
    if not instance:
        return
    setting_pub_sub.publish((instance.name, instance.cleaned_value))


@receiver(django_ready)
def on_django_ready_add_db_config(sender, **kwargs):
    Setting.refresh_all_settings()


@receiver(django_ready)
def subscribe_settings_change(sender, **kwargs):
    logger.debug("Start subscribe setting change")

    setting_pub_sub.subscribe(lambda name: Setting.refresh_item(name))
