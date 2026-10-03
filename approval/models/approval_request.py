#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""敏感操作审批单模型（自 approval.py 拆分，表名 / 行为不变）。

ApprovalRequest（轻量：一次性通行令牌）：拦截点（common/core/approval.py 的
ApprovalRequired 装饰器）在业务执行前建 PENDING 单并通知审批人；审批通过后由
原始客户端在有效期内携带 approval_id 重发同一请求，消费令牌后放行。不做服务端
请求重放（multipart/大 body 重放不可靠），含文件的敏感操作不支持审批。

状态机：
- PENDING   待审批（超 PENDING_TIMEOUT 由清理任务置 EXPIRED）
- APPROVED  已通过（expired_at 前可消费一次，消费后 consume_time 落值）
- REJECTED  已驳回（reason 必填）
- CANCELLED 申请人撤回
- EXPIRED   待审批超时 / 令牌过期
- FAILED    消费校验失败（重发请求与快照指纹不一致，留审计痕迹）
"""

import uuid

from django.contrib.postgres.indexes import GinIndex
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
    # 请求体快照（仅 JSON 且小体积时保存）：审批通过后由注册的通过后动作自动执行业务落库，
    # 省去申请人手动重试；multipart/超大 body 存空，仍走客户端携令牌重放协议
    payload = models.JSONField(_("Request payload"), default=dict, blank=True)
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
    # 审批通过后已由注册的通过后动作自动执行业务落库（申请人无需再手动重放）
    auto_completed = models.BooleanField(_("Auto completed"), default=False)
    reason = models.CharField(_("Reason"), max_length=255, blank=True, null=True)
    # 处理人显示名快照：用户被删除/改名后审批痕迹仍可读
    approver_display = models.CharField(_("Approver display"), max_length=128, blank=True, default="")
    # 目标对象轻量快照：{model, verbose_name, pk, name, changes:[{field,label,old,new}]}
    # 仅存「变更相关字段」的事实对照，供审批人看 diff 而非申请文字
    target_snapshot = models.JSONField(_("Target snapshot"), default=dict, blank=True)
    # 多级审批链（ApprovalRule 命中时启用）：current_level = 当前级次（0 = 扁平模式或已结束），
    # current_assignees = 当前级候选人冗余投影（列表展示与待办查询用；权威数据在 steps 快照）
    current_level = models.PositiveSmallIntegerField(_("Current level"), default=0)
    current_assignees = models.ManyToManyField(
        "system.UserInfo",
        related_name="approval_current_assignments",
        blank=True,
        verbose_name=_("Current approvers"),
    )

    class Meta:
        ordering = ["-created_time"]
        verbose_name = _("Approval request")
        verbose_name_plural = verbose_name
        indexes = [
            models.Index(fields=["status", "created_time"], name="idx_approval_status_created"),
            models.Index(fields=["creator", "created_time"], name="idx_approval_creator_created"),
            # 全局搜索的 pg_trgm 索引（path/module/object_pk 检索；PostgreSQL 生效，见 system/search_indexes.py）
            # 索引名不得超过 30 字符（Django 跨库上限，超长会触发 models.E034 阻断启动）
            GinIndex(fields=["path"], name="idx_approvalrequest_path_trgm", opclasses=["gin_trgm_ops"]),
            GinIndex(fields=["module"], name="idx_approval_module_trgm", opclasses=["gin_trgm_ops"]),
            GinIndex(fields=["object_pk"], name="idx_approval_object_pk_trgm", opclasses=["gin_trgm_ops"]),
        ]

    def __str__(self):
        return f"{self.method} {self.path} ({self.status})"
