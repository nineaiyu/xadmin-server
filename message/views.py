#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""聊天室 REST（/api/chat/）。

- `GET  /api/chat/room`                 我的会话列表（公共 + AI 助手 + 私聊 + 群聊，含未读/在线态）
- `POST /api/chat/room/open-private`    开通（幂等复用）与目标用户的一对一私聊
- `POST /api/chat/room/create-group`    创建多人群聊（创建者为群主，成员含自己在内 ≥2 人）
- `POST /api/chat/room/{pk}/members`    群成员变更（add / remove，仅群主）
- `POST /api/chat/room/{pk}/rename`     群聊改名（仅群主）
- `POST /api/chat/room/{pk}/leave`      退出群聊（群主退出自动转让，最后一人退出解散）
- `GET  /api/chat/message`              历史游标分页（before_id 倒序拉取，响应内按时间正序）
- `POST /api/chat/message/{id}/recall`  撤回（仅本人、2 分钟内），广播双方同步
- `GET  /api/chat/contacts`             最近在线联系人（在线优先、按最近活跃排序）
- `GET  /api/chat/contacts/user-options` 群成员候选（关键字搜索，与联系人 list 权限同口径）
- `POST /api/chat/ai/message`           AI 助手提问（通用多轮；`/kb` 前缀走知识库 RAG）
- `POST /api/chat/ai/stream`            AI 助手流式提问（SSE：meta → delta* → done | error）

