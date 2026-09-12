#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""审批域模型：轻量敏感操作审批 + 全量审批流引擎一期。

一、ApprovalRequest（轻量：一次性通行令牌）
拦截点（common/core/approval.py 的 ApprovalRequired 装饰器）在业务执行前建
PENDING 单并通知审批人；审批通过后由原始客户端在有效期内携带 approval_id 重发
同一请求，消费令牌后放行。不做服务端请求重放（multipart/大 body 重放不可靠），
含文件的敏感操作不支持审批。

状态机：
- PENDING   待审批（超 PENDING_TIMEOUT 由清理任务置 EXPIRED）
- APPROVED  已通过（expired_at 前可消费一次，消费后 consume_time 落值）
- REJECTED  已驳回（reason 必填）
- CANCELLED 申请人撤回
- EXPIRED   待审批超时 / 令牌过期
- FAILED    消费校验失败（重发请求与快照指纹不一致，留审计痕迹）

二、ApprovalFlow / ApprovalFlowNode / ApprovalInstance / ApprovalNodeTask（全量引擎一期，ADR-012）
面向业务表单的多级审批：流程定义 = 顺序节点列表（节点级条件表达式决定是否经过该
节点），节点支持或签（任一通过）/ 会签（全部通过），审批人支持 角色 / 指定用户 /
申请人上级（部门 leader）/ 表单字段（值为用户名列表）。驳回即终止（不走回退上一
节点），撤回仅限申请人且仅 PENDING。节点任务一行一个候选审批人，加签在当前节点
追加候选（is_added=True）。
"""

import uuid

from django.db import models
from django.utils.translation import gettext_lazy as _

from common.core.models import DbAuditModel


class ApprovalRequest(DbAuditModel):
    """敏感操作审批单（creator = 申请人，approver = 审批人）。"""

    class Status(models.TextChoices):
        PENDING = "PENDING", _("Pending")
        APPROVED = "APPROVED", _("Approved")
        REJECTED = "REJECTED", _("Rejected")
        EXPIRED = "EXPIRED", _("Expired")
        CANCELLED = "CANCELLED", _("Cancelled")
        FAILED = "FAILED", _("Failed")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    module = models.CharField(_("Module"), max_length=64, blank=True, null=True)
    # 请求指纹：method + path + 脱敏后 body 快照（params），消费时逐一比对
    method = models.CharField(_("Method"), max_length=8)
    path = models.CharField(_("URL path"), max_length=400)
    object_pk = models.CharField(_("Object pk"), max_length=64, blank=True, null=True)
    params = models.JSONField(_("Request params"), default=dict, blank=True)
    status = models.CharField(
        _("Status"),
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    approver = models.ForeignKey(
        "system.UserInfo",
        related_name="approved_requests",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name=_("Approver"),
    )
    approved_at = models.DateTimeField(_("Approved at"), null=True, blank=True)
    # 令牌有效期：审批通过时置为 approved_at + APPROVAL_TOKEN_TTL
    expired_at = models.DateTimeField(_("Token expired at"), null=True, blank=True)
    consume_time = models.DateTimeField(_("Consume time"), null=True, blank=True)
    reason = models.CharField(_("Reason"), max_length=255, blank=True, null=True)

    class Meta:
        ordering = ["-created_time"]
        verbose_name = _("Approval request")
        verbose_name_plural = verbose_name
        indexes = [
            models.Index(fields=["status", "created_time"], name="idx_approval_status_created"),
            models.Index(fields=["creator", "created_time"], name="idx_approval_creator_created"),
        ]

    def __str__(self):
        return f"{self.method} {self.path} ({self.status})"


class ApprovalFlow(DbAuditModel):
    """审批流程定义（列表式节点编辑，一期不做拖拽画布）。

    form_schema 描述发起申请时的动态表单：
    ``[{"key": "amount", "label": "金额", "type": "number", "required": true, "options": []}]``，
    type 支持 text/textarea/number/date/select；条件表达式与「表单字段审批人」
    均按 key 在 form_data 上取值。
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(_("Flow name"), max_length=64)
    code = models.CharField(_("Flow code"), max_length=64, unique=True)
    form_schema = models.JSONField(_("Form schema"), default=list, blank=True)
    is_active = models.BooleanField(_("Is active"), default=True, db_index=True)
    # 当前定义版本号：每次节点/表单定义变化 +1，并在 ApprovalFlowVersion 落全量快照
    # （回滚 = 把历史快照写入活定义并落新版本，见 ADR-016 §2）
    version = models.IntegerField(_("Definition version"), default=0)

    class Meta:
        ordering = ["-created_time"]
        verbose_name = _("Approval flow")
        verbose_name_plural = verbose_name

    def __str__(self):
        return f"{self.name}({self.code})"


