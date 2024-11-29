#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : role
# author : ly_13
# date : 8/10/2024

from django.db import transaction
from django.utils.translation import gettext_lazy as _
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from common.core.serializers import BaseModelSerializer
from common.utils import get_logger
from system.models import FieldPermission, UserRole

logger = get_logger(__name__)


class FieldPermissionSerializer(BaseModelSerializer):
    class Meta:
        model = FieldPermission
        fields = ['pk', 'role', 'menu', 'field']
        read_only_fields = ['pk']


class RoleSerializer(BaseModelSerializer):
    class Meta:
        model = UserRole
        fields = ['pk', 'name', 'code', 'is_active', 'description', 'menu', 'updated_time', 'field', 'fields']
        table_fields = ['pk', 'name', 'code', 'is_active', 'description', 'updated_time']
        read_only_fields = ['pk']
        extra_kwargs = {
            'menu': {
                'attrs': ['pk', 'name'], 'many': True, 'input_type': "input"
            }
        }

    # 上面写的 extra_kwargs['menu'] 和下面下结果一样，但是上面写法少写了 label 和 queryset
    # menu = BasePrimaryKeyRelatedField(queryset=Menu.objects, many=True, label=_("Menu"), attrs=['pk', 'name'],
    #                                   input_type="input")

    # field和fields 设置两个相同的label，可以进行文件导入导出
    field = serializers.SerializerMethodField(read_only=True, label=_("Fields"))
    fields = serializers.DictField(write_only=True, label=_("Fields"))

    @extend_schema_field(OpenApiTypes.OBJECT)
    def get_field(self, obj):
        results = FieldPermissionSerializer(FieldPermission.objects.filter(role=obj), many=True,
                                            ignore_field_permission=True).data
        data = {}
        for res in results:
            data[str(res.get('menu'))] = res.get('field', [])
        return data

    def save_fields(self, fields, instance):
        for k, v in fields.items():
            serializer = FieldPermissionSerializer(data={'role': instance.pk, 'menu': k, 'field': v},
                                                   ignore_field_permission=True)
            serializer.is_valid(raise_exception=True)
            serializer.save()

    def update(self, instance, validated_data):
        fields = validated_data.pop('fields', None)
        with transaction.atomic():
            instance = super().update(instance, validated_data)
            if fields:
                FieldPermission.objects.filter(role=instance).delete()
                self.save_fields(fields, instance)
        return instance

    def create(self, validated_data):
        fields = validated_data.pop('fields')
        with transaction.atomic():
            instance = super().create(validated_data)
            self.save_fields(fields, instance)
        return instance


class ListRoleSerializer(RoleSerializer):
    class Meta:
        model = UserRole
        fields = ['pk', 'name', 'is_active', 'code', 'menu', 'description', 'updated_time', 'field', 'fields']
        read_only_fields = [x.name for x in UserRole._meta.fields]

    field = serializers.ListField(default=[], read_only=True)
    menu = serializers.SerializerMethodField(read_only=True)

    @extend_schema_field(serializers.ListField)
    def get_menu(self, instance):
        return []
