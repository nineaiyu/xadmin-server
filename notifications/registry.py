#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""消息类注册表：类型 → 消息类解析与子类自动注册（自 notifications.py 拆分，行为不变）。"""

from typing import Any

from common.utils import get_logger
from notifications.backends import BACKEND
from notifications.notifications import (
    SYSTEM_MESSAGE_REGISTRY,
    USER_MESSAGE_REGISTRY,
    Message,
    SystemMessage,
    UserMessage,
    register_backend_msg,
)

logger = get_logger(__name__)


def get_message_cls(message_type: Any) -> Any:
    """按 message_type 取消息类（系统 + 用户注册表），未注册返回 None。

    供「发送测试消息」入口按订阅行的 message_type 定位实现类。
    """
    for info in SYSTEM_MESSAGE_REGISTRY + USER_MESSAGE_REGISTRY:
        if info["message_type"] == message_type:
            return info["cls"]
    return None


def register_message(cls: Any) -> Any:
    """消息类型显式注册：装饰在 Message 子类上，替代元类隐式收集。

    子类需定义 message_type_label / category / category_label；
    注册信息由消息订阅视图消费（notifications/views/notifications.py）。
    """
    if not issubclass(cls, Message):
        raise TypeError(f"register_message only accepts Message subclasses, got {cls!r}")
    info = {
        "message_type": cls.get_message_type(),
        "message_type_label": cls.message_type_label,
        "category": cls.category,
        "category_label": cls.category_label,
        # 类引用：post_migrate 补建订阅时回调 cls.post_insert_to_db
        "cls": cls,
    }
    if issubclass(cls, SystemMessage):
        SYSTEM_MESSAGE_REGISTRY.append(info)
    elif issubclass(cls, UserMessage):
        USER_MESSAGE_REGISTRY.append(info)
    else:
        raise TypeError(f"register_message requires SystemMessage or UserMessage subclass, got {cls!r}")
    return cls


# 内置后端渲染方法注册（新增后端时在各自模块加一行 register_backend_msg 即可）
register_backend_msg(BACKEND.EMAIL, "get_email_msg")
register_backend_msg(BACKEND.SITE_MSG, "get_site_msg_msg")
register_backend_msg(BACKEND.SMS, "get_sms_msg")
# IM 渠道：文本消息共用 HTML 转纯文本渲染
register_backend_msg(BACKEND.DINGTALK, "get_text_msg")
register_backend_msg(BACKEND.WECOM, "get_text_msg")
register_backend_msg(BACKEND.FEISHU, "get_text_msg")
