#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : security
# author : ly_13
# date : 8/1/2024
from django.db.models import TextChoices
from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from common.core.fields import ColorField
from common.utils.ip import is_ip_address, is_ip_network, is_ip_segment


class SecurityPasswordRuleSerializer(serializers.Serializer):
    SECURITY_PASSWORD_MIN_LENGTH = serializers.IntegerField(
        min_value=6, max_value=30, required=True,
        label=_("Minimum length (User)")
    )
    SECURITY_ADMIN_USER_PASSWORD_MIN_LENGTH = serializers.IntegerField(
        min_value=6, max_value=30, required=True,
        label=_('Minimum length (Admin)')
    )
    SECURITY_PASSWORD_UPPER_CASE = serializers.BooleanField(
        required=False, label=_('Uppercase')
    )
    SECURITY_PASSWORD_LOWER_CASE = serializers.BooleanField(
        required=False, label=_('Lowercase')
    )
    SECURITY_PASSWORD_NUMBER = serializers.BooleanField(
        required=False, label=_('Digits')
    )
    SECURITY_PASSWORD_SPECIAL_CHAR = serializers.BooleanField(
        required=False, label=_('Special characters')
    )


login_ip_limit_time_help_text = _(
    'If the user has failed to log in for a limited number of times, '
    'no login is allowed during this time interval.'
)

ip_group_help_text = _(
    'With * indicating a match all. '
    'Such as: '
    '192.168.10.1, 192.168.1.0/24, 10.1.1.1-10.1.1.20, 2001:db8:2de::e13, 2001:db8:1a:1110::/64 '
)


def ip_group_child_validator(ip_group_child):
    is_valid = ip_group_child == '*' \
               or is_ip_address(ip_group_child) \
               or is_ip_network(ip_group_child) \
               or is_ip_segment(ip_group_child)
    if not is_valid:
        error = _('IP address invalid: `{}`').format(ip_group_child)
        raise serializers.ValidationError(error)


class SecurityLoginLimitSerializer(serializers.Serializer):
    SECURITY_CHECK_DIFFERENT_CITY_LOGIN = serializers.BooleanField(
        required=False, label=_('Suspicious Login Verification'),
        help_text=_(
            'The system determines whether the login IP address belongs to a common login city. '
            'If the account is logged in from a common login city, the system sends a remote login reminder'
        )
    )
    SECURITY_LOGIN_LIMIT_COUNT = serializers.IntegerField(
        min_value=3, max_value=99999,
        label=_('User login failures count')
    )
    SECURITY_LOGIN_LIMIT_TIME = serializers.IntegerField(
        min_value=5, max_value=99999, required=True,
        label=_('User login failure period (minute)'),
        help_text=login_ip_limit_time_help_text
    )

    SECURITY_LOGIN_IP_LIMIT_COUNT = serializers.IntegerField(
        min_value=3, max_value=99999,
        label=_('IP login failures count')
    )
    SECURITY_LOGIN_IP_LIMIT_TIME = serializers.IntegerField(
        min_value=5, max_value=99999, required=True,
        label=_('IP login failure period (minute)'),
        help_text=login_ip_limit_time_help_text
    )
    SECURITY_LOGIN_IP_WHITE_LIST = serializers.ListField(
        default=[], label=_('Login IP whitelist'), allow_empty=True,
        child=serializers.CharField(max_length=1024, validators=[ip_group_child_validator]),
        help_text=ip_group_help_text
    )
    SECURITY_LOGIN_IP_BLACK_LIST = serializers.ListField(
        default=[], label=_('Login IP blacklist'), allow_empty=True,
        child=serializers.CharField(max_length=1024, validators=[ip_group_child_validator]),
        help_text=ip_group_help_text
    )


class SecurityLoginAuthSerializer(serializers.Serializer):
    SECURITY_LOGIN_ACCESS_ENABLED = serializers.BooleanField(
        required=False, default=True, label=_("Login enabled"),
        help_text=_("Enable login for user")
    )

    SECURITY_LOGIN_CAPTCHA_ENABLED = serializers.BooleanField(
        required=False, default=True, label=_("Login captcha"),
        help_text=_("Enable captcha to prevent robot authentication")
    )

    SECURITY_LOGIN_ENCRYPTED_ENABLED = serializers.BooleanField(
        required=False, default=True, label=_("Login encrypted"),
        help_text=_("Enable encryption to prevent information leakage")
    )

    SECURITY_LOGIN_TEMP_TOKEN_ENABLED = serializers.BooleanField(
        required=False, default=True, label=_("Login temp token"),
        help_text=_("Enable temporary tokens to prevent attacks")
    )

    SECURITY_LOGIN_BY_EMAIL_ENABLED = serializers.BooleanField(
        required=False, default=True, label=_("Login by email"),
        help_text=_("Enable send email verify code to user")
    )

    SECURITY_LOGIN_BY_SMS_ENABLED = serializers.BooleanField(
        required=False, default=True, label=_("Login by sms"),
        help_text=_("Enable send sms verify code to user")
    )

    SECURITY_LOGIN_BY_BASIC_ENABLED = serializers.BooleanField(
        required=False, default=True, label=_("Login by basic"),
        help_text=_("Enable basic verify to user login")
    )


