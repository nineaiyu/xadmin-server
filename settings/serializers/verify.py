#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : verify
# author : ly_13
# date : 8/7/2024
from django.db.models import TextChoices
from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from common.core.fields import ColorField


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
    class ChallengeChoices(TextChoices):
        RANDOM_CHAR = 'captcha.helpers.random_char_challenge', _('Random char')
        MATH_CHALLENGE = 'captcha.helpers.math_challenge', _('Math challenge')

    class NoiseFunctionsChoices(TextChoices):
        FUNCTION_NULL = 'captcha.helpers.noise_null', _('Noise function null')
        FUNCTION_ARCS = 'captcha.helpers.noise_arcs', _('Noise function arcs')
        FUNCTION_DOTS = 'captcha.helpers.noise_dots', _('Noise function dots')

    CAPTCHA_CHALLENGE_FUNCT = serializers.ChoiceField(choices=ChallengeChoices.choices,
                                                      default=ChallengeChoices.MATH_CHALLENGE,
                                                      label=_('Challenge generator'),
                                                      help_text=_('Image verification code generation mode'))

    CAPTCHA_LENGTH = serializers.IntegerField(
        default=4, min_value=2, max_value=16, label=_('Captcha code length'),
        help_text=_('Length of the captcha code')
    )

    CAPTCHA_FONT_SIZE = serializers.IntegerField(
        default=22, min_value=10, max_value=50, label=_('Captcha font size'),
        help_text=_('Font size of the captcha code')
    )

    CAPTCHA_TIMEOUT = serializers.IntegerField(
        min_value=1, max_value=60 * 24 * 7, label=_('Captcha timeout (minute)'),
        help_text=_("Captcha code expiration time")
    )

    CAPTCHA_BACKGROUND_COLOR = ColorField(max_length=256, required=True, label=_('Captcha background color'))
    CAPTCHA_FOREGROUND_COLOR = ColorField(max_length=256, required=True, label=_('Captcha foreground color'))

    CAPTCHA_NOISE_FUNCTIONS = serializers.MultipleChoiceField(label=_('Noise functions'),
                                                              default=NoiseFunctionsChoices.FUNCTION_NULL,
                                                              choices=NoiseFunctionsChoices.choices)
