#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : abstract
# author : ly_13
# date : 8/10/2024

from django.db import models
from django.utils.translation import gettext_lazy as _


class ModeTypeAbstract(models.Model):
    class ModeChoices(models.IntegerChoices):
        OR = 0, _("Or mode")
        AND = 1, _("And mode")

    mode_type = models.SmallIntegerField(choices=ModeChoices, default=ModeChoices.OR,
                                         verbose_name=_("Data permission mode"),
                                         help_text=_(
                                             "Permission mode, and the mode indicates that the data needs to satisfy each rule in the rule list at the same time, or the mode satisfies any rule"))

    class Meta:
        abstract = True
