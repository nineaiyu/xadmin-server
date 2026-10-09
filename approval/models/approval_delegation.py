#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""审批委托模型（自 approval.py 拆分，表名 / 行为不变）。"""

import uuid

from django.db import models
from django.utils.translation import gettext_lazy as _

from common.core.models import DbAuditModel


class ApprovalDelegation(DbAuditModel):
    """审批委托（审批流三期）：委托人在时段/流程范围内将审批权交由代理人代审。

    解析语义见 approval/utils/approval_flow/conditions.py::_expand_delegations ——
    只替换「待办归属」，不改变节点定义；代理人再委托不生效（防环）。
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    delegator = models.ForeignKey(
        "identity.UserInfo", related_name="approval_delegations", on_delete=models.CASCADE, verbose_name=_("Delegator")
    )
    delegate = models.ForeignKey(
        "identity.UserInfo",
        related_name="approval_delegations_as_delegate",
        on_delete=models.CASCADE,
        verbose_name=_("Delegate"),
    )
    start_time = models.DateTimeField(_("Start time"))
    end_time = models.DateTimeField(_("End time"))
    # 空列表 = 全部流程；否则只对命中的 flow.code 生效
    flow_codes = models.JSONField(_("Flow codes"), default=list, blank=True)
    is_active = models.BooleanField(_("Is active"), default=True)
    remark = models.CharField(_("Remark"), max_length=255, blank=True, null=True)

    class Meta:
        ordering = ["-created_time"]
        verbose_name = _("Approval delegation")
        verbose_name_plural = verbose_name
        indexes = [
            models.Index(fields=["delegator", "is_active"], name="idx_appr_deleg_delegator"),
            models.Index(fields=["delegate", "is_active"], name="idx_appr_deleg_delegate"),
        ]

    def __str__(self) -> str:
        return f"{self.delegator_id} -> {self.delegate_id}"
