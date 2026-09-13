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
    # 所属开放平台应用（ADR-030）：非空 = 由应用换发（应用停用/过期即失效 + 按应用限流）
    api_application = models.ForeignKey(
        "system.ApiApplication",
        verbose_name=_("API application"),
        on_delete=models.CASCADE,
        related_name="tokens",
        null=True,
        blank=True,
    )

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


class ApiApplication(DbAuditModel):
    """开放平台应用（client-credentials，ADR-030）。

    应用本身不携带权限：换发出的凭证以 owner（creator）身份走既有 PAT 认证链，
    三层权限 / 数据权限 / 审计（``OperationLog.auth_type=pat``）天然生效；
    应用只负责凭证换发、范围（scopes / ip_allowlist）、按应用限流与回调登记。
    """

    name = models.CharField(_("Application name"), max_length=128)
    client_id = models.CharField(_("Client id"), max_length=64, unique=True)
    # 与 PAT 同口径：sha256 哈希存储，明文只在创建/重置响应中返回一次
    client_secret_hash = models.CharField(_("Client secret hash"), max_length=64)
    client_secret_prefix = models.CharField(_("Client secret prefix"), max_length=16)
    # 回调签名密钥（密文存储，复用 webhook 的 encrypt_secret/decrypt_secret 口径）
    callback_secret_encrypted = models.CharField(_("Callback secret"), max_length=256, blank=True, default="")
    scopes = models.JSONField(_("Scopes"), default=list, blank=True)
    ip_allowlist = models.JSONField(_("Ip allowlist"), default=list, blank=True)
    # 0 = 不限；>0 = 每分钟允许的已认证请求数（超限 429，按应用维度计数）
    rate_limit_per_minute = models.IntegerField(_("Rate limit per minute"), default=0)
    callback_urls = models.JSONField(_("Callback urls"), default=list, blank=True)
    # 换发凭证的有效期（秒）；0 = 不过期（随应用 expired_at）
    token_ttl_seconds = models.IntegerField(_("Token ttl seconds"), default=7200)
    is_active = models.BooleanField(_("Is active"), default=True)
    expired_at = models.DateTimeField(_("Expired at"), null=True, blank=True)

    class Meta:
        ordering = ("-created_time",)
        verbose_name = _("API application")
        verbose_name_plural = verbose_name
        indexes = [models.Index(fields=["client_id"], name="idx_api_app_client_id")]

    def __str__(self):
        return f"{self.name}({self.client_id})"
