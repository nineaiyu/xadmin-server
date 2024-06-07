#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : server
# filename : urls
# author : ly_13
# date : 6/6/2023
from django.urls import re_path

from common.api.common import ResourcesIDCacheApi

urlpatterns = [
    re_path('^resources/cache$', ResourcesIDCacheApi.as_view(), name='resources-cache'),
]
