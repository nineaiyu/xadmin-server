#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : utils
# author : ly_13
# date : 7/25/2024
from functools import wraps
from typing import Any

from django.db.models.fields.files import FieldFile
from rest_framework.fields import Field as RFField

from common.utils.media_signing import sign_media_url


def get_file_absolute_uri(value: FieldFile, request: Any = None, use_url: bool = True) -> Any:
    if not value:
        return None

    if use_url:
        try:
            url = value.url
        except AttributeError:
            return None
        # 敏感命名空间（导出 / 附件等）的媒体链接统一带短时效签名，且默认绑定当前
        # 请求用户（见 utils/media_signing：他人持链接不可用）；无请求上下文时按
        # 空绑定签出——认证后恒不匹配，fail-closed
        url = sign_media_url(url, user=getattr(request, "user", None))
        if request is not None:
            return request.build_absolute_uri(url)
        return url

    return value.name


def input_wrapper(func: type[RFField]) -> Any:
    """
    增加 input_type 参数，用于前端识别
    """

    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> RFField:
        class Field(func):
            def __init__(self, *_args: Any, **_kwargs: Any) -> None:
                self.input_type = _kwargs.pop("input_type", "")
                super().__init__(*_args, **_kwargs)

        return Field(*args, **kwargs)

    return wrapper
