#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""MFA 与资源监控阈值序列化器（自 settings/serializers/security.py 平移）。

对外的既有导入面（settings.serializers.security）经该模块再导出保持不变。
"""

from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from settings.serializers.contract import SettingSaveContractMixin


class SecurityMFASerializer(SettingSaveContractMixin, serializers.Serializer):
    """MFA / 敏感操作二次验证设置"""

    SECURITY_MFA_CONFIRM_ENABLED = serializers.BooleanField(
        required=False,
        default=True,
        label=_("Sensitive operation verification"),
        help_text=_("Require re-verification of identity before performing sensitive operations"),
    )

    SECURITY_MFA_CONFIRM_BACKENDS = serializers.ListField(
        default=["otp", "sms", "email", "password", "passkey"],
        label=_("Verification methods"),
        allow_empty=True,
        child=serializers.ChoiceField(
            choices=[
                ("otp", _("OTP verification code")),
                ("sms", _("SMS verification code")),
                ("email", _("Email verification code")),
                ("password", _("Login password")),
                ("passkey", _("Passkey")),
            ]
        ),
        help_text=_("Verification methods allowed to be used for sensitive operation verification"),
    )

    SECURITY_MFA_VERIFY_TTL = serializers.IntegerField(
        min_value=60,
        max_value=60 * 60 * 24,
        default=60 * 60,
        label=_("MFA confirm validity period (second)"),
        help_text=_(
            "After passing the verification via OTP/SMS/Email, sensitive operations "
            "do not need to be verified again within the validity period"
        ),
    )

    SECURITY_MFA_PASSWORD_CONFIRM_TTL = serializers.IntegerField(
        min_value=60,
        max_value=60 * 60 * 24,
        default=300,
        label=_("Password confirm validity period (second)"),
        help_text=_("After passing the verification via password"),
    )

    SECURITY_MFA_LOGIN_PROTECT_ENABLED = serializers.BooleanField(
        required=False,
        default=True,
        label=_("Login MFA"),
        help_text=_(
            "Accounts with personal MFA enabled always require verification at login. "
            "When enabled, accounts bound to OTP are forced to verify even if they "
            "closed it themselves"
        ),
    )

    SECURITY_MFA_LOGIN_TOKEN_TTL = serializers.IntegerField(
        min_value=60,
        max_value=60 * 60,
        default=300,
        label=_("Login MFA token validity period (second)"),
        help_text=_("Validity period of the temporary token during login MFA verification"),
    )

    SECURITY_MFA_OTP_VALID_WINDOW = serializers.IntegerField(
        min_value=0,
        max_value=10,
        default=1,
        label=_("OTP valid window"),
        help_text=_("The number of time periods allowed before and after the OTP verification"),
    )

    SECURITY_MFA_OTP_ISSUER = serializers.CharField(
        max_length=64,
        default="XAdmin",
        label=_("OTP issuer"),
        help_text=_("The issuer name in the otpauth binding URI"),
    )


class SecurityMonitorSerializer(SettingSaveContractMixin, serializers.Serializer):
    """资源告警阈值设置（check_server_performance_period 周期检查使用）"""

    SECURITY_MONITOR_DISK_USED_MAX = serializers.IntegerField(
        min_value=1,
        max_value=100,
        default=80,
        label=_("Disk usage threshold (%)"),
        help_text=_("Send an alert email to the administrator when the disk usage exceeds this threshold"),
    )

    SECURITY_MONITOR_MEMORY_USED_MAX = serializers.IntegerField(
        min_value=1,
        max_value=100,
        default=85,
        label=_("Memory usage threshold (%)"),
        help_text=_("Send an alert email to the administrator when the memory usage exceeds this threshold"),
    )

    SECURITY_MONITOR_CPU_PERCENT_MAX = serializers.IntegerField(
        min_value=1,
        max_value=100,
        default=80,
        label=_("CPU usage threshold (%)"),
        help_text=_("Send an alert email to the administrator when the CPU usage exceeds this threshold"),
    )

    SECURITY_MONITOR_CPU_LOAD_MAX = serializers.IntegerField(
        min_value=1,
        max_value=100,
        default=5,
        label=_("CPU load threshold (single core)"),
        help_text=_("Send an alert email to the administrator when the single-core CPU load exceeds this threshold"),
    )
