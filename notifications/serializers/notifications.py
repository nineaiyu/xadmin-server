#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : notifications
# author : ly_13
# date : 9/13/2024
from rest_framework import serializers

from common.core.serializers import BaseModelSerializer
from notifications.models import SystemMsgSubscription, UserMsgSubscription


class SystemMsgSubscriptionSerializer(BaseModelSerializer):
    receive_backends = serializers.ListField(child=serializers.CharField())

    class Meta:
        model = SystemMsgSubscription
        fields = ['message_type', 'message_type_label', 'users', 'groups', 'receive_backends', 'receivers']
        read_only_fields = ['pk', 'message_type', 'message_type_label', 'receivers']
        extra_kwargs = {
            'users': {'allow_empty': True},
            'groups': {'allow_empty': True},
            'receive_backends': {'required': True}
        }

    def update(self, instance, validated_data):
        instance.set_message_type_label()
        return super().update(instance, validated_data)


class SystemMsgSubscriptionByCategorySerializer(serializers.Serializer):
    category = serializers.CharField()
    category_label = serializers.CharField()
    children = SystemMsgSubscriptionSerializer(many=True)


class UserMsgSubscriptionSerializer(BaseModelSerializer):
    receive_backends = serializers.ListField(child=serializers.CharField(), read_only=False)

    class Meta:
        model = UserMsgSubscription
        fields = ['user', 'receive_backends']
