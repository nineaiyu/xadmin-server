#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""大屏与报表序列化器。定义类资源，豁免字段权限裁剪。"""

import re

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from common.core.serializers import BaseModelSerializer
from dataset.models.dataset import Dashboard, Dataset, Report, Screen

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
SEND_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
# 报表投递渠道：邮件 + 三个 IM 渠道（空 = 仅邮件，存量兼容）
REPORT_NOTIFY_CHANNELS = ("email", "dingtalk", "wecom", "feishu")
IM_NOTIFY_CHANNELS = ("dingtalk", "wecom", "feishu")


class ScreenSerializer(BaseModelSerializer):
    """大屏模板。可见性同 Dataset（personal/shared）；创建者与超管可改。"""

    ignore_field_permission = True

    class Meta:
        model = Screen
        fields = ["pk", "name", "dashboards", "interval", "refresh", "visibility", "created_time", "updated_time"]
        read_only_fields = ["pk", "created_time", "updated_time"]
        # RePlusPage 列表列
        table_fields = ["name", "dashboards", "interval", "refresh", "visibility", "updated_time"]

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


class ScreenCommandSerializer(serializers.Serializer):
    """大屏远程控制指令：switch 需 dashboard_pk、page 需 index、refresh/auto 无参。"""

    command = serializers.ChoiceField(
        choices=[
            ("switch", _("Switch dashboard")),
            ("page", _("Go to page")),
            ("refresh", _("Refresh data")),
            ("auto", _("Resume carousel")),
        ],
        label=_("Command"),
    )
    dashboard_pk = serializers.CharField(required=False, allow_blank=True, default="", label=_("Dashboard"))
    index = serializers.IntegerField(required=False, min_value=0, label=_("Page index"))

    def validate(self, attrs):
        if attrs["command"] == "switch" and not attrs.get("dashboard_pk"):
            raise serializers.ValidationError(_("dashboard_pk is required for switch"))
        if attrs["command"] == "page" and attrs.get("index") is None:
            raise serializers.ValidationError(_("index is required for page"))
        return attrs


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
            "cron_expression",
            "recipients",
            "notify_channels",
            "im_recipients",
            "is_active",
            "last_run_at",
            "last_status",
            "created_time",
            "updated_time",
        ]
        read_only_fields = ["pk", "last_run_at", "last_status", "created_time", "updated_time"]
        # RePlusPage 列表列：聚合细则（group_by/metric/date_trunc/value_field）与 IM 收件人明细不进列表
        table_fields = [
            "name",
            "dataset",
            "frequency",
            "send_time",
            "recipients",
            "notify_channels",
            "last_status",
            "updated_time",
        ]

    def validate_dataset(self, value):
        if Dataset.objects.filter(pk=value.pk).exists():
            return value
        raise serializers.ValidationError(_("Unknown dataset in layout"))

    def validate_recipients(self, value):
        if not isinstance(value, list):
            raise serializers.ValidationError(_("Recipients cannot be empty"))
        for item in value:
            try:
                validate_email(str(item))
            except ValidationError as exc:
                raise serializers.ValidationError(_("Invalid email address: {}").format(item)) from exc
        return [str(item) for item in value]

    def validate_notify_channels(self, value):
        if not isinstance(value, list):
            raise serializers.ValidationError(_("Invalid notify channels"))
        unknown = [item for item in value if item not in REPORT_NOTIFY_CHANNELS]
        if unknown:
            raise serializers.ValidationError(
                _("Unknown notify channel: {}").format(", ".join(str(i) for i in unknown))
            )
        # 去重保序
        return list(dict.fromkeys(value))

    def validate_im_recipients(self, value):
        if not isinstance(value, list):
            raise serializers.ValidationError(_("Invalid IM recipients"))
        if len(value) > 50:
            raise serializers.ValidationError(_("Too many IM recipients"))
        cleaned = []
        for item in value:
            if isinstance(item, bool) or not isinstance(item, int) or item <= 0:
                raise serializers.ValidationError(_("Invalid IM recipient: {}").format(item))
            cleaned.append(item)
        return list(dict.fromkeys(cleaned))

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
        # cron 表达式：非空时优先于三档频次；非法表达式直接拒绝
        cron_expression = (attrs.get("cron_expression", getattr(self.instance, "cron_expression", "")) or "").strip()
        if cron_expression:
            from croniter import croniter

            if not croniter.is_valid(cron_expression):
                raise serializers.ValidationError(_("Invalid cron expression"))
        # 投递渠道与收件人联动（渠道空 = 仅邮件，存量数据与旧客户端行为不变）
        channels = attrs.get("notify_channels", getattr(self.instance, "notify_channels", []) or []) or ["email"]
        if "email" in channels:
            recipients = attrs.get("recipients", getattr(self.instance, "recipients", []) or [])
            if not recipients:
                raise serializers.ValidationError(_("Email channel requires at least one recipient"))
        if any(item in IM_NOTIFY_CHANNELS for item in channels):
            im_recipients = attrs.get("im_recipients", getattr(self.instance, "im_recipients", []) or [])
            if not im_recipients:
                raise serializers.ValidationError(_("IM channel requires at least one recipient"))
        return attrs
