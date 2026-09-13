#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""LDAP 目录绑定（ADR-017）：本地账号与目录条目的关联锚点。"""

from django.db import models
from django.utils.translation import gettext_lazy as _

from common.core.models import DbAuditModel


class LdapUserBinding(DbAuditModel):
    """用户与 LDAP 条目的绑定：dn 是目录侧唯一身份（改名不丢绑定）。

    只存绑定不存目录凭证；存在绑定即视为「密码由目录管理」，本地改密/重置入口
    会被序列化器拒绝。
    """

    user = models.OneToOneField(
        "system.UserInfo",
        on_delete=models.CASCADE,
        related_name="ldap_binding",
        verbose_name=_("User"),
    )
    dn = models.CharField(_("Distinguished name"), max_length=512, unique=True, db_index=True)
    synced_at = models.DateTimeField(_("Last synced at"), null=True, blank=True)

    class Meta:
        verbose_name = _("LDAP binding")
        verbose_name_plural = _("LDAP bindings")
        ordering = ["-created_time"]

    def __str__(self):
        return f"{self.user}(dn={self.dn})"
