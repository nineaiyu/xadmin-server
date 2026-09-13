#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""动态表单（ADR-025）：收敛控件集 JSON Schema + 通用 JSON 存储提交。"""

from django.db import models
from django.utils.translation import gettext_lazy as _

from common.core.models import DbAuditModel, DbUuidModel


class DynamicForm(DbAuditModel, DbUuidModel):
    """表单定义：收敛控件集 JSON Schema（写入侧双侧校验）。"""

    name = models.CharField(_("Name"), max_length=128, unique=True)
    description = models.CharField(_("Description"), max_length=512, blank=True, default="")
    schema = models.JSONField(_("Schema"), default=dict, help_text=_("Constrained widget-set field definitions"))
    is_active = models.BooleanField(_("Is active"), default=True)
    # G5b：开启后提交走审批流（提交 → 412 待审批 → 审批人通过 → 申请人携令牌重放）
    approval_required = models.BooleanField(_("Approval required"), default=False)

    class Meta:
        verbose_name = _("Dynamic form")
        verbose_name_plural = _("Dynamic forms")
        ordering = ("-created_time",)

    def __str__(self):
        return self.name


class DynamicFormSubmission(DbAuditModel, DbUuidModel):
    """表单提交：通用 JSON 存储，按表单 schema 校验；可见性按 creator 隔离。"""

    form = models.ForeignKey(
        DynamicForm, on_delete=models.CASCADE, related_name="submissions", verbose_name=_("Dynamic form")
    )
    data = models.JSONField(_("Data"), default=dict)

    class Meta:
        verbose_name = _("Dynamic form submission")
        verbose_name_plural = _("Dynamic form submissions")
        ordering = ("-created_time",)

    def __str__(self):
        return f"{self.form_id}:{self.creator_id}"