class SecurityRegisterAuthSerializer(serializers.Serializer):
    SECURITY_REGISTER_ACCESS_ENABLED = serializers.BooleanField(
        required=False, default=True, label=_("Register enable"),
        help_text=_("Enable register for user")
    )
    SECURITY_REGISTER_CAPTCHA_ENABLED = serializers.BooleanField(
        required=False, default=True, label=_("Register captcha"),
        help_text=_("Enable captcha to prevent robot register")
    )

    SECURITY_REGISTER_ENCRYPTED_ENABLED = serializers.BooleanField(
        required=False, default=True, label=_("Register encrypted"),
        help_text=_("Enable encryption to prevent information leakage")
    )

    SECURITY_REGISTER_TEMP_TOKEN_ENABLED = serializers.BooleanField(
        required=False, default=True, label=_("Register temp token"),
        help_text=_("Enable temporary tokens to prevent attacks")
    )

    SECURITY_REGISTER_BY_EMAIL_ENABLED = serializers.BooleanField(
        required=False, default=True, label=_("Register by email"),
        help_text=_("Enable send email verify code to user")
    )

    SECURITY_REGISTER_BY_SMS_ENABLED = serializers.BooleanField(
        required=False, default=True, label=_("Register by sms"),
        help_text=_("Enable send sms verify code to user")
    )

    SECURITY_REGISTER_BY_BASIC_ENABLED = serializers.BooleanField(
        required=False, default=True, label=_("Register by basic"),
        help_text=_("Enable basic verify to user register")
    )


class SecurityResetPasswordAuthSerializer(serializers.Serializer):
    SECURITY_RESET_PASSWORD_ACCESS_ENABLED = serializers.BooleanField(
        required=False, default=True, label=_("Reset password enable"),
        help_text=_("Enable reset password for user")
    )
    SECURITY_RESET_PASSWORD_CAPTCHA_ENABLED = serializers.BooleanField(
        required=False, default=True, label=_("Reset password captcha"),
        help_text=_("Enable captcha to prevent robot reset password")
    )

    SECURITY_RESET_PASSWORD_ENCRYPTED_ENABLED = serializers.BooleanField(
        required=False, default=True, label=_("Reset password encrypted"),
        help_text=_("Enable encryption to prevent information leakage")
    )

    SECURITY_RESET_PASSWORD_TEMP_TOKEN_ENABLED = serializers.BooleanField(
        required=False, default=True, label=_("Reset password temp token"),
        help_text=_("Enable temporary tokens to prevent attacks")
    )

    SECURITY_RESET_PASSWORD_BY_EMAIL_ENABLED = serializers.BooleanField(
        required=False, default=True, label=_("Reset password by email"),
        help_text=_("Enable send email verify code to user")
    )

    SECURITY_RESET_PASSWORD_BY_SMS_ENABLED = serializers.BooleanField(
        required=False, default=True, label=_("Reset password by sms"),
        help_text=_("Enable send sms verify code to user")
    )


class SecurityBindEmailAuthSerializer(serializers.Serializer):
    SECURITY_BIND_EMAIL_ACCESS_ENABLED = serializers.BooleanField(
        required=False, default=True, label=_("Bind email enable"),
        help_text=_("Enable bind email for user")
    )
    SECURITY_BIND_EMAIL_CAPTCHA_ENABLED = serializers.BooleanField(
        required=False, default=True, label=_("Bind email captcha"),
        help_text=_("Enable captcha to prevent robot reset password")
    )

    SECURITY_BIND_EMAIL_TEMP_TOKEN_ENABLED = serializers.BooleanField(
        required=False, default=True, label=_("Bind email encrypted"),
        help_text=_("Enable encryption to prevent information leakage")
    )

    SECURITY_BIND_EMAIL_ENCRYPTED_ENABLED = serializers.BooleanField(
        required=False, default=True, label=_("Bind email temp token"),
        help_text=_("Enable temporary tokens to prevent attacks")
    )


class SecurityBindPhoneAuthSerializer(serializers.Serializer):
    SECURITY_BIND_EMAIL_ACCESS_ENABLED = serializers.BooleanField(
        required=False, default=True, label=_("Bind phone enable"),
        help_text=_("Enable bind phone for user")
    )
    SECURITY_BIND_EMAIL_CAPTCHA_ENABLED = serializers.BooleanField(
        required=False, default=True, label=_("Bind phone captcha"),
        help_text=_("Enable captcha to prevent robot reset password")
    )

    SECURITY_BIND_EMAIL_TEMP_TOKEN_ENABLED = serializers.BooleanField(
        required=False, default=True, label=_("Bind phone encrypted"),
        help_text=_("Enable encryption to prevent information leakage")
    )

    SECURITY_BIND_EMAIL_ENCRYPTED_ENABLED = serializers.BooleanField(
        required=False, default=True, label=_("Bind phone temp token"),
        help_text=_("Enable temporary tokens to prevent attacks")
    )


class SecurityBlockIPSerializer(serializers.Serializer):
    pk = serializers.CharField(required=False, label=_("ID"))
    ip = serializers.CharField(max_length=1024, required=False, allow_blank=True, label=_("Block IP"))
    created_time = serializers.DateTimeField(label=_("Created time"))


class SecurityVerifyCodeSerializer(serializers.Serializer):
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


class SecurityCaptchaCodeSerializer(serializers.Serializer):
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
