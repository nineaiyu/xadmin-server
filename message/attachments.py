#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""聊天附件（图片 / 文件消息）：种类判定、归属校验、载荷构造与受鉴权取件。

设计要点：
- **只存引用**：`ChatMessage.attachment` 外键指向 system.UploadFile，`extra["file"]`
  存渲染用元信息快照（文件名 / 大小 / MIME / 分类 / 种类）；取件 URL 由消息 pk 派生
  （不在库里存路径，避免迁移/域名变化后失效）；
- **上传复用文件落库内核**（system.utils.upload_store：扩展名黑名单/白名单、大小、
  配额、md5 去重、分类、存储），聊天侧只做「种类匹配 + 归属校验」；
- **归属 fail-closed**：只能引用本人上传的记录（他人文件 pk 一律拒绝）；
- **临时态转正**：附件随消息落库时把 `is_tmp` 置 False（否则每日临时文件清理会把
  已发消息的附件物理删除）；转正后受 `UploadFile.has_business_reference` 保护
  （消息外键即业务引用，保留期清理会整体跳过）；
- **取件鉴权**：仅消息所在房间的可访问者可读（与 chat_service.accessible_room 同源），
  撤回后的消息一律不可取；不做逐次访问审计（图片气泡每次渲染都会取缩略图，
  逐条落审计会淹没日志；鉴权口径本身已限定到房间成员）。
"""

from urllib.parse import quote

from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import FileResponse
from django.utils.translation import gettext_lazy as _

from common.storage import storage_exists, storage_open
from message.models import ATTACHMENT_MESSAGE_TYPES, ChatMessage  # noqa: F401 再导出消息类型常量
from system.services import UploadFile
from system.utils.preview import (
    KIND_IMAGE,
    SIZE_PREVIEW,
    SIZE_THUMB,
    ensure_image_cache,
    preview_kind,
    touch_preview_cache,
)

#: 附件种类（与上传分类 upload_category 的 image 值同口径）
KIND_FILE = "file"

# 文件取件路径（受鉴权；与权限点 file:ChatMessage 的 path 同源）
FILE_URL_TEMPLATE = "/api/chat/message/{pk}/file"

# 附件消息允许携带的内容长度上限（文件名/说明文案，与 AI 消息同口径放宽）
MAX_ATTACHMENT_CAPTION_LENGTH = 2000


def attachment_kind(upload) -> str:
    """附件种类：图片（与在线预览判定同源）→ image，其余 → file。"""
    return KIND_IMAGE if preview_kind(upload) == KIND_IMAGE else KIND_FILE


def attachment_extra(upload) -> dict:
    """渲染用附件元信息快照（落库进 extra["file"]；取件 URL 在载荷层按消息 pk 派生）。"""
    return {
        "pk": str(upload.pk),
        "filename": upload.filename,
        "filesize": upload.filesize,
        "mime_type": upload.mime_type or "",
        "category": upload.category or "",
        "kind": attachment_kind(upload),
    }


def attachment_file_url(message_pk) -> str:
    """受鉴权取件地址（图片 img / 文件下载共用同一端点）。"""
    return FILE_URL_TEMPLATE.format(pk=message_pk)


def resolve_sender_attachment(file_pk, sender) -> UploadFile:
    """取发送者本人的上传件（fail-closed）：不存在 / 非本人 / 非上传件一律拒绝。

    拒绝文案与「房间不存在」同口径（不区分具体原因，避免探测他人文件是否存在）。
    """
    upload = UploadFile.objects.filter(pk=file_pk, is_upload=True).first()
    if upload is None or upload.creator_id != getattr(sender, "pk", None):
        raise DjangoValidationError(_("Attachment not found"))
    return upload


def validate_attachment_kind(upload, message_type: str) -> None:
    """消息类型与附件种类匹配：图片消息只接受图片附件（文件消息不限）。"""
    if message_type == ChatMessage.MessageType.IMAGE and attachment_kind(upload) != KIND_IMAGE:
        raise DjangoValidationError(_("Only image files can be sent as image messages"))


def validate_upload_kind(upload, kind: str) -> bool:
    """上传端点的种类门槛：`kind=image` 时必须确为图片（其余 kind 不做限制）。"""
    return not (kind == KIND_IMAGE and attachment_kind(upload) != KIND_IMAGE)


def mark_attachment_used(upload) -> None:
    """附件转正：临时上传件随消息落库即转正式件，交给保留期策略管理。"""
    if upload is not None and upload.is_tmp:
        upload.is_tmp = False
        upload.save(update_fields=["is_tmp", "updated_time"])


def attachment_response(message, request):
    """受鉴权取件响应。

    - 图片：`?size=thumb|preview` 取缩略图/预览缓存（JPEG，inline，浏览器直接渲染）；
    - 其余：按附件下载（Content-Disposition: attachment），对象存储经 storage 原语读取。
    """
    upload = message.attachment
    if upload is None:
        return None
    size = str(request.query_params.get("size") or SIZE_THUMB).lower()
    if attachment_kind(upload) == KIND_IMAGE:
        if size not in (SIZE_THUMB, SIZE_PREVIEW):
            size = SIZE_THUMB
        cache_path = ensure_image_cache(upload, size)
        if not cache_path:
            return None
        touch_preview_cache(cache_path)
        response = FileResponse(open(cache_path, "rb"), as_attachment=False, content_type="image/jpeg")
        response["Content-Disposition"] = "inline; filename*=UTF-8''{}".format(quote(upload.filename or ""))
        return response
    name = getattr(upload.filepath, "name", "") if upload.filepath else ""
    if not name or not storage_exists(name):
        return None
    response = FileResponse(
        storage_open(name, "rb"),
        as_attachment=True,
        content_type=upload.mime_type or "application/octet-stream",
    )
    response["Content-Disposition"] = "attachment; filename*=UTF-8''{}".format(quote(upload.filename or ""))
    return response


def attachment_payload(message) -> dict | None:
    """消息载荷里的附件渲染信息：`{...元信息, url, missing}`。

    - `url` 由消息 pk 派生（历史消息/广播载荷一致）；
    - `missing=True` 表示附件记录已被清理（外键 SET_NULL）或消息撤回，
      前端据此渲染「附件已失效」占位而非坏链。
    """
    if message.message_type not in ATTACHMENT_MESSAGE_TYPES:
        return None
    info = dict((message.extra or {}).get("file") or {})
    if message.is_recalled or message.attachment_id is None:
        return {**info, "url": "", "missing": True}
    return {**info, "url": attachment_file_url(message.pk), "missing": False}
