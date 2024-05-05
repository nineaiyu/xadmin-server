#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : serializers
# author : ly_13
# date : 12/21/2023
from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from django.db.models import Model
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from rest_framework.fields import empty, ChoiceField
from rest_framework.relations import PrimaryKeyRelatedField
from rest_framework.request import Request
from rest_framework.serializers import ModelSerializer, RelatedField

from common.core.config import SysConfig
from common.core.filter import get_filter_queryset
from system.models import ModelLabelField


class LabeledChoiceField(ChoiceField):
    def to_representation(self, key):
        if key is None:
            return key
        label = self.choices.get(key, key)
        return {"value": key, "label": label}

    def to_internal_value(self, data):
        if isinstance(data, dict):
            data = data.get("value")
        if isinstance(data, str) and "(" in data and data.endswith(")"):
            data = data.strip(")").split('(')[-1]
        return super(LabeledChoiceField, self).to_internal_value(data)


class ObjectRelatedField(RelatedField):
    default_error_messages = {
        "required": _("This field is required."),
        "does_not_exist": _('Invalid pk "{pk_value}" - object does not exist.'),
        "incorrect_type": _("Incorrect type. Expected pk value, received {data_type}."),
    }

    def __init__(self, **kwargs):
        self.attrs = kwargs.pop("attrs", None) or ("id", "name")
        self.many = kwargs.get("many", False)
        super().__init__(**kwargs)

    def to_representation(self, value):
        data = {}
        for attr in self.attrs:
            if not hasattr(value, attr):
                continue
            data[attr] = getattr(value, attr)
        return data

    def to_internal_value(self, data):
        queryset = self.get_queryset()
        if isinstance(data, Model):
            return queryset.get(pk=data.pk)

        if not isinstance(data, dict):
            pk = data
        else:
            pk = data.get("id") or data.get("pk") or data.get(self.attrs[0])

        try:
            if isinstance(data, bool):
                raise TypeError
            return queryset.get(pk=pk)
        except ObjectDoesNotExist:
            self.fail("does_not_exist", pk_value=pk)
        except (TypeError, ValueError):
            self.fail("incorrect_type", data_type=type(pk).__name__)


class BasePrimaryKeyRelatedField(PrimaryKeyRelatedField):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.request: Request = self.context.get("request", None)

    def get_queryset(self):
        request = self.context.get("request", None)
        if request and request.user and request.user.is_authenticated:
            return get_filter_queryset(super().get_queryset(), request.user)
        return super().get_queryset()


class BaseModelSerializer(ModelSerializer):
    serializer_related_field = BasePrimaryKeyRelatedField
    ignore_field_permission = False  # 忽略字段权限

    class Meta:
        model = None

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
        delete = True
        obj, _ = ModelLabelField.objects.update_or_create(name=model._meta.label_lower, field_type=field_type,
                                                          parent=None, defaults={'label': model._meta.verbose_name})
        for name, field in instance.fields.items():
            ModelLabelField.objects.update_or_create(name=name, parent=obj, field_type=field_type,
                                                     defaults={'label': field.label})

    if delete:
        ModelLabelField.objects.filter(field_type=field_type, updated_time__lt=now).delete()
