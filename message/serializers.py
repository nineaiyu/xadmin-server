#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""聊天室请求校验（ADR-034）。响应体为显式字典（会话/消息载荷契约见 message/chat.py）。"""

from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from message.models import MAX_CONTENT_LENGTH


class OpenPrivateRoomSerializer(serializers.Serializer):
    """开通（或复用）与目标用户的一对一私聊会话。"""

    user_pk = serializers.IntegerField(required=True, label=_("Target user"))


class ChatAiMessageSerializer(serializers.Serializer):
    """AI 助手提问（room_id 缺省时用当前用户的 AI 会话）。"""

    room_id = serializers.IntegerField(required=False, label=_("Room"))
    content = serializers.CharField(
        required=True, max_length=MAX_CONTENT_LENGTH, allow_blank=False, trim_whitespace=True, label=_("Content")
    )
    client_msg_id = serializers.CharField(required=False, allow_blank=True, max_length=64, label=_("Client message id"))
