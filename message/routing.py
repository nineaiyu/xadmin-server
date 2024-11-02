#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : server
# filename : routing
# author : ly_13
# date : 6/2/2023

from django.urls import re_path

from . import notify

app_name = 'message'

urlpatterns = [
    re_path(r"ws/message/(?P<group_name>[\w+|\-?]+)+/(?P<username>\w+)$", notify.MessageNotify.as_asgi()),
]
