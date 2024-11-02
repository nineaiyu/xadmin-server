#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : department
# author : ly_13
# date : 8/10/2024

from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers
from rest_framework.exceptions import ValidationError

from common.core.serializers import BaseModelSerializer
from common.utils import get_logger
from system.models import DeptInfo

logger = get_logger(__name__)


class DeptSerializer(BaseModelSerializer):
    class Meta:
        model = DeptInfo
        fields = [
            'pk', 'name', 'code', 'parent', 'rank', 'is_active', 'roles', 'user_count', 'rules', 'mode_type',
            'auto_bind', 'description', 'created_time'
        ]

        table_fields = [
            'name', 'pk', 'code', 'user_count', 'rank', 'mode_type', 'auto_bind', 'is_active', 'roles', 'rules',
            'created_time'
        ]

        extra_kwargs = {
            'roles': {'required': False, 'attrs': ['pk', 'name', 'code'], 'format': "{name}", 'many': True},
            'rules': {'required': False, 'attrs': ['pk', 'name', 'get_mode_type_display'], 'format': "{name}",
                      'many': True},
            'parent': {'required': False, 'attrs': ['pk', 'name', 'parent_id']},
        }

    user_count = serializers.SerializerMethodField(read_only=True, label=_("User count"))

    def validate(self, attrs):
        # 权限需要其他接口设置，下面三个参数忽略
        attrs.pop('rules', None)
        attrs.pop('roles', None)
        attrs.pop('mode_type', None)
        # 上级部门必须存在，否则会出现数据权限问题
        parent = attrs.get('parent', self.instance.parent if self.instance else None)
        if not parent:
            attrs['parent'] = self.request.user.dept
        return attrs

    def update(self, instance, validated_data):
        parent = validated_data.get('parent')
        if parent and str(parent.pk) in DeptInfo.recursion_dept_info(dept_id=instance.pk):
            raise ValidationError(_("The superior department cannot be its own subordinate department"))
        return super().update(instance, validated_data)

    @extend_schema_field(serializers.IntegerField)
    def get_user_count(self, obj):
        return obj.userinfo_set.count()
