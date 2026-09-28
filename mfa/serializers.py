#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : serializers
from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from mfa.backends import MFA_BACKEND_CLASSES
from mfa.const import ConfirmType

_METHOD_CHOICES = [(cls.name, str(cls.display_name)) for cls in MFA_BACKEND_CLASSES]
_CHALLENGE_CHOICES = [(cls.name, str(cls.display_name)) for cls in MFA_BACKEND_CLASSES if cls.challenge_required]


class ConfirmSerializer(serializers.Serializer):
    """敏感操作二次验证提交"""

    confirm_type = serializers.ChoiceField(
        choices=ConfirmType.choices, default=ConfirmType.MFA, label=_("Confirm type")
    )
    method = serializers.ChoiceField(choices=_METHOD_CHOICES, label=_("Verification method"))
    # 4096：容纳 Passkey 断言 JSON（client_data_json + authenticator_data + signature 的
    # base64url 串），OTP/密码等短验证码同样适用；登录 MFA 链路本就不限长，口径对齐
    code = serializers.CharField(max_length=4096, label=_("Verification code / password"))


class SendCodeSerializer(serializers.Serializer):
    """发送挑战验证码（短信/邮件）"""

    method = serializers.ChoiceField(choices=_CHALLENGE_CHOICES, label=_("Verification method"))


class OtpBindConfirmSerializer(serializers.Serializer):
    """OTP 绑定确认"""

    code = serializers.CharField(max_length=16, label=_("OTP verification code"))
