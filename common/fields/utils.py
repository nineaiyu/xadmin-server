#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : utils
# author : ly_13
# date : 7/25/2024
from functools import wraps

from django.db.models.fields.files import FieldFile
from rest_framework.fields import Field as RFField


def get_file_absolute_uri(value: FieldFile, request=None, use_url=True):
    if not value:
        return None

    if use_url:
        try:
            url = value.url
        except AttributeError:
            return None
        if request is not None:
            return request.build_absolute_uri(url)
        return url

    return value.name


def input_wrapper(func):
    """
    增加 input_type 参数，用于前端识别
    """

    @wraps(func)
    def wrapper(*args, **kwargs) -> RFField:
        class Field(func):
            def __init__(self, *_args, **_kwargs):
                self.input_type = _kwargs.pop("input_type", '')
                super().__init__(*_args, **_kwargs)

        return Field(*args, **kwargs)

    return wrapper
