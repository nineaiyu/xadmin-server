#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""审批实例 / 节点任务 / 讨论区评论模型（自 approval.py 拆分，表名 / 行为不变）。"""

import uuid

from django.contrib.contenttypes.fields import GenericRelation
from django.db import models
from django.utils.translation import gettext_lazy as _

from common.core.models import DbAuditModel


class ApprovalInstance(DbAuditModel):
    """流程实例（一次申请；creator = 申请人）。

    状态机：PENDING → APPROVED / REJECTED / CANCELLED（驳回与撤回均为终态，
    一期不做「驳回到上一节点」与「重新提交」）。current_node 为空表示已结束。
    """

    class Status(models.TextChoices):
        PENDING = "PENDING", _("Pending")
        APPROVED = "APPROVED", _("Approved")
        REJECTED = "REJECTED", _("Rejected")
        CANCELLED = "CANCELLED", _("Cancelled")

    # 通用标签（白名单对象）
    tagged_items = GenericRelation("system.TaggedItem")
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    flow = models.ForeignKey(
        "approval.ApprovalFlow", related_name="instances", on_delete=models.PROTECT, verbose_name=_("Flow")
    )
    # 快照：流程改名/改节点不影响历史实例展示
    flow_name = models.CharField(_("Flow name"), max_length=64)
    title = models.CharField(_("Title"), max_length=128)
    form_data = models.JSONField(_("Form data"), default=dict, blank=True)
    # 通用业务绑定：biz_type 为业务标识（如 "leave"），biz_id 为业务行主键
    # 字符串。业务模块经 create_instance(biz_type=..., biz_id=...) 挂载，实例终态时由
    # approval/utils/approval_flow/engine_events.py 的 _finish_instance 发 approval_instance_finished
    # 信号回写业务状态；两者皆空 = 引擎自带表单的独立申请（历史行为不变）。
    biz_type = models.CharField(_("Business type"), max_length=64, blank=True, default="", db_index=True)
    biz_id = models.CharField(_("Business id"), max_length=64, blank=True, default="")
    status = models.CharField(_("Status"), max_length=16, choices=Status.choices, default=Status.PENDING, db_index=True)
    # 发起时的流程定义版本号（钉住语义：推进按该版本过滤节点集，改版不影响在途单；
    # 空值（历史脏数据）回退「当前生效定义」，与绑版本改造前一致）
    flow_version = models.IntegerField(_("Flow version"), null=True, blank=True)
    current_node = models.ForeignKey(
        "approval.ApprovalFlowNode",
        related_name="+",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name=_("Current node"),
    )
    finished_at = models.DateTimeField(_("Finished at"), null=True, blank=True)
    reason = models.CharField(_("Reason"), max_length=255, blank=True, null=True)
    # 抄送人快照：发起时 = 全部可达节点 cc_users 并集 + 发起人追加（去重）；
    # 抄送人可查看实例详情、参与讨论，并在进入节点 / 实例终态时收到通知
    cc_users = models.ManyToManyField(
        "identity.UserInfo",
        related_name="approval_cc_instances",
        verbose_name=_("CC users"),
        blank=True,
    )

    class Meta:
        ordering = ["-created_time"]
        verbose_name = _("Approval instance")
        verbose_name_plural = verbose_name
        indexes = [
            models.Index(fields=["status", "created_time"], name="idx_appr_inst_status_created"),
            models.Index(fields=["creator", "created_time"], name="idx_appr_inst_creator_created"),
            models.Index(fields=["biz_type", "biz_id"], name="idx_appr_inst_biz"),
        ]

    def __str__(self):
        return f"{self.title} [{self.status}]"


