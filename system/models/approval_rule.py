#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""敏感操作审批：多级审批链模型（审批规则 + 级次定义 + 在途单级次快照）。

目标能力（「删书籍由 A 审、删用户由 B 审；A 审完再通知 B」）：

- ``ApprovalRule`` 按请求路径匹配（与 ``APPROVAL_REQUIRED_PATHS`` 同口径的路径正则
  清单），命中后由 ``ApprovalRuleLevel`` 定义的有序级次逐级审批；
- 未命中任何规则时回退既有全局审批人逻辑（``APPROVAL_APPROVER_ROLES`` /
  ``APPROVAL_APPROVER_PERMS`` / 超管），即本组模型是纯增量能力，不改动存量行为；
- ``ApprovalRequestStep`` 是建单瞬间的级次快照（含每级候选人关联）：
  规则后续改动不影响在途单；逐级通过/驳回全程留痕，供列表与详情展示进度。
"""

import uuid

from django.db import models
from django.utils.translation import gettext_lazy as _

from common.core.models import DbAuditModel


class ApprovalRule(DbAuditModel):
    """审批规则：路径命中 → 用多级审批链替换全局审批人集合。

    - path_patterns：路径正则清单（``re.search`` 命中即算，写法与系统配置
      APPROVAL_REQUIRED_PATHS 一致，如 ``["api/demo/book/(?P<pk>[^/.]+)$"]``）；
    - priority：多条规则同时命中时取 priority 最大者（并列取创建时间更新者）；
    - is_active=False 不参与匹配。
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(_("Rule name"), max_length=64)
    path_patterns = models.JSONField(_("Path patterns"), default=list, blank=True)
    priority = models.IntegerField(_("Priority"), default=0)
    is_active = models.BooleanField(_("Is active"), default=True, db_index=True)
    remark = models.CharField(_("Remark"), max_length=255, blank=True, null=True)

    class Meta:
        ordering = ["-priority", "-created_time"]
        verbose_name = _("Approval rule")
        verbose_name_plural = verbose_name

    def __str__(self):
        return f"{self.name}({self.priority})"


class ApprovalRuleLevel(DbAuditModel):
    """规则级次：order 升序即审批顺序；值支持多个人/多个角色。

    assignee_value 语义：
    - assignee_type=user：用户名（逗号分隔多选，如 ``"zhangsan,lisi"``）；
    - assignee_type=role：角色 code（逗号分隔多选，如 ``"book_approver,SystemAdmin"``）。

    approve_type（级内多人审批方式，与流程引擎口径一致）：
    - OR（或签，默认）：任一候选人通过即该级通过，进入下一级；
    - AND（会签）：全部候选人通过才进入下一级；任一人驳回即整单终止。
    """

    class AssigneeType(models.TextChoices):
        USER = "user", _("User")
        ROLE = "role", _("Role")

    class ApproveType(models.TextChoices):
        OR = "OR", _("Any one approves")
        AND = "AND", _("All must approve")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    rule = models.ForeignKey(
        "system.ApprovalRule",
        related_name="levels",
        on_delete=models.CASCADE,
        verbose_name=_("Rule"),
    )
    name = models.CharField(_("Level name"), max_length=64, blank=True, default="")
    order = models.PositiveSmallIntegerField(_("Level order"), default=1)
    approve_type = models.CharField(
        _("Approve type"), max_length=8, choices=ApproveType.choices, default=ApproveType.OR
    )
    assignee_type = models.CharField(
        _("Assignee type"), max_length=8, choices=AssigneeType.choices, default=AssigneeType.USER
    )
    assignee_value = models.CharField(_("Assignee value"), max_length=255, blank=True, default="")

    class Meta:
        ordering = ["order", "created_time"]
        verbose_name = _("Approval rule level")
        verbose_name_plural = verbose_name
        constraints = [
            models.UniqueConstraint(fields=["rule", "order"], name="uniq_approval_rule_level_order"),
        ]

    def __str__(self):
        return f"{self.rule_id}#{self.order} {self.assignee_type}:{self.assignee_value}"


