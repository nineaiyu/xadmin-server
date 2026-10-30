#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""已授权应用（OAuth 授权码 / 应用凭证）聚合序列化器。"""

from rest_framework import serializers


class AuthorizedApplicationSerializer(serializers.Serializer):
    application = serializers.DictField()
    users = serializers.ListField(child=serializers.DictField())
    user_count = serializers.IntegerField()
    scopes = serializers.ListField(child=serializers.CharField())
    authorized_time = serializers.DateTimeField(allow_null=True)
    last_used_time = serializers.DateTimeField(allow_null=True)
    access_token_count = serializers.IntegerField()
    refresh_token_count = serializers.IntegerField()
    status = serializers.CharField()
