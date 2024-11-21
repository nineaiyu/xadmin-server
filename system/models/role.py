#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : role
# author : ly_13
# date : 8/10/2024

from django.db import models
from django.utils.translation import gettext_lazy as _

from common.core.models import DbAuditModel, DbUuidModel


class UserRole(DbAuditModel, DbUuidModel):
    name = models.CharField(max_length=128, verbose_name=_("Role name"), unique=True)
    code = models.CharField(max_length=128, verbose_name=_("Role code"), unique=True)
    is_active = models.BooleanField(verbose_name=_("Is active"), default=True)
    menu = models.ManyToManyField('system.Menu', verbose_name=_("Menu"), blank=True)

    class Meta:
        verbose_name = _("User role")
        verbose_name_plural = verbose_name
        ordering = ("-created_time",)

    def __str__(self):
        return f"{self.name}({self.code})"
