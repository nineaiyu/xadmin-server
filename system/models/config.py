#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : config
# author : ly_13
# date : 8/10/2024


from django.db import models
from django.utils.translation import gettext_lazy as _

from common.core.models import DbAuditModel, DbUuidModel


class BaseConfig(DbAuditModel):
    value = models.JSONField(max_length=10240, verbose_name=_("Config value"))
    is_active = models.BooleanField(default=True, verbose_name=_("Is active"))
    access = models.BooleanField(default=False, verbose_name=_("API access"),
                                 help_text=_("Allows API interfaces to access this config"))

    class Meta:
        abstract = True


class SystemConfig(BaseConfig, DbUuidModel):
    key = models.CharField(max_length=255, unique=True, verbose_name=_("Config name"))
    inherit = models.BooleanField(default=False, verbose_name=_("User inherit"),
                                  help_text=_("Allows users to inherit this config"))

    class Meta:
        verbose_name = _("System config")
        verbose_name_plural = verbose_name

    def __str__(self):
        return "%s-%s" % (self.key, self.description)


class UserPersonalConfig(BaseConfig):
    owner = models.ForeignKey("system.UserInfo", verbose_name=_("User"), on_delete=models.CASCADE)
    key = models.CharField(max_length=255, verbose_name=_("Config name"))

    class Meta:
        verbose_name = _("User config")
        verbose_name_plural = verbose_name
        unique_together = (('owner', 'key'),)

    def __str__(self):
        return "%s-%s" % (self.key, self.description)
