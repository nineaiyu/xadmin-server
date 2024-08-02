#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : security
# author : ly_13
# date : 8/1/2024

from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

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


class SecurityBlockIPSerializer(serializers.Serializer):
    id = serializers.UUIDField(required=False)
    ip = serializers.CharField(max_length=1024, required=False, allow_blank=True)
