#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : notifications
# author : ly_13
# date : 9/13/2024
from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from common.core.fields import BasePrimaryKeyRelatedField
from common.core.serializers import BaseModelSerializer
from notifications.models import SystemMsgSubscription, UserMsgSubscription


class SystemMsgSubscriptionSerializer(BaseModelSerializer):
    ignore_field_permission = True
    receive_backends = serializers.ListField(child=serializers.CharField())
    message_type_label = serializers.CharField(read_only=True)
    receivers = BasePrimaryKeyRelatedField(attrs=['pk', 'username', 'nickname'], read_only=True, source='users',
                                           label=_("User"), many=True, format='{nickname}({username})')

    class Meta:
        model = SystemMsgSubscription
        fields = ['message_type', 'message_type_label', 'users', 'receive_backends', 'receivers']
        read_only_fields = ['pk', 'message_type', 'message_type_label', 'receivers']
        extra_kwargs = {'users': {'allow_empty': True}, 'receive_backends': {'required': True}}


class SystemMsgSubscriptionByCategorySerializer(serializers.Serializer):
    category = serializers.CharField()
    category_label = serializers.CharField()
    children = SystemMsgSubscriptionSerializer(many=True)


class UserMsgSubscriptionSerializer(BaseModelSerializer):
    ignore_field_permission = True
    receive_backends = serializers.ListField(child=serializers.CharField())
    message_type_label = serializers.CharField(read_only=True, label=_("Message Type"))
    receivers = BasePrimaryKeyRelatedField(attrs=['pk', 'username', 'nickname'], read_only=True, label=_("User"),
                                           source='user', format='{nickname}({username})')

    class Meta:
        model = UserMsgSubscription
        fields = ['message_type', 'message_type_label', 'user', 'receive_backends', 'receivers']
        read_only_fields = ['pk', 'message_type', 'message_type_label', 'receivers']
        extra_kwargs = {'user': {'read_only': True}, 'receive_backends': {'required': True}}


class UserMsgSubscriptionByCategorySerializer(serializers.Serializer):
    category = serializers.CharField()
    category_label = serializers.CharField()
    children = UserMsgSubscriptionSerializer(many=True)
