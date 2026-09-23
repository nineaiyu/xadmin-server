#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""安全域模型：

- ``AccountRisk``：账号安全风险项（巡检发现 → 处置 → 留痕）；
- ``LoginAccessPolicy``：登录访问策略（时段 / 网段 × 对象）；
- ``FileAccessLog``：文件访问审计（上传 / 下载 / 预览 / 删除留痕）；
- ``UserPasskey``：WebAuthn / Passkey 凭据。
"""

import uuid

from django.db import models
from django.utils.translation import gettext_lazy as _

from common.core.models import DbAuditModel


class AccountRisk(DbAuditModel):
    """账号安全风险项：一次巡检为「用户 × 风险类型」维护一行（幂等更新）。

    风险解除时由巡检自动置 ``RESOLVED``（remark 注明自动解除），人工处置置
    ``RESOLVED`` / ``IGNORED``（豁免）并记录处置人与时间——形成「发现 → 处置 → 留痕」闭环。
    全局型风险（如超管数量异常）``user`` 为空，每次巡检重建。
    """

    class RiskType(models.TextChoices):
        PASSWORD_EXPIRED = "password_expired", _("Password expired")
        PASSWORD_STALE = "password_stale", _("Password not changed for a long time")
        LOGIN_STALE = "login_stale", _("No login for a long time")
        NEVER_LOGGED_IN = "never_logged_in", _("Never logged in")
        SUPERUSER_NO_MFA = "superuser_no_mfa", _("Admin without MFA")
        SUPERUSER_COUNT = "superuser_count", _("Abnormal superuser count")

    class Level(models.TextChoices):
        HIGH = "high", _("High")
        MEDIUM = "medium", _("Medium")
        LOW = "low", _("Low")

    class Status(models.TextChoices):
        PENDING = "pending", _("Pending")
        RESOLVED = "resolved", _("Resolved")
        IGNORED = "ignored", _("Ignored")

    # 风险等级 / 处置状态的可视化色值（前端 tag 渲染）
    RISK_LEVEL_COLORS = {"high": "#f56c6c", "medium": "#e6a23c", "low": "#909399"}
    STATUS_COLORS = {"pending": "#e6a23c", "resolved": "#67c23a", "ignored": "#909399"}

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        "system.UserInfo",
        related_name="account_risks",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name=_("User"),
    )
    # 用户显示名快照：用户删除后风险项仍可读（与审批处理人快照同口径）
    user_display = models.CharField(_("User display"), max_length=128, blank=True, default="")
    risk_type = models.CharField(_("Risk type"), max_length=32, choices=RiskType.choices, db_index=True)
    level = models.CharField(_("Level"), max_length=8, choices=Level.choices, default=Level.MEDIUM)
    status = models.CharField(_("Status"), max_length=16, choices=Status.choices, default=Status.PENDING, db_index=True)
    # 风险明细：{"description": ..., "suggestion": ..., "metrics": {...}}
    detail = models.JSONField(_("Detail"), default=dict, blank=True)
    handled_by = models.ForeignKey(
        "system.UserInfo",
        related_name="handled_account_risks",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name=_("Handled by"),
    )
    handled_at = models.DateTimeField(_("Handled at"), null=True, blank=True)
    remark = models.CharField(_("Remark"), max_length=255, blank=True, default="")

    class Meta:
        ordering = ["-created_time"]
        verbose_name = _("Account risk")
        verbose_name_plural = verbose_name
        constraints = [
            models.UniqueConstraint(fields=["user", "risk_type"], name="uniq_account_risk_user_type"),
        ]
        indexes = [
            models.Index(fields=["status", "level"], name="idx_account_risk_status_level"),
            models.Index(fields=["risk_type", "created_time"], name="idx_account_risk_type_created"),
        ]

    def __str__(self):
        return f"{self.user_display or self.user_id} {self.risk_type} [{self.status}]"


class LoginAccessPolicy(DbAuditModel):
    """登录访问策略：按「对象 × 时段 × 来源网段」匹配登录，首个命中策略决定动作。

    action 语义：
    - ``accept``：命中即放行（豁免后续 reject 判定，用于「仅工作时间可登录」白名单窗口）；
    - ``reject``：命中即拒绝登录（文案说明命中的策略名）；
    - ``require_mfa``：命中即要求二次验证（走既有 MFA 链路）；
    - ``record``：只记录（命中写入登录日志，不改变行为）。

    条件为空 = 不限制该维度；``ip_ranges`` 每行一个 CIDR（``192.168.0.0/24``）或区间（``10.0.0.1-10.0.0.50``）。
    """

    class Action(models.TextChoices):
        ACCEPT = "accept", _("Accept")
        REJECT = "reject", _("Reject")
        REQUIRE_MFA = "require_mfa", _("Require MFA")
        RECORD = "record", _("Record only")

    class TargetType(models.TextChoices):
        ALL = "all", _("All users")
        USER = "user", _("Specified users")
        ROLE = "role", _("Specified roles")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(_("Policy name"), max_length=64)
    # 优先级：数值小者优先（首个命中生效）；同优先级按创建时间
    priority = models.IntegerField(_("Priority"), default=100, db_index=True)
    is_active = models.BooleanField(_("Is active"), default=True, db_index=True)
    target_type = models.CharField(_("Target type"), max_length=8, choices=TargetType.choices, default=TargetType.ALL)
    # 用户名（逗号分隔）或角色 code（逗号分隔）；target_type=all 时忽略
    target_value = models.CharField(_("Target value"), max_length=512, blank=True, default="")
    # 生效星期：1=周一 ... 7=周日（空 = 每天）
    weekdays = models.JSONField(_("Weekdays"), default=list, blank=True)
    # 生效时段（空 = 全天；start > end 表示跨天，如 22:00-06:00）
    start_time = models.TimeField(_("Start time"), null=True, blank=True)
    end_time = models.TimeField(_("End time"), null=True, blank=True)
    # 来源网段：每行一个 CIDR 或区间（空 = 不限）
    ip_ranges = models.TextField(_("IP ranges"), blank=True, default="")
    action = models.CharField(_("Action"), max_length=16, choices=Action.choices, default=Action.REJECT)
    remark = models.CharField(_("Remark"), max_length=255, blank=True, default="")

    class Meta:
        ordering = ["priority", "created_time"]
        verbose_name = _("Login access policy")
        verbose_name_plural = verbose_name

    def __str__(self):
        return f"{self.name} [{self.action}]"


class FileAccessLog(models.Model):
    """文件访问审计：上传 / 下载 / 预览 / 删除四类动作的元数据留痕。

    高频写表：只记元数据（不含文件内容），保留期随审计口径统一清理；
    文件与用户删除后靠名称快照保留可读性（不级联删除日志）。
    """

    class Action(models.TextChoices):
        UPLOAD = "upload", _("Upload")
        DOWNLOAD = "download", _("Download")
        PREVIEW = "preview", _("Preview")
        DELETE = "delete", _("Delete")

    id = models.BigAutoField(primary_key=True)
    file = models.ForeignKey(
        "system.UploadFile",
        related_name="access_logs",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name=_("File"),
    )
    filename = models.CharField(_("Filename"), max_length=255, blank=True, default="")
    user = models.ForeignKey(
        "system.UserInfo",
        related_name="file_access_logs",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name=_("User"),
    )
    user_display = models.CharField(_("User display"), max_length=128, blank=True, default="")
    action = models.CharField(_("Action"), max_length=16, choices=Action.choices, db_index=True)
    ipaddress = models.CharField(_("IP address"), max_length=64, blank=True, default="")
    result = models.BooleanField(_("Result"), default=True)
    detail = models.CharField(_("Detail"), max_length=255, blank=True, default="")
    created_time = models.DateTimeField(_("Created time"), auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_time"]
        verbose_name = _("File access log")
        verbose_name_plural = verbose_name
        indexes = [
            models.Index(fields=["file", "created_time"], name="idx_file_log_file_created"),
            models.Index(fields=["user", "action"], name="idx_file_log_user_action"),
        ]

    def __str__(self):
        return f"{self.filename} {self.action} by {self.user_display}"


class UserPasskey(DbAuditModel):
    """WebAuthn / Passkey 凭据：一个用户可绑定多个凭据（多设备）。

    ``public_key`` 存 COSE 公钥原文（CBOR），认领时解析为 cryptography 公钥对象验签；
    ``sign_count`` 用于单调性校验（防重放，导入的凭据可能恒为 0 → 不做失败判定）。
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        "system.UserInfo",
        related_name="passkeys",
        on_delete=models.CASCADE,
        verbose_name=_("User"),
    )
    credential_id = models.CharField(_("Credential id"), max_length=512, unique=True)
    public_key = models.BinaryField(_("Public key"))
    sign_count = models.IntegerField(_("Sign count"), default=0)
    name = models.CharField(_("Device name"), max_length=64, blank=True, default="")
    aaguid = models.CharField(_("AAGUID"), max_length=64, blank=True, default="")
    backed_up = models.BooleanField(_("Backed up"), default=False)
    last_used_at = models.DateTimeField(_("Last used at"), null=True, blank=True)

    class Meta:
        ordering = ["-created_time"]
        verbose_name = _("User passkey")
        verbose_name_plural = verbose_name

    def __str__(self):
        return f"{self.user_id} {self.name or self.credential_id[:12]}"
