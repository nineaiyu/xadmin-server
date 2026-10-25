#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""消息类注册表：类型 → 消息类解析与子类自动注册（自 notifications.py 拆分，行为不变）。"""

from typing import Any

from common.utils import get_logger
from notifications.backends import CHANNEL_SPECS
from notifications.backends.specs import ChannelSpec, register_channel_spec
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
        # 系统消息是否开放个人订阅页自选（视图层据此合并展示，默认 False）
        "user_subscribable": bool(getattr(cls, "user_subscribable", False)),
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


def register_channel(spec: ChannelSpec) -> ChannelSpec:
    """登记通知渠道（二开扩展点）：spec 清单 + 渲染方法接线一次完成。

    与内置渠道的导入期注册不同，本入口在运行期调用并立即生效；渲染方法必须
    已定义（fail-fast——静默回退公共渲染会让新渠道的排版问题难排查）。
    spec 清单的登记/冲突判定见 ``notifications.backends.specs.register_channel_spec``。
    """
    if not hasattr(Message, spec.renderer):
        raise ValueError(f"Notification channel {spec.code!r} declares missing renderer {spec.renderer!r}")
    register_channel_spec(spec)
    register_backend_msg(spec.code, spec.renderer)
    return spec
