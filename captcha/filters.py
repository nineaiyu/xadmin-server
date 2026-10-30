#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""验证码观测日志的列表过滤（供 settings 域只读列表页消费）。"""

from django_filters import rest_framework as filters

from captcha.models import CaptchaLog
from common.core.filter import BaseFilterSet


class CaptchaLogFilter(BaseFilterSet):
    scene = filters.CharFilter(field_name="scene", lookup_expr="icontains")
    action = filters.CharFilter(field_name="action", lookup_expr="icontains")
    browser = filters.CharFilter(field_name="browser", lookup_expr="icontains")

    class Meta:
        model = CaptchaLog
        fields = ["phase", "scene", "action", "type", "decision", "passed", "ua_class", "browser", "ts"]
