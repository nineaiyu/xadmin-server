#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""第三方账号绑定（OAuth2 / OIDC 通用 provider）。"""

from django.db import models
from django.utils.translation import gettext_lazy as _

from common.core.models import DbAuditModel


class UserOAuthBinding(DbAuditModel):
    """本地账号与 IdP 身份的绑定关系。

    只存「绑定」不存凭证：token 换取在回调链路内一次性完成，不落库（避免长期持有
    IdP 的 access/refresh token，减少泄露面）。`profile` 只是用于展示的只读快照。
    """

    user = models.ForeignKey(
        "system.UserInfo",
        on_delete=models.CASCADE,
        related_name="oauth_bindings",
        verbose_name=_("User"),
    )
    provider = models.CharField(_("Provider"), max_length=64, db_index=True)
    subject = models.CharField(_("Subject"), max_length=255, help_text=_("IdP 侧唯一标识（sub）"))
    profile = models.JSONField(
        _("Profile"), default=dict, blank=True, help_text=_("IdP 返回的头像/昵称/邮箱等只读快照")
    )

    class Meta:
        verbose_name = _("OAuth binding")
        verbose_name_plural = _("OAuth bindings")
        ordering = ["-created_time"]
        constraints = [models.UniqueConstraint(fields=["provider", "subject"], name="uniq_oauth_provider_subject")]

    def __str__(self):
        return f"{self.provider}:{self.subject}"

    @property
    def display_name(self) -> str:
        """展示名：优先快照里的昵称/邮箱，退化到 subject。"""
        return str(self.profile.get("nickname") or self.profile.get("email") or self.subject)

    @classmethod
    def user_has_other_login_method(cls, user, exclude_pk=None) -> bool:
        """解绑后是否仍有其它登录方式（防止把账号解成自锁）。

        无密码账号（auto_create 建号）只绑一个第三方时，解绑即永久失联。
        """
        queryset = cls.objects.filter(user=user)
        if exclude_pk is not None:
            queryset = queryset.exclude(pk=exclude_pk)
        return bool(user.has_usable_password()) or queryset.exists()
