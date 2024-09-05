#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : fields
# author : ly_13
# date : 8/6/2024
from functools import partial

import phonenumbers
from django.core.exceptions import ObjectDoesNotExist
from django.db.models import Model
from django.utils.translation import gettext_lazy as _
from rest_framework import serializers
from rest_framework.fields import ChoiceField
from rest_framework.request import Request
from rest_framework.serializers import RelatedField, MultipleChoiceField

from common.core.filter import get_filter_queryset


def attr_get(obj, attr, sp='.'):
    names = attr.split(sp)

    def func(obj):
        for name in names:
            obj = getattr(obj, name)
        return obj

    return func(obj)


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


class LabeledMultipleChoiceField(MultipleChoiceField):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.choice_mapper = {
            key: value for key, value in self.choices.items()
        }

    def to_representation(self, keys):
        if keys is None:
            return keys
        return [
            {"value": key, "label": self.choice_mapper.get(key)}
            for key in keys
        ]

    def to_internal_value(self, data):
        if not data:
            return data

        if isinstance(data[0], dict):
            return [item.get("value") for item in data]
        else:
            return data


class BasePrimaryKeyRelatedField(RelatedField):
    default_error_messages = {
        "required": _("This field is required."),
        "does_not_exist": _('Invalid pk "{pk_value}" - object does not exist.'),
        "incorrect_type": _("Incorrect type. Expected pk value, received {data_type}."),
    }

    def __init__(self, **kwargs):
        self.attrs = kwargs.pop("attrs", [])
        self.label_format = kwargs.pop("format", None)
        self.input_type = kwargs.pop("input_type", '')
        self.many = kwargs.get("many", False)
        super().__init__(**kwargs)
        self.request: Request = self.context.get("request", None)

    def use_pk_only_optimization(self):
        return False

    def get_queryset(self):
        request = self.context.get("request", None)
        if request and request.user and request.user.is_authenticated:
            return get_filter_queryset(super().get_queryset(), request.user)
        return super().get_queryset()

    def display_value(self, instance):
        # 用于自定义的choices中value的展示，默认是 str(instance) ，可以通过在model中重写__str__方法，也可以在此方法定义
        return super().display_value(instance)

    def get_choices(self, cutoff=None):
        # 用于获取可选
        is_column = getattr(self, 'is_column', False)
        queryset = self.get_queryset()
        if queryset is None:
            # Ensure that field.choices returns something sensible
            # even when accessed with a read-only field.
            return [] if is_column else {}

        if cutoff is not None:
            queryset = queryset[:cutoff]

        if is_column:
            result = []
            for item in queryset:
                data = self.to_representation(item)
                data['value'] = data.get("pk")
                result.append(data)
        else:
            result = {}
            for item in queryset:
                key = self.to_representation(item)
                if isinstance(key, dict):
                    key = key.get("pk")
                result[key] = self.display_value(item)
        return result

    def to_representation(self, value):
        if not self.attrs:
            return value.pk
        data = {}
        for attr in self.attrs:
            # if not hasattr(value, attr):
            #     continue
            # data[attr] = getattr(value, attr)
            try:
                data[attr] = attr_get(value, attr, '__')
            except:
                continue
            if isinstance(data[attr], partial):
                data[attr] = data[attr]()
        if data:
            if self.label_format:
                data["label"] = self.label_format.format(**data)
            else:
                if "label" not in self.attrs:
                    data["label"] = data.get("pk")
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


class PhoneField(serializers.CharField):

    def __init__(self, **kwargs):
        self.input_type = 'phone'
        super().__init__(**kwargs)

    def to_internal_value(self, data):
        if isinstance(data, dict):
            code = data.get('code')
            phone = data.get('phone', '')
            if code and phone:
                code = code.replace('+', '')
                data = '+{}{}'.format(code, phone)
            else:
                data = phone
        if data:
            try:
                phone = phonenumbers.parse(data, 'CN')
                data = '+{}{}'.format(phone.country_code, phone.national_number)
            except phonenumbers.NumberParseException:
                data = '+86{}'.format(data)

        return super().to_internal_value(data)

    def to_representation(self, value):
        try:
            phone = phonenumbers.parse(value, 'CN')
            value = {'code': '+%s' % phone.country_code, 'phone': phone.national_number}
        except phonenumbers.NumberParseException:
            value = {'code': '+86', 'phone': value}
        return value


class ColorField(serializers.CharField):

    def __init__(self, **kwargs):
        self.input_type = 'color'
        super().__init__(**kwargs)
