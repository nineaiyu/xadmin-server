#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""密码历史：改密/重置/建号时留存密码哈希（不存明文）。

配合 ``SECURITY_PASSWORD_HISTORY_COUNT`` 实现「最近 N 次不可复用」：
校验见 ``settings.utils.password.check_history_password``，留存见
``record_password_hash``（同一模块）。量级 = 用户数 × 改密频次，无需清理任务。
"""

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from common.core.models import DbAuditModel


class PasswordHistory(DbAuditModel):
    """用户密码历史（仅哈希）"""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("User"),
        on_delete=models.CASCADE,
        related_name="password_histories",
    )
    # Django 密码哈希（与 UserInfo.password 同构，可直接 check_password 比对）
    password = models.CharField(_("Password hash"), max_length=128)

    class Meta:
        ordering = ("-created_time",)
        verbose_name = _("Password history")
        verbose_name_plural = verbose_name

    def __str__(self):
        return f"{self.user}({self.created_time})"
