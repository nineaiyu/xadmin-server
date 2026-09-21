# -*- coding: utf-8 -*-
"""聊天室 AI 流式提问集成测试（二期，/api/chat/ai/stream SSE）。

覆盖：事件序（meta → delta* → done）、`/kb` 单段增量带引用来源、门禁 JSON 1001、
LLM 全程失败降级 system 消息 + error 事件、已有增量后中断保留部分回答、
权限门控（无 stream:ChatRoom 权限点 fail-closed 403）。
"""

import json as jsonlib

import pytest
from rest_framework.test import APIClient

from message import chat as chat_service
from message.models import ChatMessage, ChatRoom

pytestmark = pytest.mark.django_db


def _iter_stream(response):
    """流式响应的字节块（测试用同步收集）。

    ASGI 实时性由 response.is_async 守护测试覆盖；此处仅需完整消费帧序列，
    异步迭代器统一经 async_to_sync 收集（同步生成器直接返回）。
    """
    content = response.streaming_content
    if getattr(response, "is_async", False):
        from asgiref.sync import async_to_sync

        async def gather():
            return [chunk async for chunk in content]

        return async_to_sync(gather)()
    return content


STREAM_URL = "/api/chat/ai/stream"


def parse_sse(response) -> list:
    """Django 测试流式响应 → [(event, data_dict)]（按空行切帧）。"""
    frames = []
    buffer = b""
    for chunk in _iter_stream(response):
        buffer += chunk if isinstance(chunk, bytes) else str(chunk).encode()
        while b"\n\n" in buffer:
            raw, buffer = buffer.split(b"\n\n", 1)
            event, data = "message", ""
            for line in raw.decode().splitlines():
                if line.startswith("event:"):
                    event = line[len("event:") :].strip()
                elif line.startswith("data:"):
                    data = line[len("data:") :].strip()
            frames.append((event, jsonlib.loads(data) if data else {}))
    return frames


class _FakeStreamResponse:
    def __init__(self, lines, status_code=200):
        self._lines = lines
        self.status_code = status_code
        self.text = "provider rejected"

    def iter_lines(self, decode_unicode=True):
        yield from self._lines


class StreamStubLLM:
    """替换 ChatCompletionsClient._client：post 返回 OpenAI SSE 帧序列，走真实 chat_stream 解析。

    reasonings 先于 deltas 产出（思考型模型的 reasoning_content 帧）。
    """

    def __init__(self, deltas=("你好", "，我是", " AI 助手"), reasonings=()):
        self.deltas = list(deltas)
        self.reasonings = list(reasonings)
        self.requests = []

    def post(self, url, json=None, headers=None, timeout=None, stream=False):
        # 注意：形参 json 是请求载荷（requests 签名），模块 json 用 jsonlib 别名
        self.requests.append({"url": url, "json": json})
        lines = [
            f"data: {jsonlib.dumps({'choices': [{'delta': {'reasoning_content': item}}]}, ensure_ascii=False)}"
            for item in self.reasonings
        ]
        lines += [
            f"data: {jsonlib.dumps({'choices': [{'delta': {'content': delta}}]}, ensure_ascii=False)}"
            for delta in self.deltas
        ]
        lines.append("data: [DONE]")
        lines.append("")
        return _FakeStreamResponse(lines)


@pytest.fixture
def stream_stub(monkeypatch):
    stub = StreamStubLLM()
    monkeypatch.setattr("common.sdk.ai.chat.ChatCompletionsClient._client", lambda self: stub)
    return stub


@pytest.fixture
def alice(db):
    from system.models import UserInfo

    return UserInfo.objects.create_user(username="alice", password="Test@123456", nickname="爱丽丝")


@pytest.fixture
def bob(db):
    from system.models import UserInfo

    return UserInfo.objects.create_user(username="bob", password="Test@123456", nickname="鲍勃")


@pytest.fixture
def ai_enabled(settings):
    settings.AI_ASSISTANT_ENABLED = True
    settings.AI_BASE_URL = "https://ai.example.com/v1"
    settings.AI_API_KEY = "sk-test"
    settings.AI_MODEL = "test-model"
    return settings