权限：菜单权限点（`list:ChatRoom` / `create:ChatRoom` / `createGroup:ChatRoom` /
`members:ChatRoom` / `rename:ChatRoom` / `leave:ChatRoom` / `list:ChatMessage` /
`recall:ChatMessage` / `list:ChatContact` / `ask:ChatRoom` / `stream:ChatRoom`），
页面沿用聊天室菜单授权；
AI 接口额外受 `AI_ASSISTANT_ENABLED` + 凭据完整性门禁（未启用返回可读 1001）。
消息内容一律文本（前端插值渲染，不 v-html；长度上限服务端强制）。
"""

from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema
from rest_framework.decorators import action
from rest_framework.viewsets import GenericViewSet

from common.core.response import ApiResponse
from common.drf.renders import SseRendererMixin, sse_response
from common.swagger.utils import get_default_response_schema
from message import ai as chat_ai
from message import chat as chat_service
from message.models import (
    RECALL_WINDOW_MINUTES,
    ChatMessage,
    ChatRoom,
)
from message.serializers import (
    ChatAiMessageSerializer,
    CreateGroupSerializer,
    GroupMembersSerializer,
    OpenPrivateRoomSerializer,
    RenameGroupSerializer,
)
from message.utils import push_room_event
from system.utils.user_options import search_user_options

# 历史分页默认/最大条数
HISTORY_DEFAULT_LIMIT = 20
HISTORY_MAX_LIMIT = 50


def _validation_detail(exc) -> str:
    return "; ".join(getattr(exc, "messages", None) or [str(exc)])


def _can_recall(message: ChatMessage, user) -> bool:
    if message.is_recalled or not message.sender_id or message.sender_id != user.pk:
        return False
    created = message.created_time or timezone.now()
    return timezone.now() - created <= timezone.timedelta(minutes=RECALL_WINDOW_MINUTES)


class ChatRoomViewSet(GenericViewSet):
    """聊天室会话（列表 / 私聊开通）"""

    queryset = ChatRoom.objects.all()

    @extend_schema(responses=get_default_response_schema())
    def list(self, request, *args, **kwargs):
        """我的会话列表：公共聊天室置顶，AI 助手按门禁显隐，私聊未读优先。"""
        ai_enabled = chat_ai.is_enabled()
        rooms = chat_service.list_user_rooms(request.user, ai_enabled=ai_enabled)
        return ApiResponse(
            data={
                "rooms": rooms,
                "ai_enabled": ai_enabled,
                "ai_command": chat_ai.KB_COMMAND,
                "ai_hint": chat_ai.markdown_hint(),
            }
        )

    @extend_schema(request=OpenPrivateRoomSerializer, responses=get_default_response_schema())
    @action(methods=["post"], detail=False, url_path="open-private")
    def open_private(self, request, *args, **kwargs):
        """开通私聊：`room_key` 幂等，重复调用返回同一会话（双端可同时发起）。"""
        from system.models import UserInfo

        serializer = OpenPrivateRoomSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        target = UserInfo.objects.filter(pk=serializer.validated_data["user_pk"], is_active=True).first()
        if target is None:
            return ApiResponse(code=1001, detail=_("User not found"))
        try:
            room = chat_service.get_or_create_private_room(request.user, target)
        except DjangoValidationError as exc:
            return ApiResponse(code=1001, detail=_validation_detail(exc))
        return ApiResponse(
            data=chat_service.room_to_dict(room, request.user, 0, chat_service.online_user_pks()),
            detail=_("Chat opened"),
        )

    @extend_schema(request=CreateGroupSerializer, responses=get_default_response_schema())
    @action(methods=["post"], detail=False, url_path="create-group")
    def create_group(self, request, *args, **kwargs):
        """创建多人群聊：成员（含创建者）至少 2 人，创建者即群主。"""
        serializer = CreateGroupSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            room = chat_service.create_group(
                request.user,
                serializer.validated_data["name"],
                serializer.validated_data["member_pks"],
            )
        except DjangoValidationError as exc:
            return ApiResponse(code=1001, detail=_validation_detail(exc))
        return ApiResponse(
            data=chat_service.room_to_dict(room, request.user, 0, chat_service.online_user_pks()),
            detail=_("Group created"),
        )

    @extend_schema(request=GroupMembersSerializer, responses=get_default_response_schema())
    @action(methods=["get", "post"], detail=True, url_path="members")
    def members(self, request, *args, **kwargs):
        """群成员：GET 返回完整成员列表（成员可见）；POST 变更（add / remove，仅群主）。

        GET+POST 共享同一权限码（登记见 permission_sync SHARED_METHOD_PATHS），
        前端以同一权限码驱动「查看成员 / 管理成员」入口。
        """
        if request.method == "GET":
            try:
                room = chat_service.group_room_or_deny(kwargs.get("pk"), request.user)
            except DjangoValidationError as exc:
                return ApiResponse(code=1001, detail=_validation_detail(exc))
            members = [chat_service.user_brief(row.user) for row in room.members.select_related("user")]
            return ApiResponse(data={"room": chat_service.room_to_dict(room, request.user), "members": members})
        serializer = GroupMembersSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            if serializer.validated_data.get("add"):
                chat_service.add_group_members(kwargs.get("pk"), request.user, serializer.validated_data["add"])
            if serializer.validated_data.get("remove"):
                chat_service.remove_group_members(kwargs.get("pk"), request.user, serializer.validated_data["remove"])
            room = chat_service.group_room_or_deny(kwargs.get("pk"), request.user)
        except DjangoValidationError as exc:
            return ApiResponse(code=1001, detail=_validation_detail(exc))
        return ApiResponse(data=chat_service.room_to_dict(room, request.user, 0, chat_service.online_user_pks()))

    @extend_schema(request=RenameGroupSerializer, responses=get_default_response_schema())
    @action(methods=["post"], detail=True, url_path="rename")
    def rename(self, request, *args, **kwargs):
        """群聊改名（仅群主）。"""
        serializer = RenameGroupSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            room = chat_service.rename_group(kwargs.get("pk"), request.user, serializer.validated_data["name"])
        except DjangoValidationError as exc:
            return ApiResponse(code=1001, detail=_validation_detail(exc))
        return ApiResponse(
            data=chat_service.room_to_dict(room, request.user, 0, chat_service.online_user_pks()),
            detail=_("Group renamed"),
        )

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["post"], detail=True, url_path="leave")
    def leave(self, request, *args, **kwargs):
        """退出群聊：群主退出自动转让给最早加入成员；最后一人退出即解散。"""
        try:
            room = chat_service.leave_group(kwargs.get("pk"), request.user)
        except DjangoValidationError as exc:
            return ApiResponse(code=1001, detail=_validation_detail(exc))
        return ApiResponse(
            data={"room_id": room.pk, "is_active": room.is_active},
            detail=_("Left the group"),
        )


class ChatMessageViewSet(GenericViewSet):
    """聊天室消息（历史游标分页 / 撤回）"""

    queryset = ChatMessage.objects.all()

    @extend_schema(responses=get_default_response_schema())
    def list(self, request, *args, **kwargs):
        """历史消息：`room` + 可选 `before_id`（倒序游标，响应按时间正序）+ `limit`。"""
        try:
            room = chat_service.accessible_room(request.query_params.get("room"), request.user)
        except DjangoValidationError as exc:
            return ApiResponse(code=1001, detail=_validation_detail(exc))
        try:
            limit = int(request.query_params.get("limit") or HISTORY_DEFAULT_LIMIT)
        except (TypeError, ValueError):
            limit = HISTORY_DEFAULT_LIMIT
        limit = max(1, min(limit, HISTORY_MAX_LIMIT))

        queryset = ChatMessage.objects.filter(room=room).select_related("room")
        before_id = request.query_params.get("before_id")
        if before_id:
            try:
                queryset = queryset.filter(id__lt=int(before_id))
            except (TypeError, ValueError):
                return ApiResponse(code=1001, detail=_("Invalid pagination cursor"))
        rows = list(queryset.order_by("-id")[: limit + 1])
        has_more = len(rows) > limit
        rows = rows[:limit]
        avatar_map = chat_service.sender_avatar_map(rows)

        messages = []
        for message in rows:
            payload = chat_service.message_payload(message, room=room, avatar_map=avatar_map)
            payload["can_recall"] = _can_recall(message, request.user)
            messages.append(payload)
        messages.reverse()  # 前端按时间正序渲染
        return ApiResponse(
            data={
                "results": messages,
                "has_more": has_more,
                "room": chat_service.room_to_dict(room, request.user, 0, chat_service.online_user_pks()),
            }
        )

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["post"], detail=True, url_path="recall")
    def recall(self, request, *args, **kwargs):
        """撤回消息：仅本人、2 分钟内；成功后向房间广播撤回事件（双方/多端同步）。"""
        try:
            message = chat_service.recall_message(request.user, kwargs.get("pk"))
        except DjangoValidationError as exc:
            return ApiResponse(code=1001, detail=_validation_detail(exc))
        room = ChatRoom.objects.filter(pk=message.room_id).first()
        payload = {
            "message_id": message.pk,
            "id": message.pk,
            "room_id": message.room_id,
            "operator_pk": request.user.pk,
        }
        if room is not None:
            push_room_event(room, payload, message_type="chat_recall")
        return ApiResponse(data=payload, detail=_("Message recalled"))


class ChatContactViewSet(GenericViewSet):
    """聊天室联系人（最近在线）"""

    queryset = ChatRoom.objects.none()

    @extend_schema(responses=get_default_response_schema())
    def list(self, request, *args, **kwargs):
        """最近在线联系人：在线优先 + 按会话最近活跃倒序（建私聊入口）。"""
        try:
            limit = int(request.query_params.get("limit") or chat_service.CONTACT_LIMIT)
        except (TypeError, ValueError):
            limit = chat_service.CONTACT_LIMIT
        limit = max(1, min(limit, chat_service.CONTACT_LIMIT))
        return ApiResponse(data={"results": chat_service.recent_contacts(request.user, limit)})

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["get"], detail=False, url_path="user-options")
    def user_options(self, request, *args, **kwargs):
        """群成员候选：按关键字搜索在用用户（≤20 条，仅 pk/用户名/昵称）。

        口径与选人控件同源（system/utils/user_options.py）；权限与该视图 list 权限
        同口径（common/core/permission.py 的 user-options 特例），无需新增权限点。
        """
        data = search_user_options(
            keyword=request.query_params.get("keyword", ""),
            pks=request.query_params.get("pks", ""),
        )
        return ApiResponse(data=data)


class ChatAiViewSet(SseRendererMixin, GenericViewSet):
    """聊天室 AI 助手（通用多轮 + `/kb` 知识库问答）"""

    queryset = ChatRoom.objects.none()

    #: 需要 SSE 协商的流式 action
    sse_actions = ("stream",)

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
                room, None, detail, message_type=ChatMessage.MessageType.SYSTEM, extra={"error": True, "mode": "chat"}
            )
            payload = chat_service.message_payload(fallback, room=room)
            push_room_event(room, payload)
            return ApiResponse(code=1001, detail=detail, data={"question": question_payload, "message": payload})

        reply, __ = chat_service.create_message(
            room, None, answer, message_type=ChatMessage.MessageType.AI, extra=extra
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
