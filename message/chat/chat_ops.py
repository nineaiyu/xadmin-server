#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""聊天室工具函数与运维操作（自 message/chat.py 拆出，控制单文件体量）。

- 展示工具：用户简介 / 显示名 / 头像（会话渲染、联系人列表与 @提及共用）；
- 提及解析：@username 模式与解析（消息落库与未读通知链共用）；
- 运维：消息幂等键（AI/系统消息落库）、过期历史清理（beat 周期任务调用）。

分层约定：本模块只依赖 models 与配置，不反向依赖 chat.py；chat.py 再导出
本模块符号，历史调用面 ``from message import chat as chat_service`` 保持不变。
"""

import re
import uuid
from typing import Any

from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from common.utils import get_logger
from message.models import MAX_CONTENT_LENGTH, ChatMessage

logger = get_logger(__name__)


def validate_content(content: str, limit: int = MAX_CONTENT_LENGTH) -> str:
    """消息正文归一与长度校验（空内容/超上限由 ValueError 语义的校验异常拒绝）。"""
    content = (content or "").strip()
    if not content:
        raise DjangoValidationError(_("Message content cannot be empty"))
    if len(content) > limit:
        raise DjangoValidationError(_("Message is too long (max {} characters)").format(limit))
    return content


# @提及解析：@username（用户名允许字符集见 Django UnicodeUsernameValidator）
MENTION_PATTERN = re.compile(r"@([\w.\-]+)")


def _user_pk(obj: Any) -> Any:
    return getattr(obj, "pk", obj)


def display_name(user: Any) -> str:
    """消息/联系人展示名：优先昵称，回退用户名。"""
    if user is None:
        return ""
    return (getattr(user, "nickname", "") or getattr(user, "username", "") or "")[:64]


def avatar_url(user: Any) -> str:
    """头像 URL（无头像/存储异常返回空串，前端回退首字母头像）。"""
    avatar = getattr(user, "avatar", None)
    if not avatar:
        return ""
    try:
        url: str = avatar.url
        return url
    except Exception:  # noqa: BLE001 存储异常不应影响消息链路
        return ""


def user_brief(user: Any) -> dict[str, Any]:
    """用户简介（会话对端 / 联系人共用）。"""
    if user is None:
        return {}
    return {
        "pk": user.pk,
        "username": getattr(user, "username", "") or "",
        "nickname": getattr(user, "nickname", "") or "",
        "avatar": avatar_url(user),
    }


def _normalize_user_pks(raw_pks: Any) -> list[Any]:
    """主键列表规范化：可转 int、去重、剔除非法值（保序）。"""
    result = []
    for raw in raw_pks or []:
        try:
            pk = int(raw)
        except (TypeError, ValueError):
            continue
        if pk > 0 and pk not in result:
            result.append(pk)
    return result


def parse_mentions(content: str) -> list[Any]:
    """全位置、多目标解析 @提及（去重保序）。"""
    seen = []
    for name in MENTION_PATTERN.findall(content or ""):
        if name and name not in seen:
            seen.append(name)
    return seen


def mention_users(content: str, exclude_username: str = "", room: Any = None) -> list[Any]:
    """把 @提及解析为可提醒的用户对象（跳过自己与不存在的用户名）。

    携带 ``room`` 时按房间成员收敛提醒目标（非成员不投递、不提醒）：
    - 公共房间「成员」= 持有聊天通道准入权的用户（与 WS 准入同一套权限数据）——
      否则任意聊天用户手打 @username 即可向未开通聊天的任意账号投递任意内容站内信；
    - 私聊/群聊/AI 房间 = 房间成员行（ChatRoomMember）。
    """
    from identity.models import UserInfo

    names = [name for name in parse_mentions(content) if name != exclude_username]
    if not names:
        return []
    matched = list(UserInfo.objects.filter(username__in=names, is_active=True))
    if room is not None:
        matched = _room_mention_scope(matched, room)
    return matched


def _room_mention_scope(users: list[Any], room: Any) -> list[Any]:
    """按房间可访问性收敛 @提及目标（公共房间按聊天准入权，其余按成员行）。"""
    from message.models import ChatRoom, ChatRoomMember

    if getattr(room, "room_type", None) == ChatRoom.RoomType.PUBLIC:
        return [user for user in users if _can_access_chat(user)]
    member_pks = set(ChatRoomMember.objects.filter(room=room).values_list("user_id", flat=True))
    return [user for user in users if user.pk in member_pks]


def _can_access_chat(user: Any) -> bool:
    """聊天通道准入（与 message/ws/consumers.py 的 has_chat_permission 同一口径）。

    超管放行；其余用户需持有聊天室会话列表权限（菜单权限点 list:ChatRoom →
    路径 api/chat/room$）。逐用户判定（@提及目标一消息内至多数人，可接受）。
    """
    if getattr(user, "is_superuser", False):
        return True
    from common.core.permission import get_menu_pk, get_user_permission

    data = get_user_permission(user, "GET")
    return bool(get_menu_pk(data, "/api/chat/room"))


def new_client_msg_id() -> str:
    """服务端侧消息幂等键（AI/系统消息落库用，避免与客户端键空间混淆）。"""
    return uuid.uuid4().hex


def clean_expired_history(keep_days: Any = None, batch_size: Any = 2000) -> int:
    """分批删除超过保留期的聊天消息（CHAT_HISTORY_DAYS，0 = 不清理）。

    只删消息行，不动会话与成员关系：历史清空的会话仍在列表里（摘要自然为空），
    未读游标等冗余字段失效无害。按 id 升序小批删除，避免长事务长时间锁表。
    """
    from common.core.config import SysConfig

    days = SysConfig.CHAT_HISTORY_DAYS if keep_days is None else keep_days
    if not days or int(days) <= 0:
        return 0
    deadline = timezone.now() - timezone.timedelta(days=int(days))
    removed = 0
    while True:
        ids = list(
            ChatMessage.objects.filter(created_time__lte=deadline)
            .order_by("id")
            .values_list("id", flat=True)[:batch_size]
        )
        if not ids:
            break
        deleted, _counts = ChatMessage.objects.filter(id__in=ids).delete()
        removed += deleted
    logger.info(f"clean {removed} chat history message, keep_days {days}")
    return removed


def store_chat_notices(user_pks: Any, title: str, message: str, extra: dict[str, Any] | None = None) -> int:
    """聊天提醒落库（站内信兜底）：给「不在聊天室页面」的提醒目标落持久记录。

    WS 推送负责实时、落库负责可回看——此前纯 group_send，目标离线即丢、通知
    中心无记录；现与推送同批目标（同 PUSH_CHAT_MESSAGE 偏好口径）落 USER 类站内信，
    离线用户上线后可在通知中心看到并跳转对应聊天室（extra.chat_room_id）。
    群聊普通消息不落库（高频降噪），@提及与私聊落库。返回落库接收人数。
    """
    pks = [pk for pk in dict.fromkeys(user_pks or []) if pk]
    if not pks:
        return 0
    from notifications.message import SiteMessageUtil
    from notifications.models import MessageContent

    SiteMessageUtil.store_notice(
        list(pks),
        title=title,
        message=message or "",
        notice_type=MessageContent.NoticeChoices.USER,  # type: ignore[arg-type]  # Choices 元类（运行期为枚举成员）
        level=MessageContent.LevelChoices.DEFAULT,  # type: ignore[arg-type]  # Choices 元类（运行期为枚举成员）
        extra_json=extra,
    )
    return len(pks)
