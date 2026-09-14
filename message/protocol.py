#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""WebSocket 消息协议 Schema。

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

from enum import StrEnum
from typing import Any, TypedDict

PROTOCOL_VERSION = 1


class MessageAction(StrEnum):
    """消息动作枚举（字符串值，方便 match/比较与载荷路由）。

    `chat_message` 在两条通道上语义不同（历史原因，见各 Payload 文档）：
    - `ws/message/<group>/<username>`（MessageNotify，历史通道，仅兼容保留）载荷 = ChatMessagePayload；
    - `ws/chat/`（ChatNotify，聊天室重构后的通道）载荷 = ChatRoomMessagePayload。
    """

    PING = "ping"  # 心跳：上行 ping → 下行 data='pong'
    USERINFO = "userinfo"  # 请求/推送当前登录用户信息
    PUSH_MESSAGE = "push_message"  # 站内信/通知推送
    CHAT_MESSAGE = "chat_message"  # 聊天室消息（双向）
    CHAT_RECALL = "chat_recall"  # 消息撤回（双向，ws/chat/）
    CHAT_READ = "chat_read"  # 已读回执（上行 chat_read → 下行游标）
    CHAT_UNREAD = "chat_unread"  # 未读红点推送（下行，ws/chat/）
    TASK_LOG = "task_log"  # 任务执行日志增量推送（system/ws.py）
    MONITOR = "monitor"  # 监控面板指标推送（system/ws_monitor.py）


class InboundMessage(TypedDict, total=False):
    """客户端→服务端帧。"""

    action: str
    data: dict[str, Any]
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
    userinfo: dict[str, Any]


class ChatMessagePayload(TypedDict, total=False):
    """历史聊天通道（ws/message/*）的气泡载荷；服务端回填 pk/username 后广播。

    仅供旧 MessageNotify 兼容使用，新聊天室请用 ChatRoomMessagePayload。
    """

    text: str
    pk: str
    username: str
    timestamp: str


class ChatRoomMessagePayload(TypedDict, total=False):
    """聊天室消息载荷（ws/chat/ 通道）：落库后广播的完整消息记录。

    id 为自增主键（即游标），client_msg_id 供发送端做本地幂等对齐。
    """

    id: int
    room_id: int
    room_type: str
    sender_pk: int | None
    sender_name: str
    sender_avatar: str
    message_type: str
    content: str
    created_time: str
    client_msg_id: str
    extra: dict[str, Any]


class ChatRecallPayload(TypedDict, total=False):
    """消息撤回帧（上行只带 message_id；下行广播撤回结果）。"""

    message_id: int
    id: int
    room_id: int
    operator_pk: int


class ChatReadPayload(TypedDict, total=False):
    """已读帧：上行 {room_id} → 下行回执最新已读游标。"""

    room_id: int
    last_read_id: int


class ChatUnreadPayload(TypedDict, total=False):
    """未读红点帧（下行；仅私聊/AI 会话维护未读）。"""

    room_id: int
    unread_count: int


class PushMessagePayload(TypedDict, total=False):
    """通知推送载荷（message_type 语义见 message/notifications.py）。"""

    message_type: str
    title: str
    message: str
    level: str
    notice_type: dict[str, Any]
    pk: str
    sender: str | None
    recipients: list[Any]


class TaskLogPayload(TypedDict):
    """任务执行日志增量帧（system/ws.py 每轮推送一次）。"""

    offset: int
    content: str
    finished: bool


class MonitorPushPayload(TypedDict, total=False):
    """监控指标推送帧（system/ws_monitor.py）。

    section=live：主机实时快照（高频，psutil 直读）；
    section=panel：服务健康 / Redis / Celery / 慢请求 / 趋势（低频重采集）。
    """

    section: str
    live: dict[str, Any]
    services: dict[str, Any]
    redis: dict[str, Any]
    celery: dict[str, Any]
    slow: dict[str, Any]
    trend: list[dict[str, Any]]
