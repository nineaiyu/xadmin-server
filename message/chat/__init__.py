#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""聊天室领域服务门面：会话开通 / 消息落库 / 未读游标 / 联系人 / @提及。

分层约定：
- 实现按职责拆在本包子模块（messaging / chat_ops / chat_room_ops / chat_rooms /
  attachments / recall_audit / ai），对外调用面 ``from message import chat as
  chat_service`` 的属性访问经本文件再导出保持不变；
- 各实现只做同步 DB 与纯函数逻辑（REST 视图与 WS consumer 共用同一套语义）：
  WS consumer 经 `database_sync_to_async` 调用，REST 视图直接调用；
- 消息广播（channel layer）不在本包，由 message/ws 层负责，避免同步/异步边界混杂；
- 可访问性一律 fail-closed：非成员访问私聊、非归属访问他人 AI 会话均按「房间不存在」拒绝。
"""

from message.models import (
    AI_MAX_CONTENT_LENGTH as AI_MAX_CONTENT_LENGTH,
)
from message.models import (
    BOT_MESSAGE_TYPES as BOT_MESSAGE_TYPES,  # noqa: F401 显式再导出（PEP 484 语义）
)
from message.models import (
    MAX_CONTENT_LENGTH as MAX_CONTENT_LENGTH,
)
from message.models import (
    REACTION_EMOJI_MAX_LENGTH as REACTION_EMOJI_MAX_LENGTH,
)
from message.models import (
    REACTION_MAX_EMOJI_KEYS as REACTION_MAX_EMOJI_KEYS,
)
from message.models import (
    REACTION_MAX_USERS_PER_EMOJI as REACTION_MAX_USERS_PER_EMOJI,
)
from message.models import (
    REACTION_MESSAGE_TYPES as REACTION_MESSAGE_TYPES,
)
from message.models import (
    RECALL_WINDOW_MINUTES as RECALL_WINDOW_MINUTES,
)
from message.models import ChatMessage as ChatMessage  # noqa: F401 显式再导出（PEP 484 语义）
from message.models import ChatRoom as ChatRoom  # noqa: F401 显式再导出（PEP 484 语义）
from message.models import ChatRoomMember as ChatRoomMember  # noqa: F401 显式再导出（PEP 484 语义）

# 再导出（PEP 484 显式形式，跨模块调用面保持不变）：附件链路 / chat_service 调用面 / 房间与群组管理 / 会话列表构造 / 撤回审计
from .attachments import ATTACHMENT_MESSAGE_TYPES as ATTACHMENT_MESSAGE_TYPES
from .attachments import AttachmentUploadError as AttachmentUploadError
from .attachments import attachment_extra as attachment_extra
from .attachments import attachment_kind as attachment_kind
from .attachments import attachment_payload as attachment_payload
from .attachments import attachment_response as attachment_response
from .attachments import mark_attachment_used as mark_attachment_used
from .attachments import resolve_sender_attachment as resolve_sender_attachment
from .attachments import store_message_attachment as store_message_attachment
from .attachments import validate_attachment_kind as validate_attachment_kind
from .chat_ops import _normalize_user_pks as _normalize_user_pks
from .chat_ops import _user_pk as _user_pk
from .chat_ops import avatar_url as avatar_url
from .chat_ops import clean_expired_history as clean_expired_history
from .chat_ops import display_name as display_name
from .chat_ops import mention_users as mention_users
from .chat_ops import new_client_msg_id as new_client_msg_id
from .chat_ops import parse_mentions as parse_mentions
from .chat_ops import store_chat_notices as store_chat_notices
from .chat_ops import user_brief as user_brief
from .chat_ops import validate_content as validate_content
from .chat_room_ops import accessible_room as accessible_room
from .chat_room_ops import add_group_members as add_group_members
from .chat_room_ops import create_group as create_group
from .chat_room_ops import get_or_create_ai_room as get_or_create_ai_room
from .chat_room_ops import get_or_create_private_room as get_or_create_private_room
from .chat_room_ops import get_or_create_private_room_by_pk as get_or_create_private_room_by_pk
from .chat_room_ops import get_public_room as get_public_room
from .chat_room_ops import group_room_or_deny as group_room_or_deny
from .chat_room_ops import leave_group as leave_group
from .chat_room_ops import remove_group_members as remove_group_members
from .chat_room_ops import rename_group as rename_group
from .chat_room_ops import room_member_pks as room_member_pks
from .chat_rooms import UNSET as UNSET
from .chat_rooms import online_user_pks as online_user_pks
from .chat_rooms import prefetch_room_context as prefetch_room_context
from .chat_rooms import room_to_dict as room_to_dict

# 再导出：消息域实现（自本包 messaging 拆出，调用面 chat_service.<fn> 保持不变）
from .chat_sessions import CONTACT_LIMIT as CONTACT_LIMIT
from .chat_sessions import ROOM_LIST_LIMIT as ROOM_LIST_LIMIT
from .chat_sessions import bump_unread as bump_unread
from .chat_sessions import list_user_rooms as list_user_rooms
from .chat_sessions import mark_read as mark_read
from .chat_sessions import recent_contacts as recent_contacts
from .messaging import can_recall_for as can_recall_for
from .messaging import create_message as create_message
from .messaging import get_attachment_message as get_attachment_message
from .messaging import history_messages as history_messages
from .messaging import message_payload as message_payload
from .messaging import recall_message as recall_message
from .messaging import sender_avatar_map as sender_avatar_map
from .reactions import toggle_reaction as toggle_reaction

# 再导出：撤回审计常量与快照写入（调用面/测试沿用 chat_service 常量）
from .recall_audit import RECALL_AUDIT_MODULE as RECALL_AUDIT_MODULE
from .recall_audit import write_recall_snapshot as write_recall_snapshot
