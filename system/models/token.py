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
    # scope 语义 = 允许的接口路径前缀/正则清单（一期边界收口）：
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
    # 所属开放平台应用：非空 = 由应用换发（应用停用/过期即失效 + 按应用限流）
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
    """开放平台应用（client-credentials）。

    应用本身不携带权限：换发出的凭证以 owner（creator）身份走既有 PAT 认证链，
    三层权限 / 数据权限 / 审计（``OperationLog.auth_type=pat``）天然生效；
    应用只负责凭证换发、范围（scopes / ip_allowlist）、按应用限流与回调登记。

    二期增量：``grants``（模型×动作×字段×行四级授权，只收敛不提权）、
    ``daily_quota``（每日配额软告警）、OAuth 授权码（refresh 见 OAuthRefreshToken）。
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
    # 每日请求配额（0 = 不限）；达 quota_alert_percent 百分比当日首次越线发告警（软，不阻断）
    daily_quota = models.IntegerField(_("Daily quota"), default=0)
    quota_alert_percent = models.IntegerField(_("Quota alert percent"), default=80)
    is_active = models.BooleanField(_("Is active"), default=True)
    expired_at = models.DateTimeField(_("Expired at"), null=True, blank=True)

    class Meta:
        ordering = ("-created_time",)
        verbose_name = _("API application")
        verbose_name_plural = verbose_name
        indexes = [models.Index(fields=["client_id"], name="idx_api_app_client_id")]

    def __str__(self):
        return f"{self.name}({self.client_id})"


class ApiApplicationGrant(DbAuditModel):
    """应用资源授权规则（开放平台二期：模型 × 动作 × 字段 × 行四级授权）。

    生效语义（只收敛不提权，fail-closed）：
    - 应用不存在任何 is_active 规则 → 兼容模式（维持一期 owner 权限 + scopes，零影响）；
    - 存在 ≥1 条规则 → 白名单模式：请求目标模型必须被某条规则覆盖（model 精确或 ``*``），
      动作段必须在覆盖规则的 actions 内（或 ``*``）；fields 非空时收敛字段
      （输出裁剪 + 输入拒绝未授权字段）；row_filter 非空时编译为 Q 叠加在数据权限
      过滤之后（AND）。
    - 四级之上仍走原有菜单/字段/数据权限（全部取交集），永不放大回 owner 全量。
    """

    application = models.ForeignKey(
        "system.ApiApplication",
        verbose_name=_("API application"),
        on_delete=models.CASCADE,
        related_name="grants",
    )
    # 目标模型标签（system.dataset）或 *（全部模型）
    model = models.CharField(_("Model"), max_length=128)
    # 权限点动作段清单（list/retrieve/create/...）或 ["*"]（全部动作）
    actions = models.JSONField(_("Actions"), default=list)
    # 允许的字段名清单（空 = 全部字段）
    fields = models.JSONField(_("Fields"), default=list, blank=True)
    # 行级规则（DataPermission.rules 同格式；空 = 不限）
    row_filter = models.JSONField(_("Row filter"), default=list, blank=True)
    is_active = models.BooleanField(_("Is active"), default=True)

    class Meta:
        ordering = ("model", "created_time")
        verbose_name = _("API application grant")
        verbose_name_plural = verbose_name
        indexes = [models.Index(fields=["application", "is_active"], name="idx_api_grant_app_active")]

    def __str__(self):
        return f"{self.application_id}:{self.model}"


class OAuthRefreshToken(DbAuditModel):
    """OAuth 授权码模式的刷新令牌。

    与 PAT 同口径只存 sha256 哈希；一次性轮换（刷新即失效旧值），撤销可联动
    失效关联 access 凭证。权限面 = 应用 scope × 应用 grant × 授权用户权限（交集）。
    """

    token_hash = models.CharField(_("Token hash"), max_length=64, unique=True, db_index=False)
    application = models.ForeignKey(
        "system.ApiApplication",
        verbose_name=_("API application"),
        on_delete=models.CASCADE,
        related_name="refresh_tokens",
    )
    user = models.ForeignKey(
        "system.UserInfo",
        verbose_name=_("Authorized user"),
        on_delete=models.CASCADE,
        related_name="oauth_refresh_tokens",
    )
    scopes = models.JSONField(_("Scopes"), default=list, blank=True)
    expired_at = models.DateTimeField(_("Expired at"), null=True, blank=True)
    is_revoked = models.BooleanField(_("Is revoked"), default=False)
    # 本次 refresh 关联的 access 凭证（撤销时联动失效；SET_NULL 不阻断凭证回溯）
    access_token = models.ForeignKey(
        "system.PersonalAccessToken",
        verbose_name=_("Access token"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )

    class Meta:
        ordering = ("-created_time",)
        verbose_name = _("OAuth refresh token")
        verbose_name_plural = verbose_name
        indexes = [models.Index(fields=["application", "user"], name="idx_oauth_refresh_app_user")]

    def __str__(self):
        return f"oauth:{self.application_id}:{self.user_id}"
