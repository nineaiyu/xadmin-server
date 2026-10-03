#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""动态表单：收敛控件集 JSON Schema + 通用 JSON 存储提交。"""

from django.contrib.postgres.indexes import GinIndex
from django.db import models
from django.utils.translation import gettext_lazy as _

from common.core.models import DbAuditModel, DbUuidModel

#: schema 历史版本保留条数（回滚窗口；超出丢弃最旧，避免 JSON 列无界增长）
MAX_SCHEMA_HISTORY = 20


class DynamicForm(DbAuditModel, DbUuidModel):
    """表单定义：收敛控件集 JSON Schema（写入侧双侧校验）。

    版本化：schema 每次**实质变更**（规范化后不等）版本 +1，变更前快照进
    ``schema_history``（含联动规则，新→旧）；提交记录引用提交时的版本
    （``DynamicFormSubmission.schema_version``），历史版本可查看/回滚。
    """

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
    # schema 版本：每次实质变更 +1；回滚同样生成新版本（不删除历史）
    schema_version = models.PositiveIntegerField(_("Schema version"), default=1)
    # 历史版本快照（新 → 旧）：[{version, schema, updated_time, updated_by}]，上限 MAX_SCHEMA_HISTORY
    schema_history = models.JSONField(_("Schema history"), default=list, blank=True)

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
    # 可筛选字段的物化列（设计器勾选 filterable）：提交时写入这些字段的规范化取值，
    # 列表筛选编译为单条 JSON 包含查询（``filter_data @> {...}``，PostgreSQL 走 GIN）
    # ——数据仍在 data 列，本列只是筛选索引面，见 dataset/utils/dform_filter.py
    filter_data = models.JSONField(_("Filter data"), default=dict, blank=True)
    # 保存时的表单 schema 版本（审计与展示口径；提交校验始终按提交当时的 schema）
    schema_version = models.PositiveIntegerField(_("Form schema version"), default=1)
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
        indexes = [
            # 物化筛选列的 GIN 索引（PostgreSQL 生效；其它后端按不支持跳过，语义不变）
            GinIndex(fields=["filter_data"], name="idx_dformsub_filter_gin"),
        ]

    def __str__(self):
        return f"{self.form_id}:{self.creator_id}"
