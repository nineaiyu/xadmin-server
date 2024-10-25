#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : settings
# author : ly_13
# date : 10/25/2024
from common.core.serializers import BaseModelSerializer
from settings.models import Setting


class SettingSerializer(BaseModelSerializer):
    class Meta:
        model = Setting
        fields = ['pk', 'name', 'value', 'category', 'is_active', 'encrypted', 'created_time']
        read_only_fields = ['pk']
