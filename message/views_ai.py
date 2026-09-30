#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""聊天室 AI 助手视图（自 message/views.py 平移）。

AI 助手（通用多轮 + `/kb` 知识库问答）：class 与 REST/SSE 两个 action。
"""

from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema
from rest_framework.decorators import action
from rest_framework.viewsets import GenericViewSet

from common.core.response import ApiResponse
from common.core.throttle import AiThrottleMixin
from common.drf.renders import SseRendererMixin, sse_response
from common.swagger.utils import get_default_response_schema
from message import ai as chat_ai
from message import chat as chat_service
from message.models import ChatMessage, ChatRoom
from message.serializers import ChatAiMessageSerializer
from message.utils import push_room_event
from message.views import _validation_detail  # 聊天室视图共用校验文案助手（views 不反向依赖本模块）


class ChatAiViewSet(AiThrottleMixin, SseRendererMixin, GenericViewSet):
    """聊天室 AI 助手（通用多轮 + `/kb` 知识库问答）"""

    queryset = ChatRoom.objects.none()

    #: 需要 SSE 协商的流式 action
    sse_actions = ("stream",)

    #: 两个 action 均调用 LLM（含流式）：整类按对话类限流
    ai_chat_all = True

    @extend_schema(request=ChatAiMessageSerializer, responses=get_default_response_schema())
    @action(methods=["post"], detail=False, url_path="message")
    def message(self, request, *args, **kwargs):
        """提问：用户消息与 AI 回复双条落库并广播（刷新可续聊，附引用来源）。

        降级口径：LLM 失败/知识库无命中 → 落一条 system 消息（前端可见），
        REST 同时返回 code=1001 与可读 detail，不静默吞掉。
        """
        if not chat_ai.is_enabled():
            return ApiResponse(code=1001, detail=chat_ai.ai_gate_error())
        serializer = ChatAiMessageSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        content = serializer.validated_data["content"]

        room_id = serializer.validated_data.get("room_id")
        if room_id:
            try:
                room = chat_service.accessible_room(room_id, request.user)
            except DjangoValidationError as exc:
                return ApiResponse(code=1001, detail=_validation_detail(exc))
            if room.room_type != ChatRoom.RoomType.AI:
                return ApiResponse(code=1001, detail=_("Not an AI assistant chat"))
        else:
            room = chat_service.get_or_create_ai_room(request.user)

        question, __ = chat_service.create_message(
            room,
            request.user,
            content,
            client_msg_id=serializer.validated_data.get("client_msg_id") or "",
        )
        question_payload = chat_service.message_payload(question, room=room, sender=request.user)
        push_room_event(room, question_payload)

        try:
            answer, extra, mode = chat_ai.ai_reply_content(room, content)
        except DjangoValidationError as exc:
            detail = _validation_detail(exc)
            fallback, __ = chat_service.create_message(
                room,
                None,
                detail,
                message_type=ChatMessage.MessageType.SYSTEM,  # type: ignore[arg-type]  # Choices 元类
                extra={"error": True, "mode": "chat"},
            )
            payload = chat_service.message_payload(fallback, room=room)
            push_room_event(room, payload)
            return ApiResponse(code=1001, detail=detail, data={"question": question_payload, "message": payload})

        reply, __ = chat_service.create_message(
            room,
            None,
            answer,
            message_type=ChatMessage.MessageType.AI,  # type: ignore[arg-type]  # 同上
            extra=extra,
        )
        payload = chat_service.message_payload(reply, room=room)
        push_room_event(room, payload)
        return ApiResponse(data={"mode": mode, "question": question_payload, "message": payload})

    @extend_schema(request=ChatAiMessageSerializer, responses=get_default_response_schema())
    @action(methods=["post"], detail=False, url_path="stream")
    def stream(self, request, *args, **kwargs):
        """流式提问（二期）：`text/event-stream`，事件序 meta → delta* → done | error。

        事件载荷均为 JSON（``data: {...}\\n\\n``）；业务落库与 WS 广播与 `message` 同口径
        （前端实时气泡 + 多端同步），失败带内下发光 error 事件（头已发出，不能再改状态码）。
        非 SSE 错误（门禁/参数/房间）仍走 JSON 1001，前端按普通接口错误提示；
        这里显式声明 content_type，避免被 EventStreamRenderer 覆盖成 SSE 类型。
        """
        if not chat_ai.is_enabled():
            return ApiResponse(code=1001, detail=chat_ai.ai_gate_error(), content_type="application/json")
        serializer = ChatAiMessageSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        content = serializer.validated_data["content"]

        room_id = serializer.validated_data.get("room_id")
        if room_id:
            try:
                room = chat_service.accessible_room(room_id, request.user)
            except DjangoValidationError as exc:
                return ApiResponse(code=1001, detail=_validation_detail(exc), content_type="application/json")
            if room.room_type != ChatRoom.RoomType.AI:
                return ApiResponse(code=1001, detail=_("Not an AI assistant chat"), content_type="application/json")
        else:
            room = chat_service.get_or_create_ai_room(request.user)

        question, __ = chat_service.create_message(
            room,
            request.user,
            content,
            client_msg_id=serializer.validated_data.get("client_msg_id") or "",
        )
        question_payload = chat_service.message_payload(question, room=room, sender=request.user)
        push_room_event(room, question_payload)

        # SSE 响应装配走公共件（异步帧迭代器逐帧 flush + 关闭代理缓冲）
        return sse_response(chat_ai.ai_stream_events(room, content, question_payload))
