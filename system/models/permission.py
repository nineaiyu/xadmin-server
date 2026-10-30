#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : permission
# author : ly_13
# date : 8/10/2024
from typing import Any

from django.db import models
from django.utils.translation import gettext_lazy as _

from common.core.models import DbAuditModel, DbCharModel, DbUuidModel, SoftDeleteModel
from system.models import ModeTypeAbstract


# 软删：规则误删会立即改变被授权用户的可见数据，必须可回收（回收站恢复/物理清除）。
# SoftDeleteModel 必须位于 MRO 首位，delete() 才优先于审计基类解析。
class DataPermission(SoftDeleteModel, DbAuditModel, ModeTypeAbstract, DbUuidModel):
    name = models.CharField(verbose_name=_("Name"), max_length=255, unique=True)
    rules = models.JSONField(verbose_name=_("Rules"), max_length=10240)
    is_active = models.BooleanField(verbose_name=_("Is active"), default=True)
    menu = models.ManyToManyField(
        "system.Menu",
        verbose_name=_("Menu"),
        blank=True,
        help_text=_("If a menu exists, it only applies to the selected menu permission"),
    )

    class Meta:
        ordering = ("-created_time",)
        verbose_name = _("Data permission")
        verbose_name_plural = verbose_name

    def __str__(self) -> str:
        return f"{self.name}"


class FieldPermission(DbAuditModel, DbCharModel):
    role = models.ForeignKey("identity.UserRole", on_delete=models.CASCADE, verbose_name=_("Role"))
    menu = models.ForeignKey("system.Menu", on_delete=models.CASCADE, verbose_name=_("Menu"))
    field = models.ManyToManyField("system.ModelLabelField", verbose_name=_("Field"), blank=True)

    class Meta:
        verbose_name = _("Field permission")
        verbose_name_plural = verbose_name
        ordering = ("-created_time",)
        unique_together = ("role", "menu")

    def save(self, *args: Any, **kwargs: Any) -> Any:
        self.id = f"{self.role.pk}-{self.menu.pk}"
        return super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.pk}-{self.role.name}-{self.created_time}"
