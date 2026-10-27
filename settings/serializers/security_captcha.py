#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""验证码设置序列化器：总配置 / 图片验证码 / 交互式验证码三类。

与短信设置同构：总配置负责全局开关与验证码类型选择，各类型分页签只暴露
自己的参数；后续新增验证码类型时，新增一个 Serializer + ViewSet + 前端页签即可。
"""

from typing import Any

from django.conf import settings
from django.db.models import TextChoices
from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from captcha.contracts import CAPTCHA_TYPES
from common.core.fields import ColorField
from settings.serializers.contract import SettingSaveContractMixin


class SecurityCaptchaGeneralSerializer(SettingSaveContractMixin, serializers.Serializer):
    CAPTCHA_ENABLED = serializers.BooleanField(
        required=False,
        default=True,
        label=_("Enable captcha"),
        help_text=_("Global switch: when disabled, no scene requires a captcha"),
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
        IMAGE = "image", _("Image characters (fallback)")
        SLIDE = "slide", _("Slide puzzle")
        ROTATE = "rotate", _("Rotate upright")
        CLICK = "click", _("Word click")
        WORDORDER = "wordorder", _("Word order")
        ICON = "icon", _("Icon click")

    class ClickCharsetChoices(TextChoices):
        SIMPLIFIED = "simplified", _("Simplified Chinese characters")
        TRADITIONAL = "traditional", _("Traditional Chinese characters")
        MIXED = "mixed", _("Mixed simplified / traditional per challenge")
        ASCII = "ascii", _("Letters and digits")

    CAPTCHA_ENABLED_TYPES = serializers.MultipleChoiceField(
        choices=InteractiveTypeChoices.choices,
        required=False,
        default=["slide", "rotate", "click"],
        label=_("Enabled captcha types"),
        help_text=_("Challenge types eligible for random selection; image stays opt-in fallback"),
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
        default=ClickCharsetChoices.SIMPLIFIED,
        label=_("Click charset"),
    )

    CAPTCHA_BACKGROUND_PACK = serializers.CharField(
        required=False,
        allow_blank=True,
        default="builtin",
        max_length=64,
        label=_("Background pack"),
        help_text=_("builtin = bundled manifest (procedural when empty); procedural = disable photo pack"),
    )

    CAPTCHA_IMAGE_FALLBACK_ENABLED = serializers.BooleanField(
        required=False,
        default=True,
        label=_("Image captcha fallback"),
        help_text=_("Expose the image type as an accessibility fallback; it never enters the random pool"),
    )

    CAPTCHA_POW_FOR_IMAGE_FALLBACK = serializers.BooleanField(
        required=False,
        default=True,
        label=_("PoW for image fallback"),
        help_text=_("Require a low-difficulty PoW before accepting an image fallback challenge"),
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


class SecurityCaptchaRiskSerializer(SettingSaveContractMixin, serializers.Serializer):
    """风险策略：模式 / 阈值 / 算力 / 阶梯 / 告警 / 保留期。"""

    class RiskModeChoices(TextChoices):
        OFF = "off", _("Off (freeze current behavior)")
        SHADOW = "shadow", _("Shadow (score and log only)")
        ENFORCE = "enforce", _("Enforce (apply decisions)")

    CAPTCHA_RISK_MODE = serializers.ChoiceField(
        choices=RiskModeChoices.choices,
        required=False,
        default=RiskModeChoices.ENFORCE,
        label=_("Risk mode"),
        help_text=_(
            "off = risk layer disabled; shadow = score and log only; "
            "enforce = apply decisions (default for the hardened profile)"
        ),
    )
    CAPTCHA_RISK_T_ALLOW = serializers.IntegerField(
        required=False,
        default=20,
        min_value=0,
        max_value=100,
        label=_("Allow threshold"),
        help_text=_("Score below this value: no puzzle, ticket issued directly (allow); default 20"),
    )
    CAPTCHA_RISK_T_LIGHT = serializers.IntegerField(
        required=False,
        default=40,
        min_value=0,
        max_value=100,
        label=_("Light threshold"),
        help_text=_("Score at or above this value: one click + PoW (light); default 40; must be greater than allow"),
    )
    CAPTCHA_RISK_T_HARD = serializers.IntegerField(
        required=False,
        default=70,
        min_value=0,
        max_value=100,
        label=_("Hard threshold"),
        help_text=_("Score at or above this value: hard puzzle / word order with tighter tolerance; default 70"),
    )
    CAPTCHA_RISK_T_DENY = serializers.IntegerField(
        required=False,
        default=90,
        min_value=0,
        max_value=100,
        label=_("Deny threshold"),
        help_text=_("Score at or above this value: reject with the same response as a wrong answer; default 90"),
    )
    CAPTCHA_RISK_TRUST_PRIVATE_NETWORKS = serializers.BooleanField(
        required=False,
        default=True,
        label=_("Trust private networks"),
        help_text=_(
            "Treat RFC1918 and loopback addresses as trusted (no puzzle in enforce mode); "
            "keep enabled unless the office network is in the whitelist"
        ),
    )
    CAPTCHA_RISK_DATACENTER_CIDRS = serializers.CharField(
        required=False,
        allow_blank=True,
        default="",
        label=_("Datacenter CIDRs"),
        max_length=4096,
        help_text=_("Comma-separated CIDRs for datacenter/cloud ranges; a hit adds 20 points; empty disables"),
    )
    CAPTCHA_RISK_PROXY_CIDRS = serializers.CharField(
        required=False,
        allow_blank=True,
        default="",
        label=_("Proxy CIDRs"),
        max_length=4096,
        help_text=_("Comma-separated CIDRs for known proxy/TOR exits; a hit adds 30 points; empty disables"),
    )
    CAPTCHA_POW_ENABLED = serializers.BooleanField(
        required=False,
        default=True,
        label=_("Enable PoW"),
        help_text=_("Issue a PoW challenge for allow/light decisions; disabling it weakens the cost axis"),
    )
    CAPTCHA_POW_BITS_ALLOW = serializers.IntegerField(
        required=False,
        default=16,
        min_value=1,
        max_value=24,
        label=_("PoW bits (allow)"),
        help_text=_("Silent PoW difficulty for low-risk allow decisions; default 16"),
    )
    CAPTCHA_STRICT_BINDING = serializers.BooleanField(
        required=False,
        default=True,
        label=_("Strict ticket binding"),
        help_text=_("Require matching sid cookie and X-Captcha-Fp fingerprint when consuming a ticket"),
    )
    CAPTCHA_TICKET_AUDIENCE = serializers.CharField(
        required=False,
        allow_blank=True,
        default="",
        max_length=255,
        label=_("Ticket audience"),
        help_text=_("Deployment identifier bound into tickets; empty = site URL / first allowed host"),
    )
    CAPTCHA_POW_BITS_LIGHT = serializers.IntegerField(
        required=False,
        default=18,
        min_value=1,
        max_value=20,
        label=_("PoW bits (light)"),
        help_text=_("Leading-zero bits for light PoW; 18 means tens of thousands of hashes; higher is slower"),
    )
    CAPTCHA_POW_BITS_HARD = serializers.IntegerField(
        required=False,
        default=20,
        min_value=1,
        max_value=20,
        label=_("PoW bits (hard)"),
        help_text=_("Leading-zero bits for hard PoW; default 20; capped by the bits cap"),
    )
    CAPTCHA_POW_BITS_MAX = serializers.IntegerField(
        required=False,
        default=20,
        min_value=1,
        max_value=24,
        label=_("PoW bits cap"),
        help_text=_("Server-side hard cap (max 24); prevents turning the server into a hash amplifier"),
    )
    CAPTCHA_POW_IP_BUDGET = serializers.IntegerField(
        required=False,
        default=600,
        min_value=0,
        max_value=100000,
        label=_("PoW budget per IP/hour"),
        help_text=_("PoW verifications allowed per IP per hour; 0 = unlimited; requests over budget are rejected"),
    )
    CAPTCHA_FAIL_STEP = serializers.CharField(
        required=False,
        default="3,8,20",
        max_length=32,
        label=_("Failure ladder"),
        help_text=_(
            "Failure ladder: 3,8,20 means 3 failures -> force hard, 8 -> 15 min cooldown, "
            "20 -> 1 hour cooldown; 0,0,0 disables"
        ),
    )
    CAPTCHA_FAILURE_LINK_LOCK = serializers.BooleanField(
        required=False,
        default=False,
        label=_("Link failures to login lock"),
        help_text=_("Also lock the login account when captcha failures reach the ladder threshold; off by default"),
    )
    CAPTCHA_HONEYPOT_ENABLED = serializers.BooleanField(
        required=False,
        default=True,
        label=_("Enable honeypot"),
        help_text=_("Hidden decoy field; filling it proves automation and is denied (no false positives)"),
    )
    CAPTCHA_ALERT_ENABLED = serializers.BooleanField(
        required=False,
        default=True,
        label=_("Enable alerting"),
        help_text=_("Send a site notification when the deny rate or IP challenge spike crosses the threshold"),
    )
    CAPTCHA_ALERT_WINDOW_MINUTES = serializers.IntegerField(
        required=False,
        default=10,
        min_value=1,
        max_value=1440,
        label=_("Alert window (minutes)"),
        help_text=_("Statistics window for alerting; the same alert fires at most once per window"),
    )
    CAPTCHA_ALERT_DENY_RATE = serializers.FloatField(
        required=False,
        default=0.5,
        min_value=0.0,
        max_value=1.0,
        label=_("Alert deny rate"),
        help_text=_("Trigger when deny decisions reach this share of the window (0-1)"),
    )
    CAPTCHA_ALERT_MIN_SAMPLES = serializers.IntegerField(
        required=False,
        default=50,
        min_value=1,
        max_value=100000,
        label=_("Alert minimum samples"),
        help_text=_("Do not alert until the window has enough samples; avoids false alarms on low traffic"),
    )
    CAPTCHA_LOG_RETENTION_DAYS = serializers.IntegerField(
        required=False,
        default=30,
        min_value=0,
        max_value=365,
        label=_("Log retention (days)"),
        help_text=_("CaptchaLog retention in days; 0 = keep forever; a daily beat task purges expired rows"),
    )

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        keys = ["CAPTCHA_RISK_T_ALLOW", "CAPTCHA_RISK_T_LIGHT", "CAPTCHA_RISK_T_HARD", "CAPTCHA_RISK_T_DENY"]
        values = [int(attrs.get(key, getattr(settings, key, 0)) or 0) for key in keys]
        if values != sorted(values) or len(set(values)) != len(values):
            raise serializers.ValidationError(_("Thresholds must be strictly ascending"))
        step = str(attrs.get("CAPTCHA_FAIL_STEP", getattr(settings, "CAPTCHA_FAIL_STEP", "")))
        if step:
            parts = step.split(",")
            if len(parts) != 3 or any(not part.strip().isdigit() for part in parts):
                raise serializers.ValidationError({"CAPTCHA_FAIL_STEP": _("Expected three comma-separated integers")})
        return attrs


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
