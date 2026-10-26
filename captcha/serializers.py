#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""交互式验证码请求 / 响应序列化器（OpenAPI 契约来源）。"""

from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from captcha.const import CAPTCHA_SCENES, CAPTCHA_TYPES


class CaptchaChallengeInSerializer(serializers.Serializer):
    type = serializers.ChoiceField(
        choices=CAPTCHA_TYPES,
        required=False,
        label=_("Captcha type"),
        help_text=_("Omit to let the server pick one from the enabled types"),
    )
    scene = serializers.ChoiceField(choices=CAPTCHA_SCENES, label=_("Captcha scene"))


class CaptchaTrackPointSerializer(serializers.Serializer):
    x = serializers.FloatField(label=_("X coordinate"))
    y = serializers.FloatField(label=_("Y coordinate"))
    t = serializers.FloatField(label=_("Timestamp (ms)"))


class CaptchaVerifyInSerializer(serializers.Serializer):
    challenge_id = serializers.CharField(max_length=64, label=_("Challenge ID"))
    type = serializers.ChoiceField(choices=CAPTCHA_TYPES, label=_("Captcha type"))
    scene = serializers.ChoiceField(choices=CAPTCHA_SCENES, label=_("Captcha scene"))
    answer = serializers.JSONField(label=_("Answer"))
    track = CaptchaTrackPointSerializer(many=True, required=False, label=_("Pointer track"))


class CaptchaChallengeOutSerializer(serializers.Serializer):
    challenge_id = serializers.CharField(label=_("Challenge ID"))
    type = serializers.ChoiceField(choices=CAPTCHA_TYPES, label=_("Captcha type"))
    background = serializers.CharField(label=_("Background image"))
    piece = serializers.CharField(required=False, label=_("Puzzle piece"))
    tip = serializers.CharField(required=False, label=_("Click tip image"))
    width = serializers.IntegerField(required=False, label=_("Image width"))
    height = serializers.IntegerField(required=False, label=_("Image height"))
    piece_size = serializers.IntegerField(required=False, label=_("Piece size"))
    count = serializers.IntegerField(required=False, label=_("Click count"))
    ttl = serializers.IntegerField(label=_("Validity period (second)"))


class CaptchaVerifyOutSerializer(serializers.Serializer):
    passed = serializers.BooleanField(label=_("Passed"))
    ticket = serializers.CharField(required=False, label=_("One-time ticket"))
    expires_in = serializers.IntegerField(required=False, label=_("Ticket TTL (second)"))
    attempts_left = serializers.IntegerField(required=False, label=_("Attempts left"))


class CaptchaChallengeResponseSerializer(serializers.Serializer):
    code = serializers.IntegerField(label=_("Business code"))
    detail = serializers.CharField(required=False, label=_("Detail"))
    error = serializers.CharField(required=False, label=_("Error code"))
    data = CaptchaChallengeOutSerializer()


class CaptchaVerifyResponseSerializer(serializers.Serializer):
    code = serializers.IntegerField(label=_("Business code"))
    detail = serializers.CharField(required=False, label=_("Detail"))
    error = serializers.CharField(required=False, label=_("Error code"))
    data = CaptchaVerifyOutSerializer()
