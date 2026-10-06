#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""identity 域安全序列化器（风险巡检 / 登录策略 / Passkey）。

FileAccessLogSerializer 属文件域，在 system/serializers/security.py（随 file 域切分迁移）。
"""

from ipaddress import ip_address

from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from common.core.fields import LabeledChoiceField
from common.core.serializers import BaseModelSerializer
from common.utils.ip import is_ip_address, is_ip_network, is_ip_segment
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


def validate_login_policy_ip_ranges(value):
    """逐行校验登录策略网段条目，判定面与 basic 页 ip 组共用（is_ip_segment 等）。

    运行时对无法识别的条目只会退化为「与登录 IP 字符串比对」——对真实登录 IP
    永远不会命中，配置等于静默失效。因此保存期把运行时无法真正匹配的条目
    一律拒绝，并回显条目原文，避免管理员配置的网段限制无声失效。
    """
    for entry in (line.strip() for line in str(value or "").splitlines() if line.strip()):
        if entry == "*" or is_ip_address(entry) or is_ip_network(entry) or is_ip_segment(entry):
            continue
        parts = entry.split("-")
        if len(parts) == 2 and is_ip_address(parts[0]) and is_ip_address(parts[1]):
            # 区间形态的精细化提示：能走到这里说明同族/有序两项至少缺一，按因给错
            start_ip, end_ip = ip_address(parts[0]), ip_address(parts[1])
            if type(start_ip) is not type(end_ip):
                raise serializers.ValidationError(
                    _("Invalid IP range entry: `{}`, the start and end IP must be of the same IP version").format(entry)
                )
            if int(start_ip) > int(end_ip):
                raise serializers.ValidationError(
                    _("Invalid IP range entry: `{}`, the start IP must not be greater than the end IP").format(entry)
                )
            continue
        raise serializers.ValidationError(
            _(
                "Invalid IP range entry: `{}`, each line must be a single IP address, a CIDR (e.g. 192.168.1.0/24), "
                "an IP range (e.g. 10.1.1.1-10.1.1.20) or *"
            ).format(entry)
        )


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

    def validate_ip_ranges(self, value):
        validate_login_policy_ip_ranges(value)
        return value


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
