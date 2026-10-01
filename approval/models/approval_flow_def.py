#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""审批流程定义模型（自 approval.py 拆分，表名 / 行为不变）。

包含：ApprovalFlow（流程定义）/ ApprovalFlowNode（节点，含版本有效区间）/
ApprovalFlowVersion（定义版本快照）与节点查询集 / 管理器。
"""

import uuid

from django.db import models
from django.utils.translation import gettext_lazy as _

from common.core.models import DbAuditModel


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
    # （回滚 = 把历史快照写入活定义并落新版本）
    version = models.IntegerField(_("Definition version"), default=0)

    class Meta:
        db_table = "system_approvalflow"  # 3.1 拆分批次2：迁 approval app，表名不变
        ordering = ["-created_time"]
        verbose_name = _("Approval flow")
        verbose_name_plural = verbose_name

    def __str__(self):
        return f"{self.name}({self.code})"


class ApprovalFlowNodeQuerySet(models.QuerySet):
    """节点查询集：按版本有效区间取「定义事实」。

    有效区间语义：``version_from <= V AND (version_to IS NULL OR version_to > V)``。
    历史行（``version_to`` 已落值）只对钉住旧版本的实例可见——改版不再物理删除节点，
    在途单按自身 flow_version 推进（见 docs/adr/ADR-073-in-flight-flow-versioning.md）。
    """

    def effective_at(self, version=None):
        """在指定版本生效的节点；``version=None`` → 当前生效定义（version_to 为空）。"""
        if version is None:
            return self.filter(version_to__isnull=True)
        return self.filter(version_from__lte=version).filter(
            models.Q(version_to__isnull=True) | models.Q(version_to__gt=version)
        )


class ApprovalFlowNodeManager(models.Manager):
    """默认管理器：只暴露当前生效节点（历史行走 ``all_objects``）。

    `flow.nodes.all()` / `prefetch_related("nodes")` / 序列化读取因此天然只见当前
    定义（管理面不显示历史行）；版本化推进与历史查询显式走 ``all_objects``。
    """

    def get_queryset(self) -> ApprovalFlowNodeQuerySet:
        return ApprovalFlowNodeQuerySet(self.model, using=self._db).filter(version_to__isnull=True)


class ApprovalFlowNode(DbAuditModel):
    """流程节点：顺序由 order 决定，condition 命中才经过该节点（空 = 无条件）。

    - approve_type：OR（或签，任一通过即节点通过）/ AND（会签，全部通过才通过）；
    - assignee_type：role（角色 code）/ user（用户名，逗号分隔）/ leader（申请人
      所在部门 leader）/ field（表单字段 key，值为用户名或用户名列表）/ post
      （岗位 code，逗号分隔；按 UserInfo.posts 解析，不参与权限判定）；
    - timeout_hours：>0 时超时未处理由 beat 任务提醒当前节点审批人（每任务每日一次）；
    - timeout_action：超时自动动作（提醒之外的分支执行，见 periodic.execute_timeout_actions）；
    - version_from / version_to：定义有效区间（改版 = 旧行收口 + 新版本落新行，不删行）。
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
        POST = "post", _("Post")

    class TimeoutAction(models.TextChoices):
        """超时自动动作（timeout_hours 到点后由 beat 按分支执行；none = 仅提醒）。"""

        NONE = "none", _("No action")
        # 系统自动通过该任务（会签语义下其余候选人仍需处理）
        APPROVE = "approve", _("Auto approve")
        # 系统自动驳回整单（终态 REJECTED，原因注明超时自动驳回）
        REJECT = "reject", _("Auto reject")
        # 升级转交：任务转给处理人所在部门的 leader（无 leader 时保持待办继续提醒）
        TRANSFER_UP = "transfer_up", _("Escalate to leader")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    flow = models.ForeignKey(
        "approval.ApprovalFlow", related_name="nodes", on_delete=models.CASCADE, verbose_name=_("Flow")
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
    # 出口路由表（排他网关）：逐条求值首个命中即跳转 target（同流程节点
    # order）；全部未命中回退线性语义（order 之后首个条件命中节点）。空 = 纯线性。
    # 形态：[{"condition": {...}, "target": 3}]
    routes = models.JSONField(_("Branch routes"), default=list, blank=True)
    # 画布坐标（@vue-flow 节点定位）：{"x": 100, "y": 200}；仅前端布局用
    layout = models.JSONField(_("Canvas layout"), default=dict, blank=True)
    timeout_hours = models.IntegerField(_("Timeout hours"), default=0)
    # 超时自动动作：none 仅提醒；approve/reject 到点由系统代为处理；transfer_up 升级给 leader
    timeout_action = models.CharField(
        _("Timeout action"), max_length=16, choices=TimeoutAction.choices, default=TimeoutAction.NONE
    )
    # 抄送人：节点级默认抄送（用户 pk 列表）；实例发起时解析为实例级 cc_users 快照
    cc_users = models.JSONField(_("CC users"), default=list, blank=True)
    # 定义有效区间：version_from 起生效（含）、version_to 止失效（不含，NULL = 仍生效）
    version_from = models.IntegerField(_("Effective from version"), default=1)
    version_to = models.IntegerField(_("Effective until version"), null=True, blank=True, default=None)

    # 默认管理器只返回当前生效行；版本历史/按版本推进显式走 all_objects（见类定义）
    objects = ApprovalFlowNodeManager()
    all_objects = ApprovalFlowNodeQuerySet.as_manager()

    class Meta:
        db_table = "system_approvalflownode"  # 3.1 拆分批次2：迁 approval app，表名不变
        ordering = ["order", "created_time"]
        verbose_name = _("Approval flow node")
        verbose_name_plural = verbose_name
        constraints = [
            # 条件唯一：当前生效行之间 (flow, order) 唯一；历史行与当前行可同 order 共存
            models.UniqueConstraint(
                fields=["flow", "order"],
                condition=models.Q(version_to__isnull=True),
                name="uniq_flow_node_order_active",
            ),
        ]

    def __str__(self):
        return f"{self.flow_id}#{self.order} {self.name}"


class ApprovalFlowVersion(DbAuditModel):
    """流程定义版本快照：每次节点/表单定义变化落一条全量快照。

    用途 = 变更审计追溯 + 一键回滚（回滚把历史快照写回活定义并落新版本）。
    在途实例按自身 ``flow_version`` 推进（节点有效区间，见 ApprovalFlowNodeQuerySet）——
    改版不再需要 PENDING 锁，历史快照与实例钉住的版本行互为佐证。
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    flow = models.ForeignKey(
        "approval.ApprovalFlow",
        related_name="versions",
        on_delete=models.CASCADE,
        verbose_name=_("Flow"),
    )
    version = models.IntegerField(_("Version"))
    # 全量快照：{"name", "code", "is_active", "form_schema": [...], "nodes": [...]}
    snapshot = models.JSONField(_("Snapshot"), default=dict)
    remark = models.CharField(_("Remark"), max_length=128, blank=True, default="")

    class Meta:
        db_table = "system_approvalflowversion"  # 3.1 拆分批次2：迁 approval app，表名不变
        ordering = ["-version"]
        verbose_name = _("Approval flow version")
        verbose_name_plural = verbose_name
        constraints = [
            models.UniqueConstraint(fields=["flow", "version"], name="uniq_approval_flow_version"),
        ]

    def __str__(self):
        return f"{self.flow_id} v{self.version}"
