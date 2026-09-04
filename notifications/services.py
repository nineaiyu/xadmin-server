#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""
notifications app 对外服务契约层。

其他 app 需要使用 notifications 的消息通知能力时，只允许从本模块导入，
禁止直接 import notifications.notifications 等内部实现，避免 app 间横向依赖扩散。

BACKEND / SystemMessage / UserMessage / SystemMsgSubscription 为通知体系的
公共扩展点（自定义消息类型需继承 SystemMessage 并实现 post_insert_to_db），
统一经由本模块引用。
"""
from notifications.backends import BACKEND
from notifications.models import SystemMsgSubscription
from notifications.notifications import SystemMessage, UserMessage

__all__ = ["BACKEND", "SystemMessage", "UserMessage", "SystemMsgSubscription"]
