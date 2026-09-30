#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : models
"""mfa app 数据模型。

MFA 绑定状态挂在 UserInfo（otp_secret_key / mfa_level），此处仅承载
OTP 恢复码：哈希落库、一次性消费，设备全丢时的自救通道。
"""

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _


class MfaRecoveryCode(models.Model):
    """OTP 恢复码（一次性：明文只在生成响应中出现一次，库内仅存摘要）"""

    user = models.ForeignKey(
        to=settings.AUTH_USER_MODEL,
        verbose_name=_("User"),
        on_delete=models.CASCADE,
        related_name="mfa_recovery_codes",
    )
    code_hash = models.CharField(verbose_name=_("Code hash"), max_length=64)
    created_time = models.DateTimeField(verbose_name=_("Created time"), auto_now_add=True)
    used_time = models.DateTimeField(verbose_name=_("Used time"), null=True, blank=True)

    class Meta:
        verbose_name = _("MFA recovery code")
        verbose_name_plural = verbose_name
        constraints = [models.UniqueConstraint(fields=["user", "code_hash"], name="uniq_mfarecoverycode_user_hash")]
        indexes = [
            # 剩余数量 / 未用码认领查询共用（user + used_time）
            models.Index(fields=["user", "used_time"], name="idx_mfarecovery_user_used"),
        ]
