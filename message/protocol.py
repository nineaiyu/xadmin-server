#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""WebSocket 消息协议 Schema（ADR-003）。

上行/下行帧均为 JSON，公共形状：

    {
        "action": "<Action>",   # 动作，见 MessageAction
        "data": ...,            # 载荷，由具体 action 决定（见各 Payload TypedDict）
        "mid": "<str>",         # 可选，客户端回显一致性标记
        "v": 1,                 # 协议版本，可选；出站帧由服务端统一携带
    }

出站帧（send_base_json）额外含 code / detail / timestamp。
新增 action 必须：① 在此登记枚举；② 补充对应 Payload TypedDict。
"""
from enum import Enum
from typing import Any, Dict, List, Optional, TypedDict

PROTOCOL_VERSION = 1


class MessageAction(str, Enum):
    """消息动作枚举（字符串值，方便 match/比较与载荷路由）。"""

    PING = "ping"                    # 心跳：上行 ping → 下行 data='pong'
    USERINFO = "userinfo"            # 请求/推送当前登录用户信息
    PUSH_MESSAGE = "push_message"    # 站内信/通知推送
    CHAT_MESSAGE = "chat_message"    # 聊天室消息（双向）
    TASK_LOG = "task_log"            # 任务执行日志增量推送（system/ws.py）


class InboundMessage(TypedDict, total=False):
    """客户端→服务端帧。"""

    action: str
    data: Dict[str, Any]
    mid: str
    v: int


class OutboundMessage(TypedDict, total=False):
    """服务端→客户端帧。"""

    code: int
    detail: str
    action: str
    timestamp: str
    data: Any
    mid: str
    v: int


class PingPayload(TypedDict):
    content: str


class UserinfoPayload(TypedDict):
    pk: str
    userinfo: Dict[str, Any]


class ChatMessagePayload(TypedDict, total=False):
    """聊天气泡载荷；服务端回填 pk/username 后广播。"""

    text: str
    pk: str
    username: str
    timestamp: str


class PushMessagePayload(TypedDict, total=False):
    """通知推送载荷（message_type 语义见 message/notifications.py）。"""

    message_type: str
    title: str
    message: str
    level: str
    notice_type: Dict[str, Any]
    pk: str
    sender: Optional[str]
    recipients: List[Any]


class TaskLogPayload(TypedDict):
    """任务执行日志增量帧（system/ws.py 每轮推送一次）。"""

    offset: int
    content: str
    finished: bool