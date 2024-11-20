#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : department
# author : ly_13
# date : 8/10/2024

import json

from django.db import models
from django.utils.translation import gettext_lazy as _
from rest_framework.utils import encoders

from common.core.models import DbAuditModel, DbUuidModel
from system.models import ModeTypeAbstract


class DeptInfo(DbAuditModel, ModeTypeAbstract, DbUuidModel):
    name = models.CharField(verbose_name=_("Department name"), max_length=128)
    code = models.CharField(max_length=128, verbose_name=_("Department code"), unique=True)
    parent = models.ForeignKey('system.DeptInfo', on_delete=models.PROTECT, verbose_name=_("Superior department"),
                               null=True, blank=True, related_query_name="parent_query")
    roles = models.ManyToManyField("system.UserRole", verbose_name=_("Role permission"), blank=True)
    rules = models.ManyToManyField("system.DataPermission", verbose_name=_("Data permission"), blank=True)
    rank = models.IntegerField(verbose_name=_("Rank"), default=99)
    auto_bind = models.BooleanField(verbose_name=_("Auto bind"), default=False,
                                    help_text=_(
                                        "If the value of the registration parameter channel is consistent with the department code, the user is automatically bound to the department"))
    is_active = models.BooleanField(verbose_name=_("Is active"), default=True)

    @classmethod
    def recursion_dept_info(cls, dept_id: int, dept_all_list=None, dept_list=None, is_parent=False):
        parent = 'parent'
        pk = 'pk'
        if is_parent:
            parent, pk = pk, parent
        if not dept_all_list:
            dept_all_list = DeptInfo.objects.values("pk", "parent")
        if dept_list is None:
            dept_list = [dept_id]
        for dept in dept_all_list:
            if dept.get(parent) == dept_id:
                if dept.get(pk):
                    dept_list.append(dept.get(pk))
                    cls.recursion_dept_info(dept.get(pk), dept_all_list, dept_list, is_parent)
        return json.loads(json.dumps(list(set(dept_list)), cls=encoders.JSONEncoder))

    class Meta:
        verbose_name = _("Department")
        verbose_name_plural = verbose_name
        ordering = ("-rank", "-created_time",)

    def __str__(self):
        return f"{self.name}({self.pk})"