class ApprovalNodeTask(DbAuditModel):
    """节点任务：一行一个候选审批人；actor = 实际处理人（或签时与 assignee 不同则代表他人已处理）。

    - 或签：任一行 APPROVED 后，同节点其余 PENDING 行置 CANCELLED（skipped）；
    - 会签：每行都需 APPROVED，任一行 REJECTED 则实例驳回、其余行置 CANCELLED；
    - is_added：加签产生的追加行（区别于流程定义解析出的初始行）。
    """

    class Status(models.TextChoices):
        PENDING = "PENDING", _("Pending")
        APPROVED = "APPROVED", _("Approved")
        REJECTED = "REJECTED", _("Rejected")
        CANCELLED = "CANCELLED", _("Cancelled")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    instance = models.ForeignKey(
        "approval.ApprovalInstance", related_name="tasks", on_delete=models.CASCADE, verbose_name=_("Instance")
    )
    node = models.ForeignKey(
        "approval.ApprovalFlowNode",
        related_name="+",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name=_("Node"),
    )
    # 快照：节点改名/删除后历史任务仍可读
    node_name = models.CharField(_("Node name"), max_length=64)
    node_order = models.IntegerField(_("Node order"), default=1)
    assignee = models.ForeignKey(
        "identity.UserInfo",
        related_name="approval_node_tasks",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name=_("Assignee"),
    )
    actor = models.ForeignKey(
        "identity.UserInfo",
        related_name="approval_node_acted_tasks",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name=_("Actor"),
    )
    # 处理人显示名快照：用户删除/改名后审批轨迹不丢痕迹
    assignee_display = models.CharField(_("Assignee display"), max_length=128, blank=True, default="")
    actor_display = models.CharField(_("Actor display"), max_length=128, blank=True, default="")
    # 委托代审来源：assignee 为代理人时记录原审批人（委托人生效替换），
    # 供审批轨迹标注「由 X 代理」；无委托的任务留空。
    delegate_from = models.ForeignKey(
        "identity.UserInfo",
        related_name="approval_node_delegated_tasks",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name=_("Delegate from"),
    )
    status = models.CharField(_("Status"), max_length=16, choices=Status.choices, default=Status.PENDING, db_index=True)
    comment = models.CharField(_("Comment"), max_length=255, blank=True, null=True)
    acted_at = models.DateTimeField(_("Acted at"), null=True, blank=True)
    is_added = models.BooleanField(_("Added by counter-sign"), default=False)

    class Meta:
        ordering = ["node_order", "created_time"]
        verbose_name = _("Approval node task")
        verbose_name_plural = verbose_name
        indexes = [
            models.Index(fields=["assignee", "status"], name="idx_appr_task_assignee_status"),
            models.Index(fields=["instance", "node_order"], name="idx_appr_task_inst_order"),
        ]

    def __str__(self):
        return f"{self.node_name} -> {self.assignee_id} [{self.status}]"


class ApprovalInstanceComment(DbAuditModel):
    """审批实例讨论区评论：审批沟通在单内闭环（不再依赖 IM 补充说明）。

    可见性/参与人与实例同口径（申请人 / 历史与当前处理人 / 抄送人经
    ``visible_instances_for`` 收敛）；@ 提醒复用聊天室提及解析（用户名），
    默认只对 @ 提及者推送（避免评论噪音）。
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    instance = models.ForeignKey(
        "approval.ApprovalInstance",
        related_name="comments",
        on_delete=models.CASCADE,
        verbose_name=_("Instance"),
    )
    content = models.TextField(_("Content"), max_length=2000)
    # 作者显示名快照（用户删除后讨论记录仍可读，与处理人快照同口径）
    author_display = models.CharField(_("Author display"), max_length=128, blank=True, default="")
    # @ 提及的用户 pk 列表（落库快照，供前端高亮与追溯）
    mentions = models.JSONField(_("Mentions"), default=list, blank=True)

    class Meta:
        ordering = ["created_time"]
        verbose_name = _("Approval instance comment")
        verbose_name_plural = verbose_name
        indexes = [
            models.Index(fields=["instance", "created_time"], name="idx_appr_comment_inst_created"),
        ]

    def __str__(self):
        return f"{self.instance_id} {self.author_display}: {self.content[:20]}"
