#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""
notifications app 对外服务契约层。

其他 app 需要使用 notifications 的消息通知能力时，只允许从本模块导入，
禁止直接 import notifications.notifications 等内部实现，避免 app 间横向依赖扩散。
"""
from notifications.notifications import UserMessage

__all__ = ["UserMessage"]
