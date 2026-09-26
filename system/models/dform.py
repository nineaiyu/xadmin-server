#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""动态表单：收敛控件集 JSON Schema + 通用 JSON 存储提交。"""

from django.db import models
from django.utils.translation import gettext_lazy as _

from common.core.models import DbAuditModel, DbUuidModel


class DynamicForm(DbAuditModel, DbUuidModel):
    """表单定义：收敛控件集 JSON Schema（写入侧双侧校验）。"""

    name = models.CharField(_("Name"), max_length=128, unique=True)
    description = models.CharField(_("Description"), max_length=512, blank=True, default="")
    schema = models.JSONField(_("Schema"), default=dict, help_text=_("Constrained widget-set field definitions"))
    is_active = models.BooleanField(_("Is active"), default=True)
    # 开启后提交走操作审批（提交 → 412 待审批 → 审批人通过 → 申请人携令牌重放）；
    # 绑定审批流程（approval_flow）时优先走流程引擎，本开关被忽略
    approval_required = models.BooleanField(_("Approval required"), default=False)
    # 绑定审批流程：提交进入流程引擎（多级审批），实例终态回写提交状态
    approval_flow = models.ForeignKey(
        "approval.ApprovalFlow",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="bound_forms",
        verbose_name=_("Approval flow"),
    )
    # 表单模板：模板行只保存 schema（供「从模板新建」复用），不进入可填报表单列表，
    # 也不参与填报（available-forms / 提交外键均排除）；管理入口 = 表单设计器。
    is_template = models.BooleanField(_("Is template"), default=False, db_index=True)

    class Meta:
        verbose_name = _("Dynamic form")
        verbose_name_plural = _("Dynamic forms")
        ordering = ("-created_time",)

    def __str__(self):
        return self.name


class DynamicFormSubmission(DbAuditModel, DbUuidModel):
    """表单提交：通用 JSON 存储，按表单 schema 校验；可见性按 creator 隔离。

    状态：空 = 无需审批（直接生效）；绑定审批流程时随实例终态回写
    （PENDING → APPROVED / REJECTED / CANCELLED）；驳回后允许修改数据重新提交。
    DRAFT = 草稿（暂存不提交，允许缺必填字段，提交时统一按 schema 严格校验）。
    """

    class Status(models.TextChoices):
        DRAFT = "DRAFT", _("Draft")
        PENDING = "PENDING", _("Pending")
        APPROVED = "APPROVED", _("Approved")
        REJECTED = "REJECTED", _("Rejected")
        CANCELLED = "CANCELLED", _("Cancelled")

    form = models.ForeignKey(
        DynamicForm, on_delete=models.CASCADE, related_name="submissions", verbose_name=_("Dynamic form")
    )
    data = models.JSONField(_("Data"), default=dict)
    status = models.CharField(
        _("Status"),
        max_length=16,
        choices=Status.choices,
        blank=True,
        default="",
        db_index=True,
    )
    instance = models.ForeignKey(
        "approval.ApprovalInstance",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="dform_submissions",
        verbose_name=_("Approval instance"),
    )

    class Meta:
        verbose_name = _("Dynamic form submission")
        verbose_name_plural = _("Dynamic form submissions")
        ordering = ("-created_time",)

    def __str__(self):
        return f"{self.form_id}:{self.creator_id}"
