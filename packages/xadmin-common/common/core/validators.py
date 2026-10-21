#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : validators
# author : ly_13
# date : 8/6/2024
import re
from typing import Any

import phonenumbers
from django.utils.translation import gettext_lazy as _
from phonenumbers import NumberParseException
from rest_framework import serializers

#: 颜色值口径（#rgb/#rgba/#rrggbb/#rrggbbaa、rgb()/rgba()/hsl()/hsla()、CSS 颜色名）。
#: 水印颜色等手写校验与 ColorField 共用此单一常量，避免同语义两处口径。
COLOR_PATTERN = re.compile(
    r"^(#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})|(rgb|rgba|hsl|hsla)\([^)]*\)|[a-zA-Z]+)$"
)


class PhoneValidator:
    message = _("The phone number format is incorrect")

    def __call__(self, value: Any) -> None:
        if not value:
            return

        try:
            phone = phonenumbers.parse(value, "CN")
            valid = phonenumbers.is_valid_number(phone)
        except NumberParseException:
            valid = False

        if not valid:
            raise serializers.ValidationError(self.message)


class ColorValidator:
    """颜色值格式校验（空值放行，必填由字段 required 语义接管）。"""

    message = _("Invalid color value: use hex like #409EFF or rgb(...)")

    def __call__(self, value: Any) -> None:
        if not value:
            return
        if not COLOR_PATTERN.match(str(value)):
            raise serializers.ValidationError(self.message)
