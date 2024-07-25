#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : utils
# author : ly_13
# date : 7/25/2024
from django.db.models.fields.files import FieldFile


def get_file_absolute_uri(value:FieldFile, request=None, use_url=True):
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