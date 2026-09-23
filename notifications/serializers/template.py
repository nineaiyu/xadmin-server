#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""通知消息模板序列化器（配置型端点：save / reset 走显式 action）。"""

from rest_framework import serializers


class MessageTemplateWriteSerializer(serializers.Serializer):
    """模板覆盖写入（save / preview 共用）。"""

    message_type = serializers.CharField(max_length=128)
    subject_template = serializers.CharField(allow_blank=True, required=False, max_length=2000)
    body_template = serializers.CharField(allow_blank=True, required=False, max_length=20000)
    is_active = serializers.BooleanField(required=False)
    remark = serializers.CharField(allow_blank=True, required=False, max_length=255)


class MessageTemplateResetSerializer(serializers.Serializer):
    message_type = serializers.CharField(max_length=128)
