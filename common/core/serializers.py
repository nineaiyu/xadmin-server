#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : serializers
# author : ly_13
# date : 12/21/2023
from inspect import isfunction

from django.conf import settings
from django.db import transaction
from django.db.models.fields import NOT_PROVIDED
from django.utils import timezone
from django.utils.translation import activate
from rest_framework.fields import empty
from rest_framework.request import Request
from rest_framework.serializers import ModelSerializer

from common.core.config import SysConfig
from common.core.fields import BasePrimaryKeyRelatedField
from common.core.utils import PrintLogFormat
from system.models import ModelLabelField


class BaseModelSerializer(ModelSerializer):
    serializer_related_field = BasePrimaryKeyRelatedField
    ignore_field_permission = False  # 忽略字段权限

    class Meta:
        model = None
        table_fields = []  # 用于控制前端table的字段展示

    def get_value(self, dictionary):
        # We override the default field access in order to support
        # nested HTML forms.
        # 下面两行注释是因为已经在前面处理过form-data，这里无需再次处理
        # if html.is_html_input(dictionary):
        #     return html.parse_html_dict(dictionary, prefix=self.field_name) or empty
        return dictionary.get(self.field_name, empty)

    def get_uniqueness_extra_kwargs(self, field_names, declared_fields, extra_kwargs):
        """
        # 该方法是为了让继承BaseModelSerializer的方法，增加request传参,例如下面，为meta这个字段的序列化增加request参数
        class MenuSerializer(BaseModelSerializer):
            meta = MenuMetaSerializer()
        """
        for field_name in declared_fields:
            if declared_fields[field_name] and isinstance(declared_fields[field_name], BaseModelSerializer):
                obj = declared_fields[field_name]
                declared_fields[field_name] = obj.__class__(*obj._args, **obj._kwargs, request=self.request)

        extra_kwargs, hidden_fields = super().get_uniqueness_extra_kwargs(field_names, declared_fields, extra_kwargs)
        return super().get_uniqueness_extra_kwargs(field_names, declared_fields, extra_kwargs)

    def __init__(self, instance=None, data=empty, request=None, fields=None, all_fields=False, **kwargs):
        super().__init__(instance, data, **kwargs)
        self.request: Request = request or self.context.get("request", None)
        if all_fields:
            return
        if not fields and (self.request is None or getattr(self.request, 'all_fields', None) is not None):
            return
        allowed = set()
        allowed2 = allowed1 = None
        if fields is not None:
            allowed1 = set(fields)
        if self.request and SysConfig.PERMISSION_FIELD and not self.ignore_field_permission:
            if hasattr(self.request, "fields"):
                if self.request.fields and isinstance(self.request.fields, dict):
                    allowed2 = set(self.request.fields.get(self.Meta.model._meta.label_lower, []))

            if hasattr(self.request, "user") and self.request.user and self.request.user.is_superuser:
                allowed2 = set(self.fields)
        else:
            allowed2 = set(self.fields)

        if self.request and hasattr(self.request, "all_fields"):
            allowed2 = set(self.fields)

        if allowed2 is not None and allowed1 is not None:
            allowed = allowed1 & allowed2

        if allowed2 and allowed1 is None:
            allowed = allowed2

        if allowed1 and allowed2 is None:
            allowed = allowed1

        existing = set(self.fields)
        for field_name in existing - allowed:
            self.fields.pop(field_name)

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
        if self.request:
            user = self.request.user
            if user and user.is_authenticated:
                if hasattr(self.Meta.model, 'creator') or hasattr(self.instance, 'creator'):
                    validated_data["creator"] = user
                if hasattr(self.Meta.model, 'dept_belong') or hasattr(self.instance, 'dept_belong'):
                    validated_data["dept_belong"] = user.dept
        return super().create(validated_data)

    def update(self, instance, validated_data):
        if self.request:
            user = self.request.user
            if user and user.is_authenticated:
                if hasattr(self.instance, 'modifier'):
                    validated_data["modifier"] = user
        return super().update(instance, validated_data)


@transaction.atomic
def get_sub_serializer_fields():
    cls_list = []
    activate(settings.LANGUAGE_CODE)

    def get_all_subclass(base_cls):
        if base_cls.__subclasses__():
            for cls in base_cls.__subclasses__():
                cls_list.append(cls)
                get_all_subclass(cls)

    get_all_subclass(BaseModelSerializer)

    delete = False
    now = timezone.now()
    field_type = ModelLabelField.FieldChoices.ROLE
    for cls in cls_list:
        instance = cls(all_fields=True)
        model = instance.Meta.model
        if not model:
            continue
        count = [0, 0]

        delete = True
        obj, created = ModelLabelField.objects.update_or_create(name=model._meta.label_lower, field_type=field_type,
                                                                parent=None,
                                                                defaults={'label': model._meta.verbose_name})
        count[int(not created)] += 1
        for name, field in instance.fields.items():
            _, created = ModelLabelField.objects.update_or_create(name=name, parent=obj, field_type=field_type,
                                                                  defaults={'label': field.label})
            count[int(not created)] += 1
        PrintLogFormat(f"Model:({model._meta.label_lower})").warning(
            f"update_or_create role permission, created:{count[0]} updated:{count[1]}")

    if delete:
        deleted, _rows_count = ModelLabelField.objects.filter(field_type=field_type, updated_time__lt=now).delete()
        PrintLogFormat(f"Sync Role permission end").info(f"deleted success, deleted:{deleted} row_count {_rows_count}")
