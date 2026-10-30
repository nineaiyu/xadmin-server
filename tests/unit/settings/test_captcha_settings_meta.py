# -*- coding: utf-8 -*-
"""验证码设置字段守护：总配置 / 图片 / 交互式三类独立元数据。"""

from rest_framework.fields import empty

from server.const import CONFIG
from settings.serializers.security import (
    SecurityCaptchaCodeSerializer,
    SecurityCaptchaGeneralSerializer,
    SecurityCaptchaInteractiveSerializer,
)

GENERAL_FIELDS = ("CAPTCHA_ENABLED",)
IMAGE_FIELDS = (
    "CAPTCHA_CHALLENGE_FUNCT",
    "CAPTCHA_LENGTH",
    "CAPTCHA_FONT_SIZE",
    "CAPTCHA_BACKGROUND_COLOR",
    "CAPTCHA_FOREGROUND_COLOR",
    "CAPTCHA_NOISE_FUNCTIONS",
)
INTERACTIVE_FIELDS = (
    "CAPTCHA_ENABLED_TYPES",
    "CAPTCHA_SLIDE_TOLERANCE",
    "CAPTCHA_ROTATE_TOLERANCE",
    "CAPTCHA_CLICK_RADIUS",
    "CAPTCHA_CHALLENGE_TTL",
    "CAPTCHA_TICKET_TTL",
    "CAPTCHA_MAX_ATTEMPTS",
    "CAPTCHA_TRACK_CHECK_ENABLED",
    "CAPTCHA_SLIDE_SIZE",
    "CAPTCHA_ROTATE_SIZE",
    "CAPTCHA_CLICK_CHARSET",
    "CAPTCHA_IMAGE_FALLBACK_ENABLED",
    "CAPTCHA_POW_FOR_IMAGE_FALLBACK",
)


def _assert_fields(serializer_class, names):
    fields = serializer_class().fields
    for name in names:
        assert name in fields, f"设置页缺少字段 {name}"
        assert fields[name].label, f"{name} 缺少可读 label"


def _default_matches(serializer_class, name) -> bool | None:
    default = serializer_class().fields[name].default
    if default is empty:
        return None
    config = CONFIG.defaults[name]
    if isinstance(default, (list, tuple)):
        default = list(default)
    if isinstance(config, (list, tuple)):
        config = list(config)
    return default == config


def test_field_sets_are_split_by_type():
    _assert_fields(SecurityCaptchaGeneralSerializer, GENERAL_FIELDS)
    _assert_fields(SecurityCaptchaCodeSerializer, IMAGE_FIELDS)
    _assert_fields(SecurityCaptchaInteractiveSerializer, INTERACTIVE_FIELDS)
    assert "CAPTCHA_MODE" not in SecurityCaptchaCodeSerializer().fields
    assert "CAPTCHA_ENABLED_TYPES" not in SecurityCaptchaCodeSerializer().fields


def test_serializer_defaults_match_settings_defaults():
    for serializer_class, names in (
        (SecurityCaptchaGeneralSerializer, GENERAL_FIELDS),
        (SecurityCaptchaCodeSerializer, IMAGE_FIELDS),
        (SecurityCaptchaInteractiveSerializer, INTERACTIVE_FIELDS),
    ):
        for name in names:
            result = _default_matches(serializer_class, name)
            if result is None:
                continue
            assert result, f"{name} 默认值与 settings_defaults 漂移"


def test_global_switch_defaults_on():
    assert SecurityCaptchaGeneralSerializer().fields["CAPTCHA_ENABLED"].default is True
    assert CONFIG.defaults["CAPTCHA_ENABLED"] is True


def test_charset_and_type_choices_are_closed():
    for serializer_class, name, value in (
        (SecurityCaptchaInteractiveSerializer, "CAPTCHA_CLICK_CHARSET", "emoji"),
        (SecurityCaptchaInteractiveSerializer, "CAPTCHA_ENABLED_TYPES", ["unknown"]),
    ):
        serializer = serializer_class(data={name: value}, partial=True)
        assert not serializer.is_valid()
        assert name in serializer.errors


def test_interactive_size_requires_width_and_height():
    serializer = SecurityCaptchaInteractiveSerializer(data={"CAPTCHA_SLIDE_SIZE": [300, 200]}, partial=True)
    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["CAPTCHA_SLIDE_SIZE"] == [300, 200]

    invalid = SecurityCaptchaInteractiveSerializer(data={"CAPTCHA_SLIDE_SIZE": [300]}, partial=True)
    assert not invalid.is_valid()
    assert "CAPTCHA_SLIDE_SIZE" in invalid.errors
