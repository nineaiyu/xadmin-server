#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""请假业务单：审批流引擎的第一个真实业务接入方。

设计要点：
- 业务单只保存业务事实（类型/起止/天数/事由）+ 审批状态 + 流程实例外键；
  「当前节点 / 审批轨迹 / 审批意见」一律读实例（ApprovalInstance/ApprovalNodeTask），
  不在业务单上冗余，避免两处状态不一致；
- 状态流转：DRAFT（草稿，未提交）→ PENDING（审批中）→ APPROVED / REJECTED，
  撤回回到 CANCELLED；提交时经 ``create_instance(biz_type="leave", biz_id=...)``
  挂载流程实例，实例终态由 ``approval_instance_finished`` 信号回写本单状态。
"""

from django.db import models
from django.utils.translation import gettext_lazy as _

from common.core.models import DbAuditModel, DbUuidModel


class Leave(DbAuditModel, DbUuidModel):
    """请假申请单（creator = 申请人）。"""

    class LeaveType(models.TextChoices):
        ANNUAL = "annual", _("Annual leave")
        SICK = "sick", _("Sick leave")
        PERSONAL = "personal", _("Personal leave")
        COMP_TIME = "comp_time", _("Compensatory leave")
        MARRIAGE = "marriage", _("Marriage leave")
        OTHER = "other", _("Other")

    class Status(models.TextChoices):
        DRAFT = "DRAFT", _("Draft")
        PENDING = "PENDING", _("Pending")
        APPROVED = "APPROVED", _("Approved")
        REJECTED = "REJECTED", _("Rejected")
        CANCELLED = "CANCELLED", _("Cancelled")

    leave_type = models.CharField(_("Leave type"), max_length=32, choices=LeaveType.choices, default=LeaveType.ANNUAL)
    start_date = models.DateField(_("Start date"))
    end_date = models.DateField(_("End date"))
    # 支持半天（0.5 步进）；提交时校验不得超过起止跨度（见 system/utils/leave.py）
    days = models.DecimalField(_("Days"), max_digits=5, decimal_places=1)
    reason = models.CharField(_("Reason"), max_length=500)
    status = models.CharField(_("Status"), max_length=16, choices=Status.choices, default=Status.DRAFT, db_index=True)
    # 审批流程实例：撤回/驳回后重新提交会换成新实例（历史实例留在引擎侧可追溯）
    instance = models.ForeignKey(
        "system.ApprovalInstance",
        related_name="leaves",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name=_("Approval instance"),
    )

    class Meta:
        ordering = ["-created_time"]
        verbose_name = _("Leave request")
        verbose_name_plural = verbose_name
        indexes = [
            models.Index(fields=["status", "created_time"], name="idx_leave_status_created"),
            models.Index(fields=["creator", "start_date"], name="idx_leave_creator_start"),
        ]

    def __str__(self):
        return f"{self.get_leave_type_display()}({self.start_date}~{self.end_date}) [{self.status}]"

    @property
    def form_data(self) -> dict:
        """提交审批时随实例走的表单数据（条件节点与字段审批人按这些 key 取值）。"""
        return {
            "leave_type": self.leave_type,
            "start_date": self.start_date.isoformat() if self.start_date else "",
            "end_date": self.end_date.isoformat() if self.end_date else "",
            "days": float(self.days or 0),
            "reason": self.reason,
        }

    @property
    def approval_title(self) -> str:
        return f"{self.get_leave_type_display()} {self.start_date}~{self.end_date}（{self.days}天）"
