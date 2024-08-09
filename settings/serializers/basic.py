#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : basic
# author : ly_13
# date : 8/1/2024
from django.utils.translation import gettext_lazy as _
from rest_framework import serializers


class BasicSettingSerializer(serializers.Serializer):
    SITE_URL = serializers.URLField(
        required=True, label=_("Site URL"),
        help_text=_(
            'Site URL is the externally accessible address of the current product '
            'service and is usually used in links in system emails'
        )
    )

    @staticmethod
    def validate_SITE_URL(s):
        if not s:
            return 'http://127.0.0.1'
        return s.strip('/')
