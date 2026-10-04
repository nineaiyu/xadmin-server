#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : basic
# author : ly_13
# date : 8/1/2024
import re

from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from common.core.fields import ColorField, StepFloatField
from settings.serializers.contract import SettingSaveContractMixin
from system.services import invalid_user_cache_signal

# 水印文字颜色：#rgb/#rrggbbaa 十六进制、rgb()/rgba()/hsl()/hsla() 函数、CSS 颜色名
WATERMARK_COLOR_RE = re.compile(r"^(#[0-9a-fA-F]{3,8}|(rgb|rgba|hsl|hsla)\([^)]*\)|[a-zA-Z]+)$")


class BasicSettingSerializer(SettingSaveContractMixin, serializers.Serializer):
    SITE_URL = serializers.URLField(
        required=False,
        label=_("Site URL"),
        help_text=_(
            "Site URL is the externally accessible address of the current product "
            "service and is usually used in links in system emails"
        ),
    )

    FRONT_END_WEB_WATERMARK_ENABLED = serializers.BooleanField(
        required=False,
        default=True,
        label=_("Front-end web watermark"),
        help_text=_("Enable watermark for front-end web"),
    )

    FRONT_END_WEB_WATERMARK_TEXT = serializers.CharField(
        required=False,
        allow_blank=True,
        default="",
        max_length=512,
        label=_("Front-end web watermark text"),
        help_text=_(
            "Watermark text template; placeholders: {username} {nickname} {phone} {email} {pk} {time}; "
            "leave empty for '{username}-{nickname}-{time}'; use '、' for line breaks"
        ),
    )

    FRONT_END_WEB_WATERMARK_PATHS = serializers.CharField(
        required=False,
        allow_blank=True,
        default="",
        label=_("Front-end web watermark pages"),
        help_text=_(
            "Comma-separated route path prefixes the watermark applies to; leave empty for all pages. e.g. /system/user/index,/system/role/index"
        ),
    )

    FRONT_END_WEB_WATERMARK_FONT_SIZE = serializers.IntegerField(
        required=False,
        min_value=8,
        max_value=72,
        default=16,
        label=_("Front-end web watermark font size"),
        help_text=_("Watermark text font size in pixels (8-72)"),
    )

    FRONT_END_WEB_WATERMARK_OPACITY = StepFloatField(
        required=False,
        min_value=0.01,
        max_value=1,
        default=0.3,
        step=0.1,
        label=_("Front-end web watermark opacity"),
        help_text=_("Watermark opacity (0.01-1); larger values are more visible"),
    )

    FRONT_END_WEB_WATERMARK_ROTATE = serializers.IntegerField(
        required=False,
        min_value=-90,
        max_value=90,
        default=-10,
        label=_("Front-end web watermark rotation"),
        help_text=_("Watermark rotation angle in degrees (-90 to 90)"),
    )

    FRONT_END_WEB_WATERMARK_COLOR = ColorField(
        required=False,
        allow_blank=True,
        default="",
        max_length=64,
        label=_("Front-end web watermark color"),
        help_text=_("Watermark text color, hex (e.g. #909399) or rgba(...); leave empty for default gray"),
    )

    PERMISSION_FIELD_ENABLED = serializers.BooleanField(
        required=False,
        default=True,
        label=_("Field permission"),
        help_text=_("Field permissions are used to authorize access to data field display"),
    )

    PERMISSION_DATA_ENABLED = serializers.BooleanField(
        required=False,
        default=True,
        label=_("Data permission"),
        help_text=_("Data permissions are used to authorize access to data"),
    )

    EXPORT_MAX_LIMIT = serializers.IntegerField(
        required=False, label=_("Export max limit"), help_text=_("Limit the maximum number of rows of exported data")
    )

    @staticmethod
    def validate_SITE_URL(s):
        if not s:
            return "http://127.0.0.1"
        return s.strip("/")

    @staticmethod
    def validate_FRONT_END_WEB_WATERMARK_PATHS(value):
        """归一化生效页面为逗号分隔的路由前缀列表。

        每项必须以 / 开头：水印按路由前缀匹配，无效项（如直接填写中文说明）
        会让所有页面都匹配不上，水印静默失效——保存期就拒绝并给出明确报错，
        而不是留到运行期无声无息。容忍中文逗号与空白。
        """
        if not value:
            return ""
        items = [item.strip() for item in value.replace("，", ",").split(",") if item.strip()]
        invalid = [item for item in items if not item.startswith("/")]
        if invalid:
            raise serializers.ValidationError(
                _("Invalid watermark pages: %(value)s; each entry must be a route path starting with '/'")
                % {"value": ", ".join(invalid)}
            )
        return ",".join(items)

    @staticmethod
    def validate_FRONT_END_WEB_WATERMARK_COLOR(value):
        """校验水印文字颜色：十六进制 / rgb(a) / hsl(a) / CSS 颜色名，留空 = 默认灰。

        值最终进入 canvas fillStyle，非法值只会静默画出默认色——保存期直接拒绝，
        让「配了颜色却没生效」在配置时就暴露。
        """
        value = (value or "").strip()
        if not value:
            return ""
        if not WATERMARK_COLOR_RE.match(value):
            raise serializers.ValidationError(
                _("Invalid watermark color: %(value)s; use hex like #909399 or rgba(...)") % {"value": value}
            )
        return value

    def post_save(self):
        if set(self.change_fields) & {"PERMISSION_FIELD_ENABLED", "PERMISSION_DATA_ENABLED"}:
            invalid_user_cache_signal.send(sender=self, user_pk="*")
