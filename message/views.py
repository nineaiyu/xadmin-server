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
- `POST /api/chat/message/upload`       附件上传（图片/音视频/文件消息共用；复用文件中心安全策略）
- `GET  /api/chat/message/{id}/file`    附件取件（受鉴权：仅房间可访问者，图片支持 ?size=，音视频 inline）
- `POST /api/chat/message/{id}/recall`  撤回（仅本人、2 分钟内），广播双方同步
- `GET  /api/chat/contacts`             最近在线联系人（在线优先、按最近活跃排序）
- `GET  /api/chat/contacts/user-options` 群成员候选（关键字搜索，与联系人 list 权限同口径）
- `POST /api/chat/ai/message`           AI 助手提问（通用多轮；`/kb` 前缀走知识库 RAG）
- `POST /api/chat/ai/stream`            AI 助手流式提问（SSE：meta → delta* → done | error）

附件消息（image / video / audio / file）经 WS 上行 `chat_message{room_id, message_type, file_pk}` 发送：
`file_pk` 先由上传端点取得，服务端做归属校验（只能引用本人上传的文件）并落库引用。

权限：菜单权限点（`list:ChatRoom` / `create:ChatRoom` / `createGroup:ChatRoom` /
`members:ChatRoom` / `rename:ChatRoom` / `leave:ChatRoom` / `list:ChatMessage` /
`upload:ChatMessage` / `file:ChatMessage` / `recall:ChatMessage` / `list:ChatContact` /
`ask:ChatRoom` / `stream:ChatRoom`），页面沿用聊天室菜单授权；
AI 接口额外受 `AI_ASSISTANT_ENABLED` + 凭据完整性门禁（未启用返回可读 1001）。
消息内容一律文本（前端插值渲染，不 v-html；长度上限服务端强制）。
"""

from typing import Any

from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema
from rest_framework.decorators import action
from rest_framework.parsers import MultiPartParser
from rest_framework.viewsets import GenericViewSet

from common.core.response import ApiResponse
from common.core.throttle import UploadThrottle
from common.swagger.utils import get_default_response_schema
from common.utils import get_logger
from identity.utils.user_options import search_user_options
from message import ai as chat_ai
from message import chat as chat_service
from message.attachments import attachment_response
from message.models import ChatMessage, ChatRoom
from message.serializers import (
    CreateGroupSerializer,
    GroupMembersSerializer,
    OpenPrivateRoomSerializer,
    RenameGroupSerializer,
)
from message.utils import broadcast_message_recall

logger = get_logger(__name__)

# 历史分页默认/最大条数
HISTORY_DEFAULT_LIMIT = 20
HISTORY_MAX_LIMIT = 50


def _validation_detail(exc: Any) -> str:
    return "; ".join(getattr(exc, "messages", None) or [str(exc)])


class ChatRoomViewSet(GenericViewSet):
    """聊天室会话（列表 / 私聊开通）"""

    queryset = ChatRoom.objects.all()

    @extend_schema(responses=get_default_response_schema())
    def list(self, request: Any, *args: Any, **kwargs: Any) -> Any:
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
    @action(methods=["post"], detail=False, url_path="open-private")  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def open_private(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """开通私聊：`room_key` 幂等，重复调用返回同一会话（双端可同时发起）。"""
        serializer = OpenPrivateRoomSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            room = chat_service.get_or_create_private_room_by_pk(request.user, serializer.validated_data["user_pk"])
        except DjangoValidationError as exc:
            return ApiResponse(code=1001, detail=_validation_detail(exc))
        return ApiResponse(
            data=chat_service.room_to_dict(room, request.user, 0, chat_service.online_user_pks()),
            detail=_("Chat opened"),
        )

    @extend_schema(request=CreateGroupSerializer, responses=get_default_response_schema())
    @action(methods=["post"], detail=False, url_path="create-group")  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def create_group(self, request: Any, *args: Any, **kwargs: Any) -> Any:
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
    @action(methods=["get", "post"], detail=True, url_path="members")  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def members(self, request: Any, *args: Any, **kwargs: Any) -> Any:
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
    @action(methods=["post"], detail=True, url_path="rename")  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def rename(self, request: Any, *args: Any, **kwargs: Any) -> Any:
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
    @action(methods=["post"], detail=True, url_path="leave")  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def leave(self, request: Any, *args: Any, **kwargs: Any) -> Any:
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
    def list(self, request: Any, *args: Any, **kwargs: Any) -> Any:
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
        before_id = request.query_params.get("before_id")
        if before_id:
            try:
                int(before_id)
            except (TypeError, ValueError):
                return ApiResponse(code=1001, detail=_("Invalid pagination cursor"))

        return ApiResponse(data=chat_service.history_messages(room, request.user, before_id=before_id, limit=limit))

    @extend_schema(responses=get_default_response_schema())
    @action(  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
        methods=["post"],
        detail=False,
        url_path="upload",
        throttle_classes=[UploadThrottle],
        parser_classes=(MultiPartParser,),
    )
    def upload(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """聊天附件上传（图片/音视频/文件消息共用）。

        复用文件中心的安全策略与落库内核（扩展名黑名单/白名单、大小上限、配额、
        md5 去重、自动分类、存储适配）；落库为临时件——发送消息时由服务端转正，
        未发送的临时件由每日临时文件清理回收，不长期占用存储。
        `kind` 白名单 image|video|audio|file 且必须与真实种类一致（不匹配即删记录
        返回 1001，防伪造 kind 让前端按错误种类渲染气泡）。
        """
        file_obj = (request.FILES.getlist("file") or [None])[0]
        if file_obj is None:
            return ApiResponse(code=1001, detail=_("No file uploaded"))
        try:
            data = chat_service.store_message_attachment(request.user, file_obj, request.data.get("kind"), request)
        except chat_service.AttachmentUploadError as exc:
            return ApiResponse(code=exc.code, detail=exc.detail)
        return ApiResponse(data=data, detail=_("Upload successful"))

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["get"], detail=True, url_path="file")  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def file(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """附件取件（受鉴权）：仅消息所在房间的可访问者可读；撤回/附件失效返回 1001。

        图片支持 `?size=thumb|preview`（缩略图/预览缓存，inline）；音/视频按真实
        MIME inline 返回（浏览器原生播放）；其余类型按附件下载。
        """
        try:
            message = chat_service.get_attachment_message(kwargs.get("pk"), request.user)
        except DjangoValidationError as exc:
            return ApiResponse(code=1001, detail=_validation_detail(exc))
        response = attachment_response(message, request)
        if response is None:
            return ApiResponse(code=1001, detail=_("File not found"))
        return response

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["post"], detail=True, url_path="recall")  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def recall(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """撤回消息：仅本人、2 分钟内；成功后向房间广播撤回事件（双方/多端同步）。"""
        try:
            message = chat_service.recall_message(request.user, kwargs.get("pk"))
        except DjangoValidationError as exc:
            return ApiResponse(code=1001, detail=_validation_detail(exc))
        payload = broadcast_message_recall(message, request.user.pk)
        return ApiResponse(data=payload, detail=_("Message recalled"))


class ChatContactViewSet(GenericViewSet):
    """聊天室联系人（最近在线）"""

    queryset = ChatRoom.objects.none()

    @extend_schema(responses=get_default_response_schema())
    def list(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """最近在线联系人：在线优先 + 按会话最近活跃倒序（建私聊入口）。"""
        try:
            limit = int(request.query_params.get("limit") or chat_service.CONTACT_LIMIT)
        except (TypeError, ValueError):
            limit = chat_service.CONTACT_LIMIT
        limit = max(1, min(limit, chat_service.CONTACT_LIMIT))
        return ApiResponse(data={"results": chat_service.recent_contacts(request.user, limit)})

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["get"], detail=False, url_path="user-options")  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def user_options(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """群成员候选：按关键字搜索在用用户（≤20 条，仅 pk/用户名/昵称）。

        口径与选人控件同源（identity/utils/user_options.py）；权限与该视图 list 权限
        同口径（packages/xadmin-common/common/core/permission.py 的 user-options 特例），无需新增权限点。
        """
        data = search_user_options(
            keyword=request.query_params.get("keyword", ""),
            pks=request.query_params.get("pks", ""),
        )
        return ApiResponse(data=data)
