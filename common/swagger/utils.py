#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : utils
# author : ly_13
# date : 8/12/2024
from typing import List

from django_filters.utils import get_model_field
from drf_spectacular.extensions import OpenApiAuthenticationExtension, OpenApiSerializerFieldExtension
from drf_spectacular.openapi import AutoSchema
from drf_spectacular.plumbing import build_basic_type, build_object_type
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiResponse
from rest_framework.serializers import ModelSerializer
from rest_framework.utils.field_mapping import ClassLookupDict

from common.utils import get_logger

logger = get_logger(__file__)


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
        serializer = self.target.parent
        meta = getattr(serializer, 'Meta', {})
        model = getattr(meta, 'model', None)
        ms = ModelSerializer()
        field_mapping = ClassLookupDict(ms.serializer_field_mapping)
        if model:
            source = self.target.source
            source_field = get_model_field(model, source)
            source_model = getattr(source_field, 'related_model', None)
        else:
            field_mapping = None
            source_model = None
        attrs: List = self.target.attrs

        obj = {}
        for attr in attrs:
            if not source_model or not field_mapping:
                obj[attr] = build_basic_type(OpenApiTypes.STR)
            else:
                field = get_model_field(source_model, 'id' if attr == 'pk' else attr)
                if field is None:
                    obj[attr] = build_basic_type(OpenApiTypes.STR)
                else:
                    try:
                        field_class, field_kwargs = ms.build_standard_field(attr, field)
                        obj[attr] = auto_schema._map_serializer_field(field_class(**field_kwargs), direction)
                    except Exception as e:
                        logger.warning(f"get filed {attr} {field} auto schema error: {e}")
        if 'label' not in attrs:
            obj["label"] = build_basic_type(OpenApiTypes.STR)
        return build_object_type(obj)


def get_default_response_schema(data=None):
    if data is None:
        data = {}
    return {
        200: OpenApiResponse(
            build_object_type(
                properties={
                    'code': build_basic_type(OpenApiTypes.NUMBER),
                    'detail': build_basic_type(OpenApiTypes.STR),
                    **data
                }
            )
        )
    }
