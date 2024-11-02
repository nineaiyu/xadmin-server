#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : server
# filename : urls
# author : ly_13
# date : 6/6/2023
from django.urls import re_path

from common.api.common import ResourcesIDCacheAPIView, CountryListAPIView, HealthCheckAPIView

app_name = "common"

urlpatterns = [
    re_path('^resources/cache$', ResourcesIDCacheAPIView.as_view(), name='resources-cache'),
    re_path('^countries$', CountryListAPIView.as_view(), name='countries'),
    re_path('^api/health', HealthCheckAPIView.as_view(), name='health'),
]
