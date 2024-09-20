#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : server
# filename : urls
# author : ly_13
# date : 6/6/2023
from django.urls import re_path

from common.api.common import ResourcesIDCacheApi, CountryListApi, HealthCheckView

urlpatterns = [
    re_path('^resources/cache$', ResourcesIDCacheApi.as_view(), name='resources-cache'),
    re_path('^countries$', CountryListApi.as_view(), name='countries'),
    re_path('^api/health', HealthCheckView.as_view(), name='health'),
]