class TestStream:
    def test_event_order_and_persistence(self, auth_client, superuser, ai_enabled, stream_stub):
        response = auth_client.post(STREAM_URL, {"content": "介绍一下系统"}, format="json")
        assert response.status_code == 200, response.data
        assert response["Content-Type"] == "text/event-stream"
        assert response["X-Accel-Buffering"] == "no"

        frames = parse_sse(response)
        assert [event for event, __ in frames] == ["meta", "delta", "delta", "delta", "done"]
        assert frames[0][1]["question"]["content"] == "介绍一下系统"
        assert "".join(data["delta"] for __, data in frames[1:-1]) == "你好，我是 AI 助手"
        assert frames[-1][1]["mode"] == "chat"
        assert frames[-1][1]["message"]["message_type"] == ChatMessage.MessageType.AI

        room = ChatRoom.objects.get(room_key=f"ai:{superuser.pk}")
        rows = list(ChatMessage.objects.filter(room=room).order_by("id"))
        assert [row.message_type for row in rows] == [ChatMessage.MessageType.TEXT, ChatMessage.MessageType.AI]
        assert rows[1].content == "你好，我是 AI 助手"

    def test_llm_request_uses_stream_flag(self, auth_client, ai_enabled, stream_stub):
        response = auth_client.post(STREAM_URL, {"content": "你好"}, format="json")
        # 流式响应是惰性生成器：消费帧才会真正触发 LLM 请求
        parse_sse(response)
        assert stream_stub.requests[0]["json"]["stream"] is True

    def test_stream_uses_async_iterator(self, auth_client, superuser, ai_enabled, stream_stub):
        """ASGI 实时性守护：streaming_content 必须是异步迭代器。

        回归背景：同步生成器会被 Django __aiter__ 的兜底分支
        （``sync_to_async(list)``）整体跑完再一次性 yield，SSE 失去实时性。
        """
        response = auth_client.post(STREAM_URL, {"content": "你好"}, format="json")
        assert response.is_async is True

    def test_reasoning_events_forwarded_and_stored(self, auth_client, superuser, ai_enabled, monkeypatch):
        """思考型模型：reasoning 事件先于 delta 实时转发，并落库 extra.reasoning（回看）。"""
        stub = StreamStubLLM(deltas=("答案",), reasonings=("先想", "再看"))
        monkeypatch.setattr("common.sdk.ai.chat.ChatCompletionsClient._client", lambda self: stub)

        frames = parse_sse(auth_client.post(STREAM_URL, {"content": "介绍一下系统"}, format="json"))
        assert [event for event, __ in frames] == ["meta", "reasoning", "reasoning", "delta", "done"]
        assert "".join(data["delta"] for event, data in frames if event == "reasoning") == "先想再看"

        reply = ChatMessage.objects.filter(message_type=ChatMessage.MessageType.AI).first()
        assert reply.extra["reasoning"] == "先想再看"
        assert reply.extra.get("no_answer") is None

    def test_only_reasoning_keeps_answer_placeholder(self, auth_client, superuser, ai_enabled, monkeypatch):
        """只有思考没有回答：思考保留（extra.reasoning）+ 内容落可读提示（extra.no_answer），不落降级。"""
        from django.utils.translation import gettext as _t

        stub = StreamStubLLM(deltas=(), reasonings=("想了很久",))
        monkeypatch.setattr("common.sdk.ai.chat.ChatCompletionsClient._client", lambda self: stub)

        frames = parse_sse(auth_client.post(STREAM_URL, {"content": "难问题"}, format="json"))
        assert frames[-1][0] == "done"
        reply = ChatMessage.objects.filter(message_type=ChatMessage.MessageType.AI).first()
        assert reply is not None
        assert reply.extra["no_answer"] is True
        assert reply.extra["reasoning"] == "想了很久"
        assert reply.content == _t("The model did not provide a final answer; please retry or switch models")
        assert not ChatMessage.objects.filter(message_type=ChatMessage.MessageType.SYSTEM).exists()

    def test_kb_single_delta_with_sources(self, auth_client, ai_enabled, monkeypatch):
        monkeypatch.setattr(
            "system.utils.ai.ask",
            lambda question: {
                "answer": "根据文档，重置密码见 [1]。",
                "sources": [{"title": "手册", "path": "upload/manual.md", "chunk_index": 0}],
            },
        )
        response = auth_client.post(STREAM_URL, {"content": "/kb 如何重置密码"}, format="json")
        frames = parse_sse(response)
        assert [event for event, __ in frames] == ["meta", "delta", "done"]
        assert frames[1][1]["delta"] == "根据文档，重置密码见 [1]。"
        assert frames[2][1]["message"]["extra"]["sources"][0]["path"] == "upload/manual.md"

    def test_failure_without_delta_degrades_to_system_message(self, auth_client, superuser, ai_enabled, monkeypatch):
        from common.sdk.ai.chat import AiSdkError

        monkeypatch.setattr(
            "common.sdk.ai.chat.ChatCompletionsClient.chat_stream",
            lambda self, messages, temperature=0.2: (_ for _ in ()).throw(AiSdkError("provider down")),
        )
        response = auth_client.post(STREAM_URL, {"content": "你好"}, format="json")
        frames = parse_sse(response)
        assert frames[-1][0] == "error"
        fallback = ChatMessage.objects.filter(message_type=ChatMessage.MessageType.SYSTEM).first()
        assert fallback is not None
        assert fallback.extra.get("error") is True
        assert frames[-1][1]["message"]["id"] == fallback.pk
        assert not ChatMessage.objects.filter(message_type=ChatMessage.MessageType.AI).exists()

    def test_interruption_after_delta_keeps_partial_answer(self, auth_client, superuser, ai_enabled, monkeypatch):
        from common.sdk.ai.chat import AiSdkError

        def broken_stream(self, messages, temperature=0.2):
            yield {"type": "content", "text": "部分"}
            yield {"type": "content", "text": "回答"}
            raise AiSdkError("stream interrupted")

        monkeypatch.setattr("common.sdk.ai.chat.ChatCompletionsClient.chat_stream", broken_stream)
        response = auth_client.post(STREAM_URL, {"content": "写个长答案"}, format="json")
        frames = parse_sse(response)
        assert frames[-1][0] == "done"
        reply = ChatMessage.objects.filter(message_type=ChatMessage.MessageType.AI).first()
        assert reply is not None
        assert reply.content == "部分回答"
        from django.utils.translation import gettext as _t

        assert reply.extra.get("partial") == str(_t("AI service is temporarily unavailable"))

    def test_browser_accept_header_negotiation(self, auth_client, superuser, ai_enabled, stream_stub):
        """回归：浏览器 fetch 携带 Accept: text/event-stream 时不得 406。

        APIClient 默认 Accept: */* 会命中 JSONRenderer，历史上该缺陷只在真实浏览器
        出现（AI 流式链路在浏览器侧整体 406、不可用），需要显式以 SSE Accept 回归。
        """
        response = auth_client.post(
            STREAM_URL, {"content": "介绍一下系统"}, format="json", HTTP_ACCEPT="text/event-stream"
        )
        assert response.status_code == 200
        assert response["Content-Type"] == "text/event-stream"
        frames = parse_sse(response)
        assert frames[-1][0] == "done"

    def test_gate_json_content_type_with_sse_accept(self, auth_client, superuser, settings):
        """门禁错误保持 JSON 响应（即便 Accept 请求 SSE），前端按普通接口错误提示。"""
        settings.AI_ASSISTANT_ENABLED = False
        response = auth_client.post(STREAM_URL, {"content": "hi"}, format="json", HTTP_ACCEPT="text/event-stream")
        assert response.status_code == 200
        assert response["Content-Type"] == "application/json"
        assert response.data["code"] == 1001

    def test_gate_disabled_returns_json(self, auth_client, superuser, settings):
        settings.AI_ASSISTANT_ENABLED = False
        response = auth_client.post(STREAM_URL, {"content": "你好"}, format="json")
        assert response.json()["code"] == 1001
        assert not ChatMessage.objects.exists()

    def test_non_ai_room_rejected(self, auth_client, superuser, bob, ai_enabled, stream_stub):
        room = chat_service.get_or_create_private_room(superuser, bob)
        response = auth_client.post(STREAM_URL, {"content": "hello", "room_id": room.pk}, format="json")
        assert response.json()["code"] == 1001

    def test_permission_gate_fail_closed(self, db, alice, ai_enabled, stream_stub):
        """未授予 stream:ChatRoom 权限点的普通用户 fail-closed 403。"""
        client = APIClient()
        client.force_authenticate(alice)
        response = client.post(STREAM_URL, {"content": "你好"}, format="json")
        assert response.status_code == 403
