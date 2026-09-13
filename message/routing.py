#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : server
# filename : routing
# author : ly_13
# date : 6/2/2023

from django.urls import re_path

from . import consumers, notify

app_name = "message"

urlpatterns = [
    # 全站通知推送 / 登录日志 / 会话登记通道（历史行为保持不变）
    re_path(r"ws/message/(?P<group_name>[\w+|\-?]+)+/(?P<username>\w+)$", notify.MessageNotify.as_asgi()),
    # 聊天室专用通道（ADR-034）：公共广播 + 私聊/AI 定向 + 未读推送，不登记会话
    re_path(r"ws/chat/$", consumers.ChatNotify.as_asgi()),
]
