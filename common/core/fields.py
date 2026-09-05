#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : fields
# author : ly_13
# date : 8/6/2024
from functools import partial

import phonenumbers
from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.db.models import Model
from django.db.models.fields.files import FieldFile
from django.utils.translation import gettext_lazy as _
from rest_framework import serializers
from rest_framework.request import Request

from common.core.filter import get_filter_queryset
from common.fields.utils import get_file_absolute_uri
from server.utils import get_current_request


def attr_get(obj, attr, sp='.'):
    names = attr.split(sp)

    def func(obj):
        for name in names:
            obj = getattr(obj, name)
        return obj

    return func(obj)


_CHOICES_MAX_CACHE = {"value": None, "expires": 0.0}


def get_search_choices_max_count(default=200, ttl=60):
    """PERF-07：读取关联列 choices 的行数上限（系统配置 SEARCH_CHOICES_MAX_COUNT）。

    局部导入避免潜在的循环依赖；配置读取失败时退回默认值，绝不让下拉数据影响主流程。
    search-columns / search-fields 会对每个关联字段调用一次，这里做进程内短 TTL 缓存，
    避免把一次 Redis/DB 读放大成 N 次；配置变更最迟 ttl 秒后生效。
    """
    import time

    now = time.monotonic()
    cached = _CHOICES_MAX_CACHE["value"]
    if cached is not None and _CHOICES_MAX_CACHE["expires"] > now:
        return cached
    try:
        from common.core.config import SysConfig

        value = int(SysConfig.SEARCH_CHOICES_MAX_COUNT)
        if value <= 0:
            return default
        _CHOICES_MAX_CACHE["value"] = value
        _CHOICES_MAX_CACHE["expires"] = now + ttl
        return value
    except Exception:
        return default  # 读取异常不缓存，下一次请求自动重试


class LabeledChoiceField(serializers.ChoiceField):
    def __init__(self, **kwargs):
        self.attrs = kwargs.pop("attrs", None) or ("value", "label")
        super().__init__(**kwargs)

    def to_representation(self, key):
        if key is None:
            return key
        label = self.choices.get(key, key)
        return {"value": key, "label": label}

    def to_internal_value(self, data):
        if not data:
            return data
        if isinstance(data, dict):
            data = data.get("value")
        if isinstance(data, str) and "(" in data and data.endswith(")"):
            data = data.strip(")").split('(')[-1]
        return super(LabeledChoiceField, self).to_internal_value(data)

    def get_schema(self):
        """
        为 drf-spectacular 提供 OpenAPI schema
        """
        if getattr(self, 'many', False):
            return {
                'type': 'array',
                'items': {
                    'type': 'object',
                    'properties': {
                        'value': {'type': 'string'},
                        'label': {'type': 'string'}
                    }
                },
                'description': getattr(self, 'help_text', ''),
                'title': getattr(self, 'label', ''),
            }
        else:
            return {
                'type': 'object',
                'properties': {
                    'value': {'type': 'string'},
                    'label': {'type': 'string'}
                },
                'description': getattr(self, 'help_text', ''),
                'title': getattr(self, 'label', ''),
            }


class LabeledMultipleChoiceField(serializers.MultipleChoiceField):
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


