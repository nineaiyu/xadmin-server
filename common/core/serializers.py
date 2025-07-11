#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : serializers
# author : ly_13
# date : 12/21/2023
from inspect import isfunction
from typing import List

from django.conf import settings
from django.db.models import QuerySet
from django.db.models.fields import NOT_PROVIDED
from rest_framework.fields import empty
from rest_framework.request import Request
from rest_framework.serializers import ModelSerializer

from common.core.fields import BasePrimaryKeyRelatedField, LabeledChoiceField
from server.utils import get_current_request


class BaseModelSerializer(ModelSerializer):
    serializer_related_field = BasePrimaryKeyRelatedField
    serializer_choice_field = LabeledChoiceField
    ignore_field_permission = False  # 忽略字段权限

    class Meta:
        model = None
        table_fields = []  # 用于控制前端table的字段展示
        tabs = []

    def get_value(self, dictionary):
        # We override the default field access in order to support
        # nested HTML forms.
        # 下面两行注释是因为已经在前面处理过form-data，这里无需再次处理
        # if html.is_html_input(dictionary):
        #     return html.parse_html_dict(dictionary, prefix=self.field_name) or empty
        return dictionary.get(self.field_name, empty)

    def get_allow_fields(self, fields, ignore_field_permission):
        """
        self.fields: 默认定义的字段
        fields: 需要展示的字段
        allow_fields: 字段权限允许的字段
        """
        _fields = set(self.fields)
        if fields is None:
            fields = _fields

        if self.ignore_field_permission or ignore_field_permission or (
                self.request and hasattr(self.request, "ignore_field_permission")):
            return set(fields) & _fields

        allow_fields = []
        # 获取权限字段，如果没有配置，则为定义的所有字段
        if self.request and settings.PERMISSION_FIELD_ENABLED and not self.ignore_field_permission:
            if hasattr(self.request, "user") and self.request.user and self.request.user.is_superuser:
                allow_fields = _fields
            elif hasattr(self.request, "fields"):
                if self.request.fields and isinstance(self.request.fields, dict):
                    allow_fields = self.request.fields.get(self.Meta.model._meta.label_lower, [])
        else:
            allow_fields = _fields

        return set(fields) & _fields & set(allow_fields)

    def __init__(self, instance=None, data=empty, fields=None, ignore_field_permission=False, **kwargs):
        """
        :param instance:
        :param data:
        :param request: Request 对象
        :param fields: 序列化展示的字段， 默认定义的全部字段
        :param ignore_field_permission: 忽略字段权限控制
        """
        super().__init__(instance, data, **kwargs)
        meta = getattr(self, 'Meta', None)
        if meta and hasattr(meta, 'tabs') and meta.fields != '__all__':
            meta.fields = meta.fields + self.get_fields_from_tabs(meta.tabs)

        self.request: Request = get_current_request()
        if self.request is None:
            return
        allowed = self.get_allow_fields(fields, ignore_field_permission)
        for field_name in set(self.fields) - allowed:
            self.fields.pop(field_name)

    @staticmethod
    def get_fields_from_tabs(tabs: List) -> List[str]:
        seen = set()
        result = []
        for tab in tabs:
            for field in tab.fields:
                if field not in seen:
                    seen.add(field)
                    result.append(field)
        return result

    def build_standard_field(self, field_name, model_field):
        field_class, field_kwargs = super().build_standard_field(field_name, model_field)
        default = getattr(model_field, 'default', NOT_PROVIDED)
        if default != NOT_PROVIDED:
            # 将model中的默认值同步到序列化中
            if isfunction(default):
                default = default()
            field_kwargs.setdefault("default", default)
        return field_class, field_kwargs

    def create(self, validated_data):
        n_file_objs = []
        for field in self.Meta.model._meta.get_fields():
            if field.is_relation and field.related_model._meta.label == "system.UploadFile":
                if field.name in validated_data:
                    file_data = validated_data[field.name]
                    if isinstance(file_data, (list, QuerySet)):
                        n_file_objs.extend(validated_data.get(field.name))
                    else:
                        n_file_objs.append(validated_data.get(field.name))

        result = super().create(validated_data)

        for n_file in n_file_objs:
            setattr(n_file, 'is_tmp', False)
            n_file.save(update_fields=['is_tmp'])
        return result

    def update(self, instance, validated_data):
        n_file_objs = []
        d_file_objs = []
        for field in self.Meta.model._meta.get_fields():
            if field.is_relation and field.related_model._meta.label == "system.UploadFile":
                if field.name in validated_data:
                    file_data = validated_data[field.name]
                    if isinstance(file_data, (list, QuerySet)):
                        d_file_objs.extend(
                            set(getattr(instance, field.name).all()) - set(validated_data.get(field.name)))
                        n_file_objs.extend(
                            set(validated_data.get(field.name)) - set(getattr(instance, field.name).all()))
                    else:
                        o_file_obj = getattr(instance, field.name)
                        n_file_obj = validated_data.get(field.name)
                        if o_file_obj.pk != n_file_obj.pk:
                            d_file_objs.append(o_file_obj)
                            n_file_objs.append(n_file_obj)

        result = super().update(instance, validated_data)

        for d_file in d_file_objs:
            d_file.delete()
        for n_file in n_file_objs:
            setattr(n_file, 'is_tmp', False)
            n_file.save(update_fields=['is_tmp'])
        return result


class TabsColumn(object):

    def __init__(self, label: str, fields: List[str]):
        self.label = label
        self.fields = fields

    def __str__(self):
        return {'label': self.label, 'fields': self.fields}
