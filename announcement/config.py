#!/usr/bin/env python
# -*- coding:utf-8 -*-

from django.urls import path, include

URLPATTERNS = [
    path('api/announcement/', include('announcement.urls')),
]

PERMISSION_WHITE_REURL = {
    "^/api/announcement/user$": ['GET'],
    "^/api/announcement/user/.*$": ['GET'],
}