class BasePrimaryKeyRelatedField(serializers.RelatedField):
    """
    Base class for primary key related fields.
    """
    default_error_messages = {
        "required": _("This field is required."),
        "does_not_exist": _('Invalid pk "{pk_value}" - object does not exist.'),
        "incorrect_type": _("Incorrect type. Expected pk value, received {data_type}."),
        "queryset_none": _("The query set is empty."),
    }

    def __init__(self, attrs=None, ignore_field_permission=False, **kwargs):
        """
        :param attrs: 默认为 None，返回默认的 pk， 一般需要自定义
        :param ignore_field_permission: 忽略字段权限控制
        """
        self.attrs = attrs if attrs else ["pk"]
        self.label_format = kwargs.pop("format", None)
        self.input_type = kwargs.pop("input_type", None)
        self.input_type_prefix = kwargs.pop("input_type_prefix", None)
        self.input_type_suffix = kwargs.pop("input_type_suffix", None)
        self.many = kwargs.get("many", False)
        super().__init__(**kwargs)
        self.request: Request = get_current_request()
        self.ignore_field_permission = ignore_field_permission

    def use_pk_only_optimization(self):
        return False

    def __add_request(self):
        if not self.request:
            self.request = get_current_request()

    def get_queryset(self):
        self.__add_request()
        if self.request and self.request.user and self.request.user.is_authenticated:
            return get_filter_queryset(super().get_queryset(), self.request.user)
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

        # PERF-07：关联列全量序列化 choices 会随关联表增大线性恶化（每次打开表格页都触发），
        # 这里在 queryset 层截断：只序列化上限+1 行，多余的一行仅用于判定是否发生了截断。
        max_count = get_search_choices_max_count()
        if cutoff is not None:
            queryset = queryset[:cutoff]
        elif max_count:
            queryset = queryset[:max_count + 1]

        if is_column:
            result = []
            for item in queryset:
                data = self.to_representation(item)
                if isinstance(data, dict):
                    if "pk" in data:
                        data['value'] = data.get("pk")
                else:
                    data = {"value": data, "label": data}
                result.append(data)
        else:
            result = {}
            for item in queryset:
                key = self.to_representation(item)
                if isinstance(key, dict):
                    key = key.get("pk")
                result[key] = self.display_value(item)

        if max_count and cutoff is None and len(result) > max_count:
            self.choices_truncated = True
            if isinstance(result, list):
                result = result[:max_count]
            else:
                result = dict(list(result.items())[:max_count])
        return result

    def get_allow_fields(self, value):
        self.__add_request()
        if self.attrs is None:  # 默认没写attrs, 返回默认pk
            return self.attrs
        fields = [x.name for x in value._meta.fields]

        if not isinstance(self.attrs, (list, set)):  # 如果存在，且不是列表，则返回所有字段
            self.attrs = fields
        extra_fields = set(self.attrs) - set(fields)  # 这些字段不在model内，并且不受权限控制

        if self.ignore_field_permission or (self.request and hasattr(self.request, "ignore_field_permission")):
            return set(self.attrs)

        allow_fields = []
        if self.request and settings.PERMISSION_FIELD_ENABLED:
            if hasattr(self.request, "user") and self.request.user and self.request.user.is_superuser:
                allow_fields = self.attrs
            elif hasattr(self.request, "fields"):
                if self.request.fields and isinstance(self.request.fields, dict):
                    allow_fields = self.request.fields.get(value._meta.label_lower, [])
        else:
            allow_fields = self.attrs

        return set(self.attrs) & set(allow_fields) | extra_fields

    def to_representation(self, value):
        attrs = self.get_allow_fields(value)
        if not attrs:
            return value.pk
        data = {}
        for attr in attrs:
            # if not hasattr(value, attr):
            #     continue
            # data[attr] = getattr(value, attr)
            try:
                data[attr] = attr_get(value, attr, '__')
            except Exception:
                continue
            if isinstance(data[attr], FieldFile):
                data[attr] = get_file_absolute_uri(data[attr], self.request)
            if isinstance(data[attr], partial):
                data[attr] = data[attr]()
        if data:
            if self.label_format:
                try:
                    data["label"] = self.label_format.format(**data)
                except Exception:  # 使用权限控制的时候，format字段可能不在权限里面
                    data["label"] = data.get("pk")
            else:
                if "label" not in self.attrs:
                    data["label"] = data.get("pk")
        return data

    def _get_related_memo(self):
        """PERF-10：请求级关联对象缓存。

        导入 R 行 × F 个关联字段时，旧实现每字段每行执行一次 SELECT（超管 R×F 条，
        非超管叠加数据权限最坏 5×R×F 条）。memo 挂在请求级（thread-local request 对象
        属性）上，单次请求生命周期内复用；key 必须含字段维度——不同字段的 queryset
        过滤条件不同，同一 pk 在不同数据权限下不能串用。
        celery 后台导入时 background_task_view_set_job 每任务构造独立 WSGIRequest，
        memo 天然按任务隔离。
        """
        request = None
        # 沿序列化树向上找 context（many=True 时 request 在 ListSerializer.context 上）
        node = self.parent
        while node is not None:
            context = getattr(node, 'context', None)
            if isinstance(context, dict) and context.get('request') is not None:
                request = context['request']
                break
            node = getattr(node, 'parent', None)
        if request is None:
            request = self.request
        if request is None:
            return None
        memo = getattr(request, '_related_memo', None)
        if memo is None:
            memo = request._related_memo = {}
        return memo

    def to_internal_value(self, data):
        queryset = self.get_queryset()
        if queryset is None:
            return self.fail("queryset_none")

        memo = self._get_related_memo()
        if isinstance(data, Model):
            pk = data.pk
        elif not isinstance(data, dict):
            pk = data
        else:
            pk = data.get("id") or data.get("pk") or data.get(self.attrs[0])

        memo_key = (self.field_name, str(pk))
        if memo is not None and memo_key in memo:
            return memo[memo_key]

        try:
            if isinstance(data, bool):
                raise TypeError
            obj = queryset.get(pk=pk)
        except ObjectDoesNotExist:
            self.fail("does_not_exist", pk_value=pk)
        except (TypeError, ValueError):
            self.fail("incorrect_type", data_type=type(pk).__name__)

        if memo is not None:
            memo[memo_key] = obj
        return obj

    def get_schema(self):
        """
        为 drf-spectacular 提供 OpenAPI schema
        """
        # 获取字段的基本信息
        field_type = 'array' if self.many else 'object'

        if field_type == 'array':
            # 如果是多对多关系
            return {
                'type': 'array',
                'items': self._get_openapi_item_schema(),
                'description': getattr(self, 'help_text', ''),
                'title': getattr(self, 'label', ''),
            }
        else:
            # 如果是一对一关系
            return {
                'type': 'object',
                'properties': self._get_openapi_properties_schema(),
                'description': getattr(self, 'help_text', ''),
                'title': getattr(self, 'label', ''),
            }

    def _get_openapi_item_schema(self):
        """
        获取数组项的 OpenAPI schema
        """
        return self._get_openapi_object_schema()

    def _get_openapi_object_schema(self):
        """
        获取对象的 OpenAPI schema
        """
        properties = {}

        # 动态分析 attrs 中的属性类型
        for attr in self.attrs:
            # 尝试从 queryset 的 model 中获取字段信息
            field_type = self._infer_field_type(attr)
            properties[attr] = {
                'type': field_type,
                'description': f'{attr} field'
            }

        return {
            'type': 'object',
            'properties': properties,
            'required': ['id'] if 'id' in self.attrs else []
        }

    def _infer_field_type(self, attr_name):
        """
        智能推断字段类型
        """
        try:
            # 如果有 queryset，尝试从 model 中获取字段信息
            if hasattr(self, 'queryset') and self.queryset is not None:
                model = self.queryset.model
                if hasattr(model, '_meta') and hasattr(model._meta, 'fields'):
                    field = model._meta.get_field(attr_name)
                    if field:
                        return self._map_django_field_type(field)
        except Exception:
            pass

        # 如果没有 queryset 或无法获取字段信息，使用启发式规则
        return self._heuristic_field_type(attr_name)

    def _map_django_field_type(self, field):
        """
        将 Django 字段类型映射到 OpenAPI 类型
        """
        field_type = type(field).__name__

        # 整数类型
        if 'Integer' in field_type or 'BigInteger' in field_type or 'SmallInteger' in field_type:
            return 'integer'
        # 浮点数类型
        elif 'Float' in field_type or 'Decimal' in field_type:
            return 'number'
        # 布尔类型
        elif 'Boolean' in field_type:
            return 'boolean'
        # 日期时间类型
        elif 'DateTime' in field_type or 'Date' in field_type or 'Time' in field_type:
            return 'string'
        # 文件类型
        elif 'File' in field_type or 'Image' in field_type:
            return 'string'
        # 其他类型默认为字符串
        else:
            return 'string'

    def _heuristic_field_type(self, attr_name):
        """
        启发式推断字段类型
        """
        # 基于属性名的启发式规则

        if attr_name in ['is_active', 'enabled', 'visible'] or attr_name.startswith('is_'):
            return 'boolean'
        elif attr_name in ['count', 'number', 'size', 'amount']:
            return 'integer'
        elif attr_name in ['price', 'rate', 'percentage']:
            return 'number'
        else:
            # 默认返回字符串类型
            return 'string'

    def _get_openapi_properties_schema(self):
        """
        获取对象属性的 OpenAPI schema
        """
        return self._get_openapi_object_schema()['properties']


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
