#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : basic
# author : ly_13
# date : 8/1/2024
from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from system.signal import invalid_user_cache_signal


class BasicSettingSerializer(serializers.Serializer):
    SITE_URL = serializers.URLField(
        required=False, label=_("Site URL"),
        help_text=_(
            'Site URL is the externally accessible address of the current product '
            'service and is usually used in links in system emails'
        )
    )

    FRONT_END_WEB_WATERMARK_ENABLED = serializers.BooleanField(
        required=False, default=True, label=_("Front-end web watermark"),
        help_text=_("Enable watermark for front-end web")
    )

    PERMISSION_FIELD_ENABLED = serializers.BooleanField(
        required=False, default=True, label=_("Field permission"),
        help_text=_("Field permissions are used to authorize access to data field display")
    )

    PERMISSION_DATA_ENABLED = serializers.BooleanField(
        required=False, default=True, label=_("Data permission"),
        help_text=_("Data permissions are used to authorize access to data")
    )

    EXPORT_MAX_LIMIT = serializers.IntegerField(
        required=False, label=_('Export max limit'),
        help_text=_("Limit the maximum number of rows of exported data")
    )

    @staticmethod
    def validate_SITE_URL(s):
        if not s:
            return 'http://127.0.0.1'
        return s.strip('/')

    def post_save(self):
        if set(getattr(self, '_change_fields', [])) & {'PERMISSION_FIELD_ENABLED', 'PERMISSION_DATA_ENABLED'}:
            invalid_user_cache_signal.send(sender=self, user_pk='*')
