#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : verify
# author : ly_13
# date : 8/7/2024

from django.utils.translation import gettext_lazy as _
from rest_framework import serializers


class VerifyCodeSettingSerializer(serializers.Serializer):
    VERIFY_CODE_TTL = serializers.IntegerField(
        min_value=5, max_value=60 * 60 * 10,
        label=_("Verify code TTL (second)"),
        help_text=_("Verify code expiration time")
    )

    VERIFY_CODE_LIMIT = serializers.IntegerField(
        min_value=5, max_value=60 * 60 * 10,
        label=_("Verify code rate (second)"),
        help_text=_("Verify code send rate limit")
    )

    VERIFY_CODE_LENGTH = serializers.IntegerField(
        default=6, min_value=4, max_value=16, label=_('Code length'),
        help_text=_('Length of the sent verification code')
    )

    VERIFY_CODE_UPPER_CASE = serializers.BooleanField(
        required=False, label=_('Uppercase')
    )

    VERIFY_CODE_LOWER_CASE = serializers.BooleanField(
        required=False, label=_('Lowercase')
    )

    VERIFY_CODE_DIGIT_CASE = serializers.BooleanField(
        required=False, label=_('Digits')
    )