class ApprovalFlowNode(DbAuditModel):
    """流程节点：顺序由 order 决定，condition 命中才经过该节点（空 = 无条件）。

    - approve_type：OR（或签，任一通过即节点通过）/ AND（会签，全部通过才通过）；
    - assignee_type：role（角色 code）/ user（用户名，逗号分隔）/ leader（申请人
      所在部门 leader）/ field（表单字段 key，值为用户名或用户名列表）；
    - timeout_hours：>0 时超时未处理由 beat 任务提醒当前节点审批人（每任务每日一次）。
    """

    class ApproveType(models.TextChoices):
        OR = "OR", _("Any approver")
        AND = "AND", _("All approvers")
        # 比例会签：通过人数 / 候选总数 ≥ approve_ratio% 即节点通过（ratio=100 退化为 AND）
        RATIO = "RATIO", _("Ratio approvers")

    class AssigneeType(models.TextChoices):
        ROLE = "role", _("Role")
        USER = "user", _("User")
        LEADER = "leader", _("Leader")
        FIELD = "field", _("Form field")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    flow = models.ForeignKey(
        "system.ApprovalFlow", related_name="nodes", on_delete=models.CASCADE, verbose_name=_("Flow")
    )
    name = models.CharField(_("Node name"), max_length=64)
    order = models.IntegerField(_("Node order"), default=1)
    approve_type = models.CharField(
        _("Approve type"), max_length=5, choices=ApproveType.choices, default=ApproveType.OR
    )
    # RATIO 策略的通过比例（%）：1-100；非 RATIO 类型忽略
    approve_ratio = models.SmallIntegerField(_("Approve ratio"), default=100)
    assignee_type = models.CharField(
        _("Assignee type"), max_length=8, choices=AssigneeType.choices, default=AssigneeType.ROLE
    )
    assignee_value = models.CharField(_("Assignee value"), max_length=255, blank=True, default="")
    # 条件表达式：{"field": "amount", "op": "gte", "value": 1000}；空 dict = 无条件
    condition = models.JSONField(_("Condition"), default=dict, blank=True)
    # 出口路由表（排他网关，ADR-016 §1）：逐条求值首个命中即跳转 target（同流程节点
    # order）；全部未命中回退线性语义（order 之后首个条件命中节点）。空 = 纯线性。
    # 形态：[{"condition": {...}, "target": 3}]
    routes = models.JSONField(_("Branch routes"), default=list, blank=True)
    # 画布坐标（@vue-flow 节点定位）：{"x": 100, "y": 200}；仅前端布局用
    layout = models.JSONField(_("Canvas layout"), default=dict, blank=True)
    timeout_hours = models.IntegerField(_("Timeout hours"), default=0)

    class Meta:
        ordering = ["order", "created_time"]
        verbose_name = _("Approval flow node")
        verbose_name_plural = verbose_name
        constraints = [
            models.UniqueConstraint(fields=["flow", "order"], name="uniq_approval_flow_node_order"),
        ]

    def __str__(self):
        return f"{self.flow_id}#{self.order} {self.name}"


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

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    flow = models.ForeignKey(
        "system.ApprovalFlow", related_name="instances", on_delete=models.PROTECT, verbose_name=_("Flow")
    )
    # 快照：流程改名/改节点不影响历史实例展示
    flow_name = models.CharField(_("Flow name"), max_length=64)
    title = models.CharField(_("Title"), max_length=128)
    form_data = models.JSONField(_("Form data"), default=dict, blank=True)
    status = models.CharField(_("Status"), max_length=16, choices=Status.choices, default=Status.PENDING, db_index=True)
    # 发起时的流程定义版本号（纯追溯字段：推进仍读活定义，见 ADR-016 §2 边界）
    flow_version = models.IntegerField(_("Flow version"), null=True, blank=True)
    current_node = models.ForeignKey(
        "system.ApprovalFlowNode",
        related_name="+",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name=_("Current node"),
    )
    finished_at = models.DateTimeField(_("Finished at"), null=True, blank=True)
    reason = models.CharField(_("Reason"), max_length=255, blank=True, null=True)

    class Meta:
        ordering = ["-created_time"]
        verbose_name = _("Approval instance")
        verbose_name_plural = verbose_name
        indexes = [
            models.Index(fields=["status", "created_time"], name="idx_appr_inst_status_created"),
            models.Index(fields=["creator", "created_time"], name="idx_appr_inst_creator_created"),
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
        "system.ApprovalInstance", related_name="tasks", on_delete=models.CASCADE, verbose_name=_("Instance")
    )
    node = models.ForeignKey(
        "system.ApprovalFlowNode",
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
        "system.UserInfo",
        related_name="approval_node_tasks",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name=_("Assignee"),
    )
    actor = models.ForeignKey(
        "system.UserInfo",
        related_name="approval_node_acted_tasks",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name=_("Actor"),
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


class ApprovalFlowVersion(DbAuditModel):
    """流程定义版本快照（ADR-016 §2）：每次节点/表单定义变化落一条全量快照。

    用途 = 变更审计追溯 + 一键回滚（回滚把历史快照写回活定义并落新版本）；
    不做「在途实例绑版本」（推进仍读活定义，靠 PENDING 锁维持一致性）。
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    flow = models.ForeignKey(
        "system.ApprovalFlow",
        related_name="versions",
        on_delete=models.CASCADE,
        verbose_name=_("Flow"),
    )
    version = models.IntegerField(_("Version"))
    # 全量快照：{"name", "code", "is_active", "form_schema": [...], "nodes": [...]}
    snapshot = models.JSONField(_("Snapshot"), default=dict)
    remark = models.CharField(_("Remark"), max_length=128, blank=True, default="")

    class Meta:
        ordering = ["-version"]
        verbose_name = _("Approval flow version")
        verbose_name_plural = verbose_name
        constraints = [
            models.UniqueConstraint(fields=["flow", "version"], name="uniq_approval_flow_version"),
        ]

    def __str__(self):
        return f"{self.flow_id} v{self.version}"
