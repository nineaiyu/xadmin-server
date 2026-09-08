# -*- coding: utf-8 -*-
"""WebSocket 协议 Schema 测试。

覆盖 MessageAction 枚举完整性、出站帧携带协议版本 v 与通用字段，
防止新增 action 或改帧结构时契约静默漂移。
"""

from asgiref.sync import async_to_sync

from message.base import AsyncJsonWebsocket
from message.protocol import (
    PROTOCOL_VERSION,
    MessageAction,
)


def test_action_enum_covers_known_actions():
    """协议登记的动作全集（新增 action 必须同步在此登记）。"""
    assert MessageAction.PING == "ping"
    assert MessageAction.USERINFO == "userinfo"
    assert MessageAction.PUSH_MESSAGE == "push_message"
    assert MessageAction.CHAT_MESSAGE == "chat_message"
    assert MessageAction.TASK_LOG == "task_log"
    assert PROTOCOL_VERSION == 1


def test_outbound_frame_contains_version_and_common_fields():
    """出站帧统一携带 code/detail/timestamp/v，data/mid 按需出现。"""

    class Consumer(AsyncJsonWebsocket):
        def __init__(self):
            self.frames = []

        async def send_json(self, content, close=False):
            self.frames.append(content)

    consumer = Consumer()

    async def scenario():
        await consumer.send_base_json(
            MessageAction.TASK_LOG.value,
            {"offset": 0, "content": "x", "finished": True},
            mid="m-1",
        )

    async_to_sync(scenario)()
    frame = consumer.frames[0]
    assert frame["action"] == MessageAction.TASK_LOG.value
    assert frame["v"] == PROTOCOL_VERSION
    assert frame["code"] == 1000
    assert "timestamp" in frame
    assert frame["mid"] == "m-1"
    assert frame["data"] == {"offset": 0, "content": "x", "finished": True}


def test_unknown_action_falls_through_to_receive_json():
    """未登记动作保有扩展通道（receive_json 兜底），不应报错断连。"""
    received = {}
    import json

    class Consumer(AsyncJsonWebsocket):
        async def receive_json(self, action, data, content, **kwargs):
            received["action"] = action
            received["data"] = data

    consumer = Consumer()

    async def scenario():
        await consumer.receive(json.dumps({"action": "custom_biz", "data": {"k": 1}}))

    async_to_sync(scenario)()
    assert received == {"action": "custom_biz", "data": {"k": 1}}
