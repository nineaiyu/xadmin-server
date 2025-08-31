#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : utils
# author : ly_13
# date : 8/12/2024
from typing import List

from drf_spectacular.extensions import OpenApiAuthenticationExtension, OpenApiSerializerFieldExtension
from drf_spectacular.openapi import AutoSchema
from drf_spectacular.plumbing import build_basic_type, build_object_type
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiResponse

from common.utils import get_logger

logger = get_logger(__name__)


class CustomAutoSchema(AutoSchema):

    def get_tags(self) -> List[str]:
        return [self.view.__class__.__name__]


class OpenApiAuthenticationScheme(OpenApiAuthenticationExtension):
    target_class = 'common.core.auth.CookieJWTAuthentication'  # full import path OR class ref
    name = 'CookieJWTAuthentication'  # name used in the schema

    def get_security_definition(self, auto_schema):
        return {}


class OpenApiPrimaryKeyRelatedField(OpenApiSerializerFieldExtension):
    target_class = 'common.core.fields.BasePrimaryKeyRelatedField'

    def map_serializer_field(self, auto_schema, direction):
        field = self.target
        # 获取字段的基本信息
        field_type = 'array' if field.many else 'object'

        if field_type == 'array':
            # 如果是多对多关系
            return {
                'type': 'array',
                'items': self._get_openapi_item_schema(field),
                'description': getattr(field, 'help_text', ''),
                'title': getattr(field, 'label', ''),
            }
        else:
            # 如果是一对一关系
            return {
                'type': 'object',
                'properties': self._get_openapi_properties_schema(field),
                'description': getattr(field, 'help_text', ''),
                'title': getattr(field, 'label', ''),
            }

    def _get_openapi_item_schema(self, field):
        """
        获取数组项的 OpenAPI schema
        """
        return self._get_openapi_object_schema(field)

    def _get_openapi_object_schema(self, field):
        """
        获取对象的 OpenAPI schema
        """
        properties = {}

        # 动态分析 attrs 中的属性类型
        for attr in field.attrs:
            # 尝试从 queryset 的 model 中获取字段信息
            field_type = self._infer_field_type(field, attr)
            properties[attr] = {
                'type': field_type,
                'description': f'{attr} field'
            }

        return {
            'type': 'object',
            'properties': properties,
            'required': ['id'] if 'id' in field.attrs else []
        }

    def _infer_field_type(self, field, attr_name):
        """
        智能推断字段类型
        """
        try:
            # 如果有 queryset，尝试从 model 中获取字段信息
            if hasattr(field, 'queryset') and field.queryset is not None:
                model = field.queryset.model
                if hasattr(model, '_meta') and hasattr(model._meta, 'fields'):
                    model_field = model._meta.get_field(attr_name)
                    if model_field:
                        return self._map_django_field_type(model_field)
        except Exception:
            pass

        # 如果没有 queryset 或无法获取字段信息，使用启发式规则
        return self._heuristic_field_type(attr_name)

    def _map_django_field_type(self, model_field):
        """
        将 Django 字段类型映射到 OpenAPI 类型
        """
        field_type = type(model_field).__name__

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

    def _get_openapi_properties_schema(self, field):
        """
        获取对象属性的 OpenAPI schema
        """
        return self._get_openapi_object_schema(field)['properties']


class LabeledChoiceFieldExtension(OpenApiSerializerFieldExtension):
    """
    为 LabeledChoiceField 提供 OpenAPI schema
    """
    target_class = 'common.core.fields.LabeledChoiceField'

    def map_serializer_field(self, auto_schema, direction):
        field = self.target

        if getattr(field, 'many', False):
            return {
                'type': 'array',
                'items': {
                    'type': 'object',
                    'properties': {
                        'value': {'type': 'string'},
                        'label': {'type': 'string'}
                    }
                },
                'description': getattr(field, 'help_text', ''),
                'title': getattr(field, 'label', ''),
            }
        else:
            return {
                'type': 'object',
                'properties': {
                    'value': {'type': 'string'},
                    'label': {'type': 'string'}
                },
                'description': getattr(field, 'help_text', ''),
                'title': getattr(field, 'label', ''),
            }


def get_default_response_schema(data=None):
    if data is None:
        data = {}
    return {
        200: OpenApiResponse(
            build_object_type(
                properties={
                    'code': build_basic_type(OpenApiTypes.NUMBER),
                    'detail': build_basic_type(OpenApiTypes.STR),
                    'requestId': build_basic_type(OpenApiTypes.STR),
                    'timestamp': build_basic_type(OpenApiTypes.STR),
                    **data
                }
            )
        )
    }