class ApprovalRequestStep(DbAuditModel):
    """在途审批单的级次快照：一行一级，assignees 为建单瞬间的候选人。

    - 当前级 = status=PENDING 的最小 order（主单 ApprovalRequest.current_level 同步冗余）；
    - 或签（OR）：任一人通过即该级 APPROVED 并推进下一级；
    - 会签（AND）：逐人记录通过动作（ApprovalRequestStepAction），全部候选人通过
      才置 APPROVED 并推进下一级；任一人驳回即整单终止；
    - 任一级驳回：该级置 REJECTED、其余 PENDING 级置 CANCELLED、整单驳回；
    - 申请人撤回 / 超时过期：PENDING 级统一置 CANCELLED。
    """

    class Status(models.TextChoices):
        PENDING = "PENDING", _("Pending")
        APPROVED = "APPROVED", _("Approved")
        REJECTED = "REJECTED", _("Rejected")
        CANCELLED = "CANCELLED", _("Cancelled")

    # 复用规则级次的审批方式枚举（快照字段与规则同源，避免两处定义漂移）
    ApproveType = ApprovalRuleLevel.ApproveType

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request = models.ForeignKey(
        "system.ApprovalRequest",
        related_name="steps",
        on_delete=models.CASCADE,
        verbose_name=_("Approval request"),
    )
    # 级次名称/类型/值均为建单快照（规则改动不影响历史展示）
    name = models.CharField(_("Level name"), max_length=64, blank=True, default="")
    order = models.PositiveSmallIntegerField(_("Level order"), default=1)
    assignee_type = models.CharField(_("Assignee type"), max_length=8, blank=True, default="")
    assignee_value = models.CharField(_("Assignee value"), max_length=255, blank=True, default="")
    # 审批方式快照（OR/AND）：规则改动不影响在途单的判定口径
    approve_type = models.CharField(
        _("Approve type"),
        max_length=8,
        choices=ApprovalRuleLevel.ApproveType.choices,
        default=ApprovalRuleLevel.ApproveType.OR,
    )
    # 候选人快照（一行一人，M2M）；当前级候选人另有主单冗余投影 current_assignees
    assignees = models.ManyToManyField(
        "system.UserInfo",
        related_name="approval_step_assignments",
        blank=True,
        verbose_name=_("Assignees"),
    )
    status = models.CharField(_("Status"), max_length=16, choices=Status.choices, default=Status.PENDING, db_index=True)
    approver = models.ForeignKey(
        "system.UserInfo",
        related_name="acted_approval_steps",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name=_("Approver"),
    )
    # 处理人显示名快照（U-1）：用户删除/改名后审批痕迹仍可读
    approver_display = models.CharField(_("Approver display"), max_length=128, blank=True, default="")
    comment = models.CharField(_("Comment"), max_length=255, blank=True, null=True)
    acted_at = models.DateTimeField(_("Acted at"), null=True, blank=True)

    class Meta:
        ordering = ["order", "created_time"]
        verbose_name = _("Approval request step")
        verbose_name_plural = verbose_name
        constraints = [
            models.UniqueConstraint(fields=["request", "order"], name="uniq_approval_request_step_order"),
        ]
        indexes = [
            models.Index(fields=["request", "status"], name="idx_appr_step_req_status"),
        ]

    def __str__(self):
        return f"{self.request_id}#{self.order} [{self.status}]"


class ApprovalRequestStepAction(DbAuditModel):
    """级次内的逐人审批动作（会签留痕）：一行 = 某人通过/驳回该级。

    - 或签（OR）：至多 1 行（任一人通过即推进，其余人不再可操作）；
    - 会签（AND）：每位候选人通过各记一行（重复提交被唯一约束挡住），
      已通过人数 = 候选人数量时该级完成；
    - 驳回同样记一行（status=REJECTED），整单终止后保留留痕。
    """

    class Status(models.TextChoices):
        APPROVED = "APPROVED", _("Approved")
        REJECTED = "REJECTED", _("Rejected")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    step = models.ForeignKey(
        "system.ApprovalRequestStep",
        related_name="actions",
        on_delete=models.CASCADE,
        verbose_name=_("Approval step"),
    )
    approver = models.ForeignKey(
        "system.UserInfo",
        related_name="approval_step_actions",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name=_("Approver"),
    )
    # 处理人显示名快照（U-1）：用户删除/改名后审批痕迹仍可读
    approver_display = models.CharField(_("Approver display"), max_length=128, blank=True, default="")
    status = models.CharField(_("Status"), max_length=16, choices=Status.choices, default=Status.APPROVED)
    comment = models.CharField(_("Comment"), max_length=255, blank=True, null=True)

    class Meta:
        ordering = ["created_time"]
        verbose_name = _("Approval step action")
        verbose_name_plural = verbose_name
        constraints = [
            models.UniqueConstraint(fields=["step", "approver"], name="uniq_approval_step_action_user"),
        ]

    def __str__(self):
        return f"{self.step_id}:{self.approver_id} [{self.status}]"
