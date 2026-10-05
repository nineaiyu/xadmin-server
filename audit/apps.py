#!/usr/bin/env python
# -*- coding:utf-8 -*-
from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class AuditConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "audit"
    verbose_name = _("Audit")

    def ready(self):
        from . import signal_handler  # noqa: F401  脱敏规则缓存失效接收器注册
