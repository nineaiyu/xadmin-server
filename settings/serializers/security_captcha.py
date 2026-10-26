#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""验证码设置序列化器：总配置 / 图片验证码 / 交互式验证码三类。

与短信设置同构：总配置负责全局开关与验证码类型选择，各类型分页签只暴露
自己的参数；后续新增验证码类型时，新增一个 Serializer + ViewSet + 前端页签即可。
"""

from django.db.models import TextChoices
from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from captcha.contracts import CAPTCHA_TYPES
from common.core.fields import ColorField
from settings.serializers.contract import SettingSaveContractMixin


class SecurityCaptchaGeneralSerializer(SettingSaveContractMixin, serializers.Serializer):
    class ModeChoices(TextChoices):
        IMAGE = "image", _("Image captcha")
        INTERACTIVE = "interactive", _("Interactive captcha")

    CAPTCHA_ENABLED = serializers.BooleanField(
        required=False,
        default=True,
        label=_("Enable captcha"),
        help_text=_("Global switch: when disabled, no scene requires a captcha"),
    )

    CAPTCHA_MODE = serializers.ChoiceField(
        choices=ModeChoices.choices,
        required=False,
        default=ModeChoices.IMAGE,
        label=_("Captcha mode"),
        help_text=_("Captcha type used by all scenes; new types can be added as separate tabs"),
    )


class SecurityCaptchaCodeSerializer(SettingSaveContractMixin, serializers.Serializer):
    class ChallengeChoices(TextChoices):
        RANDOM_CHAR = "captcha.helpers.random_char_challenge", _("Random char")
        MATH_CHALLENGE = "captcha.helpers.math_challenge", _("Math challenge")

    class NoiseFunctionsChoices(TextChoices):
        FUNCTION_NULL = "captcha.helpers.noise_null", _("Noise function null")
        FUNCTION_ARCS = "captcha.helpers.noise_arcs", _("Noise function arcs")
        FUNCTION_DOTS = "captcha.helpers.noise_dots", _("Noise function dots")

    CAPTCHA_CHALLENGE_FUNCT = serializers.ChoiceField(
        choices=ChallengeChoices.choices,
        default=ChallengeChoices.MATH_CHALLENGE,
        label=_("Challenge generator"),
        help_text=_("Image verification code generation mode"),
    )

    CAPTCHA_LENGTH = serializers.IntegerField(
        default=4, min_value=2, max_value=16, label=_("Captcha code length"), help_text=_("Length of the captcha code")
    )

    CAPTCHA_FONT_SIZE = serializers.IntegerField(
        default=26,
        min_value=10,
        max_value=50,
        label=_("Captcha font size"),
        help_text=_("Font size of the captcha code"),
    )

    CAPTCHA_TIMEOUT = serializers.IntegerField(
        min_value=1,
        max_value=60 * 24 * 7,
        label=_("Captcha timeout (minute)"),
        help_text=_("Captcha code expiration time"),
    )

    CAPTCHA_BACKGROUND_COLOR = ColorField(max_length=256, required=True, label=_("Captcha background color"))
    CAPTCHA_FOREGROUND_COLOR = ColorField(max_length=256, required=True, label=_("Captcha foreground color"))

    CAPTCHA_NOISE_FUNCTIONS = serializers.MultipleChoiceField(
        label=_("Noise functions"),
        default=[
            NoiseFunctionsChoices.FUNCTION_ARCS,
            NoiseFunctionsChoices.FUNCTION_DOTS,
        ],
        choices=NoiseFunctionsChoices.choices,
    )


class SecurityCaptchaInteractiveSerializer(SettingSaveContractMixin, serializers.Serializer):
    class InteractiveTypeChoices(TextChoices):
        SLIDE = "slide", _("Slide puzzle")
        ROTATE = "rotate", _("Rotate upright")
        CLICK = "click", _("Word click")

    class ClickCharsetChoices(TextChoices):
        CHINESE = "chinese", _("Common Chinese characters")
        ASCII = "ascii", _("Letters and digits")

    CAPTCHA_INTERACTIVE_TYPES = serializers.MultipleChoiceField(
        choices=InteractiveTypeChoices.choices,
        required=False,
        default=["slide", "rotate", "click"],
        label=_("Interactive captcha types"),
        help_text=_("Enabled challenge types when the mode is interactive"),
    )

    CAPTCHA_SLIDE_TOLERANCE = serializers.IntegerField(
        required=False,
        default=4,
        min_value=1,
        max_value=30,
        label=_("Slide tolerance (px)"),
    )

    CAPTCHA_ROTATE_TOLERANCE = serializers.IntegerField(
        required=False,
        default=4,
        min_value=1,
        max_value=30,
        label=_("Rotate tolerance (degree)"),
    )

    CAPTCHA_CLICK_RADIUS = serializers.IntegerField(
        required=False,
        default=20,
        min_value=5,
        max_value=80,
        label=_("Click hit radius (px)"),
    )

    CAPTCHA_CHALLENGE_TTL = serializers.IntegerField(
        required=False,
        default=120,
        min_value=30,
        max_value=600,
        label=_("Challenge validity (second)"),
    )

    CAPTCHA_TICKET_TTL = serializers.IntegerField(
        required=False,
        default=120,
        min_value=30,
        max_value=600,
        label=_("Captcha ticket validity (second)"),
    )

    CAPTCHA_MAX_ATTEMPTS = serializers.IntegerField(
        required=False,
        default=3,
        min_value=1,
        max_value=10,
        label=_("Max attempts per challenge"),
    )

    CAPTCHA_TRACK_CHECK_ENABLED = serializers.BooleanField(
        required=False,
        default=True,
        label=_("Pointer track check"),
        help_text=_("Reject answers without a plausible pointer trajectory"),
    )

    CAPTCHA_SLIDE_SIZE = serializers.ListField(
        required=False,
        default=[320, 180],
        child=serializers.IntegerField(min_value=120, max_value=800),
        label=_("Slide background size (px)"),
    )

    CAPTCHA_ROTATE_SIZE = serializers.ListField(
        required=False,
        default=[240, 240],
        child=serializers.IntegerField(min_value=160, max_value=800),
        label=_("Rotate image size (px)"),
    )

    CAPTCHA_CLICK_CHARSET = serializers.ChoiceField(
        choices=ClickCharsetChoices.choices,
        required=False,
        default=ClickCharsetChoices.CHINESE,
        label=_("Click charset"),
    )

    CAPTCHA_IMAGE_FALLBACK_ENABLED = serializers.BooleanField(
        required=False,
        default=False,
        label=_("Image captcha fallback"),
        help_text=_(
            "Allow users to switch to the image captcha when the interactive mode is enabled "
            "(accessibility fallback; weaker against OCR)"
        ),
    )

    @staticmethod
    def _validate_size(value: list[int]) -> list[int]:
        if len(value) != 2:
            raise serializers.ValidationError(_("Size must contain width and height"))
        return value

    def validate_CAPTCHA_SLIDE_SIZE(self, value: list[int]) -> list[int]:
        return self._validate_size(value)

    def validate_CAPTCHA_ROTATE_SIZE(self, value: list[int]) -> list[int]:
        return self._validate_size(value)


class CaptchaImagePreviewDataSerializer(serializers.Serializer):
    image = serializers.CharField(label=_("Preview image"))
    length = serializers.IntegerField(label=_("Answer length"))


class CaptchaImagePreviewResponseSerializer(serializers.Serializer):
    code = serializers.IntegerField(label=_("Business code"))
    detail = serializers.CharField(required=False, label=_("Detail"))
    data = CaptchaImagePreviewDataSerializer()


class CaptchaInteractivePreviewInSerializer(serializers.Serializer):
    type = serializers.ChoiceField(choices=CAPTCHA_TYPES, required=False, label=_("Captcha type"))


class CaptchaInteractivePreviewDataSerializer(serializers.Serializer):
    type = serializers.ChoiceField(choices=CAPTCHA_TYPES, label=_("Captcha type"))
    background = serializers.CharField(label=_("Background image"))
    piece = serializers.CharField(required=False, label=_("Puzzle piece"))
    tip = serializers.CharField(required=False, label=_("Click tip image"))
    width = serializers.IntegerField(required=False, label=_("Image width"))
    height = serializers.IntegerField(required=False, label=_("Image height"))
    piece_size = serializers.IntegerField(required=False, label=_("Piece size"))
    count = serializers.IntegerField(required=False, label=_("Click count"))


class CaptchaInteractivePreviewResponseSerializer(serializers.Serializer):
    code = serializers.IntegerField(label=_("Business code"))
    detail = serializers.CharField(required=False, label=_("Detail"))
    data = CaptchaInteractivePreviewDataSerializer()
