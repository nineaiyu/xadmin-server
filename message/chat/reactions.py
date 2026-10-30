#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""表情回应：extra.reactions 的读改写（行锁串行化，不建新表）。

自 messaging 拆出（消息域 vs 会话域 vs 回应域分文件，控制单文件体量）。
"""

from typing import Any

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from message.models import (
    REACTION_EMOJI_MAX_LENGTH,
    REACTION_MAX_EMOJI_KEYS,
    REACTION_MAX_USERS_PER_EMOJI,
    REACTION_MESSAGE_TYPES,
    ChatMessage,
    ChatRoom,
)

from .chat_ops import _user_pk
from .chat_room_ops import accessible_room


def toggle_reaction(user: Any, message_pk: Any, emoji: Any, op: Any) -> tuple[Any, ...] | None:
    """表情回应落库（extra["reactions"] = {emoji: [user_pk, ...]}，不建新表）。

    返回 ``(房间, 消息 pk, 全量 reactions, 广播时刻 epoch 秒)``，调用方据此向房间
    广播 `chat_reaction` 帧（全量表，客户端整体替换）。

    以下情形**静默忽略**（返回 None，不报错不广播）：
    - 消息不存在 / 已撤回：撤回即冻结交互（与「撤回后附件不可取件」同口径）；
    - 操作者非房间可访问者：fail-closed，与 accessible_room 同源（不区分
      「房间不存在」与「无权」，避免探测）；
    - 机器消息（ai / system）：回应没有对象语义，前端也不提供入口；
    - op 落空（重复 add 幂等不重记 / 移除不存在的回应）：仍返回当前全量表，
      供广播把 stale 客户端拉齐。

    emoji 为空 / 超长、op 非法、回应数超上限（模型层 REACTION_* 常量）抛可读
    校验错误，由 consumer 回执 1001。

    并发：select_for_update 行锁串行化同一消息的 extra 读改写——JSONField 没有
    字段级原子操作（F 表达式只覆盖数值/列表顶层），不加锁时两个用户同时回应会
    后写覆盖前写（丢回应）。
    """
    emoji = str(emoji or "").strip()
    op = str(op or "").strip().lower()
    if not emoji:
        raise DjangoValidationError(_("Reaction emoji cannot be empty"))
    if len(emoji) > REACTION_EMOJI_MAX_LENGTH:
        raise DjangoValidationError(
            _("Reaction emoji is too long (max {} characters)").format(REACTION_EMOJI_MAX_LENGTH)
        )
    if op not in ("add", "remove"):
        raise DjangoValidationError(_("Invalid reaction operation"))
    try:
        message_pk = int(message_pk)
    except (TypeError, ValueError):
        # 上行 pk 非法按「消息不存在」静默忽略（不回错误帧，避免探测面）
        return None
    with transaction.atomic():
        message = ChatMessage.objects.select_for_update().filter(pk=message_pk).first()
        if message is None or message.is_recalled or message.message_type not in REACTION_MESSAGE_TYPES:
            return None
        try:
            accessible_room(message.room_id, user)
        except DjangoValidationError:
            return None
        user_pk = _user_pk(user)
        extra = dict(message.extra or {})
        reactions = dict(extra.get("reactions") or {})
        members = list(reactions.get(emoji) or [])
        changed = False
        if op == "add":
            if user_pk not in members:
                if emoji not in reactions and len(reactions) >= REACTION_MAX_EMOJI_KEYS:
                    raise DjangoValidationError(
                        _("Too many emojis on this message (max {})").format(REACTION_MAX_EMOJI_KEYS)
                    )
                if len(members) >= REACTION_MAX_USERS_PER_EMOJI:
                    raise DjangoValidationError(
                        _("Too many users reacted with this emoji (max {})").format(REACTION_MAX_USERS_PER_EMOJI)
                    )
                reactions[emoji] = [*members, user_pk]
                changed = True
        else:
            # 只能移除自己：载荷不含目标用户字段，实现上即无法替他人移除；
            # 移除不存在的回应（自己不在列表）为幂等落空，不产生写与广播
            if user_pk in members:
                remaining = [pk for pk in members if pk != user_pk]
                if remaining:
                    reactions[emoji] = remaining
                else:
                    reactions.pop(emoji, None)
                changed = True
        if changed:
            if reactions:
                extra["reactions"] = reactions
            else:
                extra.pop("reactions", None)  # 回应清空后移除键，extra 不留空壳
            message.extra = extra
            message.save(update_fields=["extra", "updated_time"])
    room = ChatRoom.objects.filter(pk=message.room_id).first()
    return room, message.pk, dict(extra.get("reactions") or {}), int(timezone.now().timestamp())
