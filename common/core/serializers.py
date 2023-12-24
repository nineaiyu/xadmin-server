#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : serializers
# author : ly_13
# date : 12/21/2023
from rest_framework.fields import empty
from rest_framework.request import Request
from rest_framework.serializers import ModelSerializer


class BaseModelSerializer(ModelSerializer):
    class Meta:
        model = None

    def __init__(self, instance=None, data=empty, request=None, **kwargs):
        super().__init__(instance, data, **kwargs)
        self.request: Request = request or self.context.get("request", None)

    def create(self, validated_data):
        if self.request:
            user = self.request.user
            if user and user.is_authenticated:
                if hasattr(self.Meta.model, 'creator') or hasattr(self.instance, 'creator'):
                    validated_data["creator_id"] = user.pk
                if hasattr(self.Meta.model, 'dept_belong') or hasattr(self.instance, 'dept_belong'):
                    validated_data["dept_belong_id"] = user.dept_id
        return super().create(validated_data)

    def update(self, instance, validated_data):
        if self.request:
            user = self.request.user
            if user and user.is_authenticated:
                if hasattr(self.instance, 'modifier'):
                    validated_data["modifier_id"] = user.pk
        return super().update(instance, validated_data)
