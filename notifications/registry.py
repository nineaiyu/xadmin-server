#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""消息类注册表：类型 → 消息类解析与子类自动注册（自 notifications.py 拆分，行为不变）。"""

from typing import Any

from common.utils import get_logger
from notifications.backends import CHANNEL_SPECS
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


# 内置后端渲染方法注册：渠道 → 渲染方法由 backends/specs.py 单一声明派生
for _spec in CHANNEL_SPECS:
    if not hasattr(Message, _spec.renderer):
        logger.error(
            "Notification channel %s declares missing renderer %s, fallback to common message",
            _spec.code,
            _spec.renderer,
        )
        continue
    register_backend_msg(_spec.code, _spec.renderer)
