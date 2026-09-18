#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""聊天室请求校验。响应体为显式字典（会话/消息载荷契约见 message/chat.py）。"""

from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from message.models import MAX_CONTENT_LENGTH


class OpenPrivateRoomSerializer(serializers.Serializer):
    """开通（或复用）与目标用户的一对一私聊会话。"""

    user_pk = serializers.IntegerField(required=True, label=_("Target user"))


class CreateGroupSerializer(serializers.Serializer):
    """创建多人群聊：群名 + 首批成员（不含创建者，创建者自动成为群主）。"""

    name = serializers.CharField(
        required=True, max_length=64, allow_blank=False, trim_whitespace=True, label=_("Group name")
    )
    member_pks = serializers.ListField(
        required=True,
        allow_empty=False,
        child=serializers.IntegerField(min_value=1),
        label=_("Members"),
    )


class GroupMembersSerializer(serializers.Serializer):
    """群成员变更：add / remove 至少一项（仅群主可操作）。"""

    add = serializers.ListField(
        required=False, default=list, child=serializers.IntegerField(min_value=1), label=_("Add members")
    )
    remove = serializers.ListField(
        required=False, default=list, child=serializers.IntegerField(min_value=1), label=_("Remove members")
    )

    def validate(self, attrs):
        if not attrs.get("add") and not attrs.get("remove"):
            raise serializers.ValidationError(_("Nothing to update"))
        return attrs


class RenameGroupSerializer(serializers.Serializer):
    """群聊改名（仅群主）。"""

    name = serializers.CharField(
        required=True, max_length=64, allow_blank=False, trim_whitespace=True, label=_("Group name")
    )


class ChatAiMessageSerializer(serializers.Serializer):
    """AI 助手提问（room_id 缺省时用当前用户的 AI 会话）。"""

    room_id = serializers.IntegerField(required=False, label=_("Room"))
    content = serializers.CharField(
        required=True, max_length=MAX_CONTENT_LENGTH, allow_blank=False, trim_whitespace=True, label=_("Content")
    )
    client_msg_id = serializers.CharField(required=False, allow_blank=True, max_length=64, label=_("Client message id"))
