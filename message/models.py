#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""聊天室数据模型。

三张表：
- ChatRoom：会话（public 全站单例 / private 一对一 / ai 每用户一间），room_key 规范化唯一键；
- ChatRoomMember：私聊与 AI 会话的成员 + 未读游标（公共聊天室全员可见，不建行）；
- ChatMessage：消息（BigAuto 主键即自增游标，(room, id) 索引支撑倒序游标分页）。

设计边界：
- 公共聊天室不维护未读（进入即浏览）；
- `sender_name` 存发送时快照，昵称改名不回溯历史消息；
- `client_msg_id` 幂等键：断线重发/乐观上屏去重，`(sender, client_msg_id)` 部分唯一索引兜底。
"""

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from common.core.models import DbBaseModel

# 发送端与展示端的消息长度上限（两端一致，见 message/protocol.py 契约）
MAX_CONTENT_LENGTH = 2000
# AI 回复/系统提示长度上限（不受用户输入上限约束：模型回答常超 2000 字）
AI_MAX_CONTENT_LENGTH = 8000
# 会话列表最后消息摘要长度
LAST_MESSAGE_LENGTH = 200
# 本人可撤回消息的时间窗口（分钟）
RECALL_WINDOW_MINUTES = 2

PUBLIC_ROOM_KEY = "public"


def private_room_key(pk_a, pk_b) -> str:
    """一对一私聊规范化键：小 pk 在前，保证 (A,B) 与 (B,A) 落到同一会话。"""
    low, high = sorted([int(pk_a), int(pk_b)])
    return f"dm:{low}:{high}"


def ai_room_key(owner_pk) -> str:
    """AI 助手会话键：每个用户一间，刷新/重进续聊。"""
    return f"ai:{int(owner_pk)}"


class ChatRoom(DbBaseModel):
    """会话（公共聊天室 / 私聊 / AI 助手）。"""

    class RoomType(models.TextChoices):
        PUBLIC = "public", _("Public chat room")
        PRIVATE = "private", _("Private chat")
        AI = "ai", _("AI assistant")

    room_type = models.CharField(
        _("Room type"), max_length=16, choices=RoomType.choices, default=RoomType.PUBLIC, db_index=True
    )
    room_key = models.CharField(_("Room key"), max_length=64, unique=True)
    name = models.CharField(_("Display name"), max_length=64, blank=True, default="")
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("Owner"),
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text=_("AI room owner; empty for public/private rooms"),
    )
    last_message = models.CharField(_("Last message"), max_length=LAST_MESSAGE_LENGTH, blank=True, default="")
    last_message_time = models.DateTimeField(_("Last message time"), null=True, blank=True, db_index=True)
    is_active = models.BooleanField(_("Active"), default=True, db_index=True)

    class Meta:
        verbose_name = _("Chat room")
        verbose_name_plural = _("Chat rooms")
        ordering = ("-last_message_time", "-id")

    def __str__(self):
        return f"{self.room_key}({self.room_type})"

    @property
    def is_public(self) -> bool:
        return self.room_type == self.RoomType.PUBLIC


class ChatRoomMember(DbBaseModel):
    """会话成员 + 未读游标（仅私聊 / AI 会话维护行）。"""

    room = models.ForeignKey(ChatRoom, verbose_name=_("Room"), on_delete=models.CASCADE, related_name="members")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name=_("User"), on_delete=models.CASCADE, related_name="+"
    )
    # 已读游标 = 该用户已读到的最大消息 id；unread_count 为冗余计数（新消息 +1、已读清零）
    last_read_id = models.BigIntegerField(_("Last read message id"), default=0)
    unread_count = models.IntegerField(_("Unread count"), default=0)

    class Meta:
        verbose_name = _("Chat room member")
        verbose_name_plural = _("Chat room members")
        constraints = [models.UniqueConstraint(fields=["room", "user"], name="uniq_chat_room_member")]
        indexes = [models.Index(fields=["user", "room"], name="chat_member_user_room_idx")]

    def __str__(self):
        return f"{self.room_id}#{self.user_id}"


class ChatMessage(DbBaseModel):
    """聊天消息。主键自增即游标，`(room, id)` 索引支撑倒序拉取 + before_id 翻页。"""

    class MessageType(models.TextChoices):
        TEXT = "text", _("Text")
        AI = "ai", _("AI reply")
        SYSTEM = "system", _("System message")

    room = models.ForeignKey(ChatRoom, verbose_name=_("Room"), on_delete=models.CASCADE, related_name="messages")
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("Sender"),
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text=_("Empty for AI / system messages"),
    )
    sender_name = models.CharField(_("Sender name"), max_length=64, blank=True, default="")
    message_type = models.CharField(
        _("Message type"), max_length=16, choices=MessageType.choices, default=MessageType.TEXT, db_index=True
    )
    content = models.TextField(_("Content"))
    client_msg_id = models.CharField(_("Client message id"), max_length=64, blank=True, default="")
    is_recalled = models.BooleanField(_("Recalled"), default=False)
    recalled_time = models.DateTimeField(_("Recalled time"), null=True, blank=True)
    # AI 回复的引用来源等结构化附加信息（text 消息为空 dict）
    extra = models.JSONField(_("Extra"), default=dict, blank=True)

    class Meta:
        verbose_name = _("Chat message")
        verbose_name_plural = _("Chat messages")
        ordering = ("-id",)
        indexes = [models.Index(fields=["room", "-id"], name="chat_msg_room_id_idx")]
        constraints = [
            # 客户端幂等键：同一发送者的同一 client_msg_id 只落一条（断线重发去重）
            models.UniqueConstraint(
                fields=["sender", "client_msg_id"],
                condition=~models.Q(client_msg_id=""),
                name="uniq_chat_msg_sender_client_id",
            )
        ]

    def __str__(self):
        return f"{self.room_id}#{self.pk}"
