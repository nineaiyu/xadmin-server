#!/usr/bin/env python
# -*- coding:utf-8 -*-
from django.apps import AppConfig


class IdentityConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "identity"

    def ready(self) -> None:
        from . import signal_handler  # noqa: F401  缓存失效接收器注册
