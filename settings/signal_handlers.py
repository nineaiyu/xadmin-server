#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : signal_handlers.py
# author : ly_13
# date : 7/31/2024

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver
from django.utils.functional import LazyObject

from common.signals import django_ready
from common.utils import get_logger
from common.utils.connection import RedisPubSub
from settings.models import Setting

logger = get_logger(__name__)


class SettingSubPub(LazyObject):
    def _setup(self):
        self._wrapped = RedisPubSub("settings")


setting_pub_sub = SettingSubPub()


@receiver(post_save, sender=Setting)
def refresh_settings_on_changed(sender, instance=None, **kwargs):
    if not instance:
        return
    setting_pub_sub.publish((instance.name, instance.cleaned_value))


@receiver(post_delete, sender=Setting)
def reset_settings_on_deleted(sender, instance=None, **kwargs):
    """删除 Setting 行后回收运行时热更值（T03-07）：恢复静态默认值并广播。

    绕过 UI 的删除（批量删除 / admin / 脚本）此前无人回收——被删键的旧值在
    进程内继续生效，形同删除失败。选 post_delete 而非 pre_delete：删除 SQL
    成功后才触发；ATOMIC_REQUESTS 回滚的极端窗口最多多广播一次「恢复默认」
    （与 post_save 即时发布的既有口径一致），不会出现「行已删、值残留」。
    广播先于本进程应用：发布异常会让删除请求回滚（Redis 不可用时删除失败
    且状态一致），本进程应用随后执行（同进程订阅者也会收到消息，幂等）。
    """
    if not instance:
        return
    default = Setting.default_value(instance.name)
    setting_pub_sub.publish((instance.name, default))
    Setting.refresh_item((instance.name, default))


@receiver(django_ready)
def on_django_ready_add_db_config(sender, **kwargs):
    Setting.refresh_all_settings()


@receiver(django_ready)
def subscribe_settings_change(sender, **kwargs):
    logger.debug("Start subscribe setting change")

    setting_pub_sub.subscribe(lambda name: Setting.refresh_item(name))
