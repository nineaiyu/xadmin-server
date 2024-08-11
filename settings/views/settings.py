#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : settings
# author : ly_13
# date : 7/31/2024

import logging

from django.conf import settings

from common.core.modelset import NoDetailModelSet
from settings.models import Setting
from settings.serializers.basic import BasicSettingSerializer

logger = logging.getLogger(__name__)


class BaseSettingView(NoDetailModelSet):
    queryset = Setting.objects.all()
    serializer_class = BasicSettingSerializer
    category = "basic"
    serializer_class_mapper = {}

    def get_serializer_class(self):
        if not self.serializer_class_mapper:
            return super().get_serializer_class()
        self.category = self.request.query_params.get('category', 'basic')
        cls = self.serializer_class_mapper.get(self.category, self.serializer_class)
        return cls

    def get_fields(self):
        serializer = self.get_serializer_class()()
        fields = serializer.get_fields()
        return fields

    def get_object(self):
        items = self.get_fields().keys()
        obj = {}
        for item in items:
            if hasattr(settings, item):
                obj[item] = getattr(settings, item)
            else:
                obj[item] = None
        return obj

    def parse_serializer_data(self, serializer):
        data = []
        fields = self.get_fields()
        encrypted_items = [name for name, field in fields.items() if field.write_only]
        for name, value in serializer.validated_data.items():
            encrypted = name in encrypted_items
            if encrypted and value in ['', None]:
                continue
            data.append({
                'name': name, 'value': value,
                'encrypted': encrypted, 'category': self.category
            })
        return data

    def perform_update(self, serializer):
        post_data_names = list(self.request.data.keys())
        settings_items = self.parse_serializer_data(serializer)
        serializer_data = getattr(serializer, 'data', {})

        for item in settings_items:
            if item['name'] not in post_data_names:
                continue
            changed, setting = Setting.update_or_create(**item, user=self.request.user)
            if not changed:
                continue
            serializer_data[setting.name] = setting.cleaned_value
        setattr(serializer, '_data', serializer_data)
        if hasattr(serializer, 'post_save'):
            serializer.post_save()
