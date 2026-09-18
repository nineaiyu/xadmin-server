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

from django.utils import timezone

from common.utils import get_logger
from message.models import ChatMessage

logger = get_logger(__name__)

# @提及解析：@username（用户名允许字符集见 Django UnicodeUsernameValidator）
MENTION_PATTERN = re.compile(r"@([\w.\-]+)")


def _user_pk(obj):
    return getattr(obj, "pk", obj)


def display_name(user) -> str:
    """消息/联系人展示名：优先昵称，回退用户名。"""
    if user is None:
        return ""
    return (getattr(user, "nickname", "") or getattr(user, "username", "") or "")[:64]


def avatar_url(user) -> str:
    """头像 URL（无头像/存储异常返回空串，前端回退首字母头像）。"""
    avatar = getattr(user, "avatar", None)
    if not avatar:
        return ""
    try:
        return avatar.url
    except Exception:  # noqa: BLE001 存储异常不应影响消息链路
        return ""


def user_brief(user) -> dict:
    """用户简介（会话对端 / 联系人共用）。"""
    if user is None:
        return {}
    return {
        "pk": user.pk,
        "username": getattr(user, "username", "") or "",
        "nickname": getattr(user, "nickname", "") or "",
        "avatar": avatar_url(user),
    }


def _normalize_user_pks(raw_pks) -> list:
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


def parse_mentions(content: str) -> list:
    """全位置、多目标解析 @提及（去重保序）。"""
    seen = []
    for name in MENTION_PATTERN.findall(content or ""):
        if name and name not in seen:
            seen.append(name)
    return seen


def mention_users(content: str, exclude_username: str = "") -> list:
    """把 @提及解析为在用用户对象（跳过自己与不存在的用户名）。"""
    from system.models import UserInfo

    names = [name for name in parse_mentions(content) if name != exclude_username]
    if not names:
        return []
    return list(UserInfo.objects.filter(username__in=names, is_active=True))


def new_client_msg_id() -> str:
    """服务端侧消息幂等键（AI/系统消息落库用，避免与客户端键空间混淆）。"""
    return uuid.uuid4().hex


def clean_expired_history(keep_days=None, batch_size=2000) -> int:
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
