#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""定时报表序列化器（自 analysis.py 拆分，行为不变）。

投递渠道常量（邮件 + 三个 IM 渠道）与调度/收件人校验；执行以创建者权限
上下文进行，非创建者只看收件人占位。
"""

import re
from typing import Any

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from common.core.credentials import MASK
from common.core.serializers import BaseModelSerializer
from dataset.models.dataset import Dataset, Report
from dataset.utils.dataset import dataset_visible_to, numeric_columns_of
from dataset.utils.report_design import ReportDesignError, normalize_report_design

SEND_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
# 报表投递渠道：邮件 + 三个 IM 渠道（空 = 仅邮件，存量兼容）
REPORT_NOTIFY_CHANNELS = ("email", "dingtalk", "wecom", "feishu")
IM_NOTIFY_CHANNELS = ("dingtalk", "wecom", "feishu")


class ReportSerializer(BaseModelSerializer):
    """定时报表。执行以创建者权限上下文进行（menu 上下文为空 = 仅全局授权）。"""

    ignore_field_permission = True
    # 数据集是「定义资源」：绑定路径只校验可见性（validate_dataset），不再叠加
    # dataset.dataset 数据权限——否则只有报表菜单权限的用户能看到下拉、保存却
    # 报 "Invalid pk ... or the current role has no data permission"。执行期仍
    # 按创建者数据权限 fail-closed 收敛行集。
    dataset = serializers.PrimaryKeyRelatedField(queryset=Dataset.objects.all(), label=_("Dataset"))
    # 非创建者修改/删除被写守卫拒绝（1003）：下发 is_owner 供行内按钮显隐
    row_owner_guard = True

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
            "design",
            "frequency",
            "send_time",
            "weekday",
            "month_day",
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
            "last_run_at",
            "last_status",
            "updated_time",
        ]

    def to_representation(self, instance: Any) -> dict[str, Any]:
        """非创建者只看收件人占位：调度配置可见不等于邮箱/IM 账号明细可见。"""
        data = super().to_representation(instance)
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if (
            user is not None
            and getattr(user, "is_authenticated", False)
            and not getattr(user, "is_superuser", False)
            and instance.creator_id != getattr(user, "pk", None)
        ):
            for field in ("recipients", "im_recipients"):
                values = data.get(field)
                if isinstance(values, list):
                    data[field] = [MASK] * len(values)
        return dict(data)

    def validate_dataset(self, value: Any) -> Any:
        if not Dataset.objects.filter(pk=value.pk).exists():
            raise serializers.ValidationError(_("Unknown dataset in layout"))
        # 存在性不等于可见性：绑定他人 personal 数据集会把对方的数据口径
        # （列 / 过滤 / 排序）复制进自己的报表，运行后还能拿到结果数据。
        request = self.context.get("request")
        viewer = getattr(request, "user", None) or getattr(self.instance, "creator", None)
        if not dataset_visible_to(viewer, value.pk):
            raise serializers.ValidationError(_("The dataset is not visible to you (personal dataset of another user)"))
        return value

    def validate_recipients(self, value: Any) -> Any:
        if not isinstance(value, list):
            raise serializers.ValidationError(_("Recipients cannot be empty"))
        for item in value:
            try:
                validate_email(str(item))
            except ValidationError as exc:
                raise serializers.ValidationError(_("Invalid email address: {}").format(item)) from exc
        return [str(item) for item in value]

    def validate_notify_channels(self, value: Any) -> Any:
        if not isinstance(value, list):
            raise serializers.ValidationError(_("Invalid notify channels"))
        unknown = [item for item in value if item not in REPORT_NOTIFY_CHANNELS]
        if unknown:
            raise serializers.ValidationError(
                _("Unknown notify channel: {}").format(", ".join(str(i) for i in unknown))
            )
        # 去重保序
        return list(dict.fromkeys(value))

    def validate_im_recipients(self, value: Any) -> Any:
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

    def validate(self, attrs: Any) -> Any:
        merged_mode = attrs.get("mode", getattr(self.instance, "mode", "rows"))
        # 报表设计：列 / 聚合字段都按数据集与数值列校验，单一事实源在
        # dataset.utils.report_design；空载荷 = 存量口径（全列明细单表），不做强制。
        if "design" in attrs:
            dataset = attrs.get("dataset", getattr(self.instance, "dataset", None))
            try:
                attrs["design"] = normalize_report_design(
                    attrs.get("design"), dataset, numeric_columns_of(dataset) if dataset else []
                )
            except ReportDesignError as exc:
                raise serializers.ValidationError(str(exc)) from exc
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
        # 每月几号（monthly 用）：1~28 避开月末歧义（2 月 30 号等永不命中的日期）
        month_day = attrs.get("month_day", getattr(self.instance, "month_day", 1))
        try:
            month_day = int(month_day)
        except (TypeError, ValueError) as exc:
            raise serializers.ValidationError(_("Month day must be between 1 and 28")) from exc
        if not 1 <= month_day <= 28:
            raise serializers.ValidationError(_("Month day must be between 1 and 28"))
        attrs["month_day"] = month_day
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
