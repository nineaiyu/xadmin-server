#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""敏感操作审批单（轻量审批流：一次性通行令牌）。

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
