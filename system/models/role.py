#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : role
# author : ly_13
# date : 8/10/2024

from django.db import models
from django.utils.translation import gettext_lazy as _

from common.core.models import DbAuditModel, DbUuidModel, SoftDeleteModel


class UserRole(SoftDeleteModel, DbAuditModel, DbUuidModel):
    """角色软删除——删除进入回收站，post_save 信号自动失效权限缓存，
    回收站可恢复；name/code 唯一约束仅作用于未删除数据（见 Meta.constraints），
    已删除角色释放其名称与编码。
    """

    name = models.CharField(max_length=128, verbose_name=_("Role name"))
    code = models.CharField(max_length=128, verbose_name=_("Role code"))
    is_active = models.BooleanField(verbose_name=_("Is active"), default=True)
    menu = models.ManyToManyField("system.Menu", verbose_name=_("Menu"), blank=True)

    class Meta:
        verbose_name = _("User role")
        verbose_name_plural = verbose_name
        ordering = ("-created_time",)
        constraints = [
            models.UniqueConstraint(
                fields=["name"], condition=models.Q(deleted_at__isnull=True), name="uniq_userrole_name_active"
            ),
            models.UniqueConstraint(
                fields=["code"], condition=models.Q(deleted_at__isnull=True), name="uniq_userrole_code_active"
            ),
        ]

    def __str__(self):
        return f"{self.name}({self.code})"
