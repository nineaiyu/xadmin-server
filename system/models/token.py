#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""个人访问令牌（PAT）：机器对机器集成的编程访问凭证。

一次创建对应一条记录：
- 明文仅在创建响应中返回一次，库内只存 sha256 哈希（token_hash unique）；
- token_prefix（前 12 位）用于列表辨识；
- 属主复用 DbAuditModel.creator（pre_save 信号自动赋值），凭证严格个人所有；
- 不建 UserSession、不受 JWT 失效缓存影响：吊销 = 置 is_active=False，查库即时生效。
"""

from django.db import models
from django.utils.translation import gettext_lazy as _

from common.core.models import DbAuditModel


class PersonalAccessToken(DbAuditModel):
    """个人访问令牌（PAT），认证方式：Authorization: Pat <token>"""

    name = models.CharField(_("Token name"), max_length=128)
    token_hash = models.CharField(_("Token hash"), max_length=64, unique=True, db_index=False)
    token_prefix = models.CharField(_("Token prefix"), max_length=16)
    # scope 语义 = 允许的接口路径前缀/正则清单（ADR-008 一期边界收口）：
    # 空清单 = 不限（既有 token 向后兼容）；校验内联在统一权限层
    # （common.core.permission.IsAuthenticated），防「显式 permission_classes 覆写」
    # 与「同请求带 JWT+Pat 双 header」两种绕过路径
    scopes = models.JSONField(_("Scopes"), default=list, blank=True)
    # IP 白名单（凭证治理）：空清单 = 不限；条目为单个 IP 或 CIDR 网段。
    # 认证时按来源 IP（get_request_ip）校验，未命中即拒绝并留审计
    ip_allowlist = models.JSONField(_("Ip allowlist"), default=list, blank=True)
    is_active = models.BooleanField(_("Is active"), default=True)
    expired_at = models.DateTimeField(_("Expired at"), null=True, blank=True)
    last_used_time = models.DateTimeField(_("Last used time"), null=True, blank=True)

    class Meta:
        ordering = ("-created_time",)
        verbose_name = _("Personal access token")
        verbose_name_plural = verbose_name
        indexes = [
            # 个人中心列表（creator + created_time）与过期清理（expired_at）共用
            models.Index(fields=["creator", "created_time"], name="idx_pat_creator_created"),
            models.Index(fields=["expired_at"], name="idx_pat_expired_at"),
        ]

    def __str__(self):
        return f"{self.name}({self.token_prefix})"
