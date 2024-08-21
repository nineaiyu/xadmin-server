#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : department
# author : ly_13
# date : 8/10/2024
import logging

from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers
from rest_framework.exceptions import ValidationError

from common.core.fields import BasePrimaryKeyRelatedField
from system.models import DeptInfo
from system.serializers.base import BaseRoleRuleInfo

logger = logging.getLogger(__name__)


class DeptSerializer(BaseRoleRuleInfo):
    class Meta:
        model = DeptInfo
        fields = ['pk', 'name', 'code', 'parent', 'rank', 'is_active', 'roles', 'user_count', 'rules',
                  'mode_type', 'auto_bind', 'description', 'created_time']

        table_fields = ['name', 'pk', 'code', 'user_count', 'rank', 'mode_type', 'auto_bind', 'is_active', 'roles',
                        'rules', 'created_time']

        extra_kwargs = {'roles': {'read_only': True}, 'rules': {'read_only': True}}

    user_count = serializers.SerializerMethodField(read_only=True, label=_("User count"))
    parent = BasePrimaryKeyRelatedField(queryset=DeptInfo.objects, allow_null=True, required=False,
                                        label=_("Superior department"), attrs=['pk', 'name', 'parent_id'])

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
        if parent and parent.pk in DeptInfo.recursion_dept_info(dept_id=instance.pk):
            raise ValidationError(_("The superior department cannot be its own subordinate department"))
        return super().update(instance, validated_data)

    @extend_schema_field(serializers.IntegerField)
    def get_user_count(self, obj):
        return obj.userinfo_set.count()
