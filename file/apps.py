#!/usr/bin/env python
# -*- coding:utf-8 -*-
from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class FileConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "file"
    verbose_name = _("File")
