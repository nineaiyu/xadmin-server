#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : verify
# author : ly_13
# date : 8/7/2024
from django.db.models import TextChoices
from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from common.core.fields import LabeledChoiceField


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


class CaptchaSettingSerializer(serializers.Serializer):
    class CHALLENGE(TextChoices):
        RANDOM_CHAR = 'captcha.helpers.random_char_challenge', _('Random char')
        MATH_CHALLENGE = 'captcha.helpers.math_challenge', _('Math challenge')

    CAPTCHA_CHALLENGE_FUNCT = LabeledChoiceField(
        choices=CHALLENGE.choices, default=CHALLENGE.MATH_CHALLENGE, label=_('Challenge generator'),
        help_text=_('Image verification code generation mode')
    )

    CAPTCHA_LENGTH = serializers.IntegerField(
        default=4, min_value=2, max_value=16, label=_('Captcha code length'),
        help_text=_('Length of the captcha code')
    )

    CAPTCHA_TIMEOUT = serializers.IntegerField(
        min_value=1, max_value=60 * 24 * 7, label=_('Captcha timeout (minute)'),
        help_text=_("Captcha code expiration time")
    )

    CAPTCHA_NOISE_FUNCTION_ARCS = serializers.BooleanField(
        required=True, label=_('Noise function arcs'),
    )

    CAPTCHA_NOISE_FUNCTION_DOTS = serializers.BooleanField(
        required=True, label=_('Noise function dots')
    )

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data['CAPTCHA_NOISE_FUNCTIONS'] = ['captcha.helpers.noise_null']
        if data['CAPTCHA_NOISE_FUNCTION_ARCS']:
            data['CAPTCHA_NOISE_FUNCTIONS'].append('captcha.helpers.noise_arcs')
        if data['CAPTCHA_NOISE_FUNCTION_DOTS']:
            data['CAPTCHA_NOISE_FUNCTIONS'].append('captcha.helpers.noise_dots')
        return data
