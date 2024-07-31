#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : form_data
# author : ly_13
# date : 6/25/2024
import re

from django.conf import settings
from django.http import QueryDict
from django.http.multipartparser import MultiPartParser as DjangoMultiPartParser
from django.http.multipartparser import MultiPartParserError
from django.utils.translation import gettext_lazy as _
from rest_framework.exceptions import ParseError
from rest_framework.parsers import BaseParser, DataAndFiles


def format_data(data: QueryDict | dict):
    """
    axios 配置如下：

    const defaultConfig: AxiosRequestConfig = {
      baseURL: import.meta.env.VITE_API_DOMAIN,
      // 请求超时时间
      timeout: 30000,
      headers: {
        Accept: "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "X-Requested-With": "XMLHttpRequest"
      },
      // 数组格式参数序列化（https://github.com/axios/axios/issues/5142）
      // 自动转换为ids=1&ids=2&ids=3这种形式
      paramsSerializer: params => {
        return stringify(params, { arrayFormat: "repeat" });
      },
      formSerializer: { indexes: null, dots: true }
    };

    axios form-data 反向解析器
    将form-data数据：
    {
        'category.value': '0',
        'admin.value': '1',
        'admin.label': '(isummer)',
        'admin.pk': '1',
        'covers.0.value': '2',
        'covers.0.label': '1111',
        'covers.0.pk': '2',
        'covers.1.value': '11112',
        'covers.1.label': '11111111',
        'covers.1.pk': '11112',
    }
    解析为：
    {
        'category': {'value': '0'},
        'admin': {'value': '1', 'label': '(isummer)', 'pk': '1'},
        'covers': [
            {'value': '2', 'label': '1111', 'pk': '2'},
            {'value': '11112', 'label': '11111111', 'pk': '11112'}
        ]
    }
    """
    new_data = {}
    for key, value in data.items():
        key_split = key.split('.')
        if len(key_split) == 1:  # 直接key
            if key_split[0] == 'pks':  # 用于批量操作
                try:
                    value = data.getlist(key_split[0])
                except:
                    value = data.get(key_split[0])
            new_data[key_split[0]] = value
        else:
            if re.match(r'\d+', key_split[1]):  # 列表
                info: list = new_data.get(key_split[0])
                if not info:
                    new_data[key_split[0]] = [{}]
                    result = format_data({".".join(key_split[1:]): value})
                    lk = list(result.keys())
                    new_data[key_split[0]][int(lk[0])] = result.get(lk[0])
                else:
                    result = format_data({".".join(key_split[1:]): value})
                    lk = list(result.keys())
                    if int(lk[0]) + 1 > len(new_data[key_split[0]]):
                        new_data[key_split[0]].append({})
                    new_data[key_split[0]][int(lk[0])].update(result.get(lk[0]))
            else:  # 字典
                info: dict = new_data.get(key_split[0], {})
                if not info:
                    new_data[key_split[0]] = format_data({".".join(key_split[1:]): value})
                else:
                    info.update(format_data({".".join(key_split[1:]): value}))

    return new_data


class AxiosMultiPartParser(BaseParser):
    """
    Parser for multipart form data, which may include file data.
    """
    media_type = 'multipart/form-data'

    def parse(self, stream, media_type=None, parser_context=None):
        """
        Parses the incoming bytestream as a multipart encoded form,
        and returns a DataAndFiles object.

        `.data` will be a `QueryDict` containing all the form parameters.
        `.files` will be a `QueryDict` containing all the form files.
        """
        parser_context = parser_context or {}
        request = parser_context['request']
        encoding = parser_context.get('encoding', settings.DEFAULT_CHARSET)
        meta = request.META.copy()
        meta['CONTENT_TYPE'] = media_type
        upload_handlers = request.upload_handlers

        try:
            parser = DjangoMultiPartParser(meta, stream, upload_handlers, encoding)
            data, files = parser.parse()
            new_data = QueryDict('', mutable=True)
            for key, value in format_data(data).items():
                if isinstance(value, list):  # list一般为manytomany, 需要通过setlist进行设置
                    new_data.setlist(key, value)
                else:
                    new_data[key] = value
            return DataAndFiles(new_data, files)
        except MultiPartParserError as exc:
            raise ParseError(_("Multipart form parse error - {}").format(str(exc)))
