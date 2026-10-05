#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""identity 域安全序列化器（风险巡检 / 登录策略 / Passkey）。

FileAccessLogSerializer 属文件域，在 system/serializers/security.py（随 file 域切分迁移）。
"""

from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from common.core.fields import LabeledChoiceField
from common.core.serializers import BaseModelSerializer
from identity.models import AccountRisk, LoginAccessPolicy, UserPasskey


class AccountRiskSerializer(BaseModelSerializer):
    risk_type = LabeledChoiceField(choices=AccountRisk.RiskType.choices, required=False)
    level = LabeledChoiceField(choices=AccountRisk.Level.choices, required=False)
    status = LabeledChoiceField(choices=AccountRisk.Status.choices, required=False)

    class Meta:
        model = AccountRisk
        fields = [
            "pk",
            "user",
            "user_display",
            "risk_type",
            "level",
            "status",
            "detail",
            "handled_by",
            "handled_at",
            "remark",
            "created_time",
            "updated_time",
        ]
        read_only_fields = [
            "pk",
            "user",
            "user_display",
            "risk_type",
            "level",
            "detail",
            "handled_by",
            "handled_at",
            "created_time",
            "updated_time",
        ]
        table_fields = ["user_display", "risk_type", "level", "status", "remark", "handled_at", "created_time"]


class LoginAccessPolicySerializer(BaseModelSerializer):
    action = LabeledChoiceField(choices=LoginAccessPolicy.Action.choices)
    target_type = LabeledChoiceField(choices=LoginAccessPolicy.TargetType.choices)

    class Meta:
        model = LoginAccessPolicy
        fields = [
            "pk",
            "name",
            "priority",
            "is_active",
            "target_type",
            "target_value",
            "weekdays",
            "start_time",
            "end_time",
            "ip_ranges",
            "action",
            "remark",
            "created_time",
            "updated_time",
        ]
        table_fields = ["name", "priority", "is_active", "target_type", "action", "remark", "updated_time"]

    def validate(self, attrs):
        instance = self.instance
        start = attrs.get("start_time", getattr(instance, "start_time", None))
        end = attrs.get("end_time", getattr(instance, "end_time", None))
        if bool(start) != bool(end):
            raise serializers.ValidationError(_("Start time and end time must be set together"))
        target_type = attrs.get("target_type", getattr(instance, "target_type", None))
        target_value = attrs.get("target_value", getattr(instance, "target_value", ""))
        if (
            target_type in (LoginAccessPolicy.TargetType.USER, LoginAccessPolicy.TargetType.ROLE)
            and not str(target_value or "").strip()
        ):
            raise serializers.ValidationError(_("Please specify the target users or roles"))
        return attrs


class UserPasskeySerializer(BaseModelSerializer):
    class Meta:
        model = UserPasskey
        fields = [
            "pk",
            "credential_id",
            "name",
            "aaguid",
            "backed_up",
            "last_used_at",
            "created_time",
        ]
        read_only_fields = ["pk", "credential_id", "aaguid", "backed_up", "last_used_at", "created_time"]
        table_fields = ["name", "aaguid", "last_used_at", "created_time"]
