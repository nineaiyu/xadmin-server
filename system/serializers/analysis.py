#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""大屏与报表序列化器（ADR-021）。定义类资源，豁免字段权限裁剪。"""

import re

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from common.core.serializers import BaseModelSerializer
from system.models.dataset import Dashboard, Dataset, Report, Screen

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
SEND_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class ScreenSerializer(BaseModelSerializer):
    """大屏模板。可见性同 Dataset（personal/shared）；创建者与超管可改。"""

    ignore_field_permission = True

    class Meta:
        model = Screen
        fields = ["pk", "name", "dashboards", "interval", "refresh", "visibility", "created_time", "updated_time"]
        read_only_fields = ["pk", "created_time", "updated_time"]

    def validate_dashboards(self, value):
        if not isinstance(value, list):
            raise serializers.ValidationError(_("Invalid screen dashboards"))
        known = {str(pk) for pk in Dashboard.objects.values_list("pk", flat=True)}
        for pk in value:
            if str(pk) not in known:
                raise serializers.ValidationError(_("Unknown dashboard in layout"))
        return value

    def validate_interval(self, value):
        if not (5 <= int(value) <= 3600):
            raise serializers.ValidationError(_("Interval must be between 5 and 3600 seconds"))
        return value

    def validate_refresh(self, value):
        if not (10 <= int(value) <= 3600):
            raise serializers.ValidationError(_("Refresh must be between 10 and 3600 seconds"))
        return value


class ReportSerializer(BaseModelSerializer):
    """定时报表。执行以创建者权限上下文进行（menu 上下文为空 = 仅全局授权）。"""

    ignore_field_permission = True

    class Meta:
        model = Report
        fields = [
            "pk",
            "name",
            "dataset",
            "mode",
            "group_by",
            "metric",
            "date_trunc",
            "value_field",
            "frequency",
            "send_time",
            "weekday",
            "recipients",
            "is_active",
            "last_run_at",
            "last_status",
            "created_time",
            "updated_time",
        ]
        read_only_fields = ["pk", "last_run_at", "last_status", "created_time", "updated_time"]

    def validate_dataset(self, value):
        if Dataset.objects.filter(pk=value.pk).exists():
            return value
        raise serializers.ValidationError(_("Unknown dataset in layout"))

    def validate_recipients(self, value):
        if not isinstance(value, list) or not value:
            raise serializers.ValidationError(_("Recipients cannot be empty"))
        for item in value:
            try:
                validate_email(str(item))
            except ValidationError as exc:
                raise serializers.ValidationError(_("Invalid email address: {}").format(item)) from exc
        return [str(item) for item in value]

    def validate(self, attrs):
        merged_mode = attrs.get("mode", getattr(self.instance, "mode", "rows"))
        if merged_mode not in ("rows", "aggregate"):
            raise serializers.ValidationError(_("Invalid report mode"))
        if merged_mode == "aggregate":
            group_by = attrs.get("group_by", getattr(self.instance, "group_by", ""))
            if not group_by:
                raise serializers.ValidationError(_("Group by is required for aggregate reports"))
        send_time = attrs.get("send_time", getattr(self.instance, "send_time", ""))
        if send_time and not SEND_TIME_RE.match(send_time):
            raise serializers.ValidationError(_("Send time must be HH:MM"))
        weekday = attrs.get("weekday", getattr(self.instance, "weekday", 0))
        if not (0 <= int(weekday) <= 6):
            raise serializers.ValidationError(_("Weekday must be between 0 and 6"))
        return attrs
