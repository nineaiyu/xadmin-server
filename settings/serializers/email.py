#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : email
# author : ly_13
# date : 8/1/2024
from django.utils.translation import gettext_lazy as _
from rest_framework import serializers


class EmailSettingSerializer(serializers.Serializer):
    EMAIL_ENABLED = serializers.BooleanField(
        default=False, label=_('Email'), help_text=_('Enable Email Service (Email)')
    )
    EMAIL_HOST = serializers.CharField(max_length=1024, required=True, label=_("Host"))
    EMAIL_PORT = serializers.CharField(max_length=5, required=True, label=_("Port"))
    EMAIL_HOST_USER = serializers.CharField(
        max_length=128, required=True, label=_("Account"),
        help_text=_("The user to be used for email server authentication")
    )
    EMAIL_HOST_PASSWORD = serializers.CharField(
        max_length=1024, required=False, label=_("Password"), write_only=True,
        help_text=_(
            "Password to use for the email server. It is used in conjunction with `User` when authenticating to the email server")
    )
    EMAIL_FROM = serializers.CharField(
        max_length=128, allow_blank=True, required=False, label=_('Sender'),
        help_text=_('Sender email address (default to using the `User`)')
    )

    EMAIL_SUBJECT_PREFIX = serializers.CharField(
        max_length=128, allow_blank=True, required=False, label=_('Subject prefix'),
        help_text=_("The subject line prefix of the sent email")
    )
    EMAIL_USE_SSL = serializers.BooleanField(
        required=False, label=_('Use SSL'),
        help_text=_(
            'Whether to use an implicit TLS (secure) connection when talking to the SMTP server. In most email documentation this type of TLS connection is referred to as SSL. It is generally used on port 465')
    )
    EMAIL_USE_TLS = serializers.BooleanField(
        required=False, label=_("Use TLS"),
        help_text=_(
            'Whether to use a TLS (secure) connection when talking to the SMTP server. This is used for explicit TLS connections, generally on port 587')
    )

    EMAIL_RECIPIENT = serializers.EmailField(
        max_length=128, allow_blank=True, required=False, label=_('Recipient'),
        help_text=_("The recipient is used for testing the email server's connectivity")
    )
