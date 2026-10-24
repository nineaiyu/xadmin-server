#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""聊天附件（图片 / 音视频 / 文件消息）：种类判定、归属校验、载荷构造与受鉴权取件。

设计要点：
- **只存引用**：`ChatMessage.attachment` 外键指向 file.UploadFile，`extra["file"]`
  存渲染用元信息快照（文件名 / 大小 / MIME / 分类 / 种类）；取件 URL 由消息 pk 派生
  （不在库里存路径，避免迁移/域名变化后失效）；
- **上传复用文件落库内核**（file.utils.upload_store：扩展名黑名单/白名单、大小、
  配额、md5 去重、分类、存储），聊天侧只做「种类匹配 + 归属校验」；
- **归属 fail-closed**：只能引用本人上传的记录（他人文件 pk 一律拒绝）；
- **临时态转正**：附件随消息落库时把 `is_tmp` 置 False（否则每日临时文件清理会把
  已发消息的附件物理删除）；转正后受 `UploadFile.has_business_reference` 保护
  （消息外键即业务引用，保留期清理会整体跳过）；
- **取件鉴权**：仅消息所在房间的可访问者可读（与 chat_service.accessible_room 同源），
  撤回后的消息一律不可取；不做逐次访问审计（图片气泡每次渲染都会取缩略图，
  逐条落审计会淹没日志；鉴权口径本身已限定到房间成员）。
"""

from typing import Any
from urllib.parse import quote

from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import FileResponse
from django.utils.translation import gettext_lazy as _

from common.storage import storage_exists, storage_open
from common.utils import get_logger
from file.services import UploadFile
from file.utils.file_audit import log_file_access
from file.utils.preview import (
    KIND_IMAGE,
    SIZE_PREVIEW,
    SIZE_THUMB,
    ensure_image_cache,
    preview_kind,
    touch_preview_cache,
)
from file.utils.upload_category import CATEGORY_AUDIO, CATEGORY_VIDEO, guess_upload_category
from file.utils.upload_store import (
    UploadError,
    check_upload_limits,
    invalidate_upload_stats_cache,
    store_upload_file,
)
from message.models import ATTACHMENT_MESSAGE_TYPES as ATTACHMENT_MESSAGE_TYPES  # 再导出消息类型常量
from message.models import ChatMessage

logger = get_logger(__name__)

#: 附件种类（image 与在线预览判定同口径；video/audio 与上传分类同口径）
KIND_FILE = "file"
KIND_VIDEO = "video"
KIND_AUDIO = "audio"

#: 上传端点 kind 白名单（与附件消息类型集合同源；kind=file 对实际种类不限）
UPLOAD_KINDS = (KIND_IMAGE, KIND_VIDEO, KIND_AUDIO, KIND_FILE)

# 文件取件路径（受鉴权；与权限点 file:ChatMessage 的 path 同源）
FILE_URL_TEMPLATE = "/api/chat/message/{pk}/file"

# 附件消息允许携带的内容长度上限（文件名/说明文案，与 AI 消息同口径放宽）
MAX_ATTACHMENT_CAPTION_LENGTH = 2000


def attachment_kind(upload: Any) -> str:
    """附件种类：图片 → image，音/视频 → video/audio，其余 → file。

    - 图片沿用在线预览判定（存量口径零漂移：仅按 MIME 前缀判图，不引入扩展名
      兜底，避免 svg 等无 MIME 记录从「按文件下载」漂移成「缩略图渲染失败」）；
    - 音/视频复用上传分类的公开判定（file.utils.upload_category：MIME 前缀优先、
      扩展名兜底），不在本模块重复维护扩展名表；
    - pdf/office/压缩包/未知一律 file（沿用附件下载语义）。
    """
    if preview_kind(upload) == KIND_IMAGE:
        return KIND_IMAGE
    category = guess_upload_category(upload.filename, upload.mime_type)
    if category == CATEGORY_VIDEO:
        return KIND_VIDEO
    if category == CATEGORY_AUDIO:
        return KIND_AUDIO
    return KIND_FILE


def attachment_extra(upload: Any) -> dict[str, Any]:
    """渲染用附件元信息快照（落库进 extra["file"]；取件 URL 在载荷层按消息 pk 派生）。"""
    return {
        "pk": str(upload.pk),
        "filename": upload.filename,
        "filesize": upload.filesize,
        "mime_type": upload.mime_type or "",
        "category": upload.category or "",
        "kind": attachment_kind(upload),
    }


def attachment_file_url(message_pk: Any) -> str:
    """受鉴权取件地址（图片 img / 文件下载共用同一端点）。"""
    return FILE_URL_TEMPLATE.format(pk=message_pk)


def resolve_sender_attachment(file_pk: Any, sender: Any) -> UploadFile:
    """取发送者本人的上传件（fail-closed）：不存在 / 非本人 / 非上传件一律拒绝。

    拒绝文案与「房间不存在」同口径（不区分具体原因，避免探测他人文件是否存在）。
    """
    upload = UploadFile.objects.filter(pk=file_pk, is_upload=True).first()
    if upload is None or upload.creator_id != getattr(sender, "pk", None):
        raise DjangoValidationError(_("Attachment not found"))
    return upload


#: 消息类型 → 允许的附件种类（文件消息对实际种类不限）。
#: 经 str() 取 Choices 成员的值：mypy 的 Django 插件把成员类型推成 tuple，
#: 直接作 dict 键会误报；运行期 str(member) 即其字符串值
_KIND_BY_MESSAGE_TYPE: dict[str, str] = {
    str(ChatMessage.MessageType.IMAGE): KIND_IMAGE,
    str(ChatMessage.MessageType.VIDEO): KIND_VIDEO,
    str(ChatMessage.MessageType.AUDIO): KIND_AUDIO,
}


def validate_attachment_kind(upload: Any, message_type: str) -> None:
    """消息类型与附件种类匹配：图片/音视频消息只接受对应种类附件（文件消息不限）。"""
    required = _KIND_BY_MESSAGE_TYPE.get(message_type)
    if required and attachment_kind(upload) != required:
        raise DjangoValidationError(_("The message type does not match the attachment kind"))


def validate_upload_kind(upload: Any, kind: str) -> bool:
    """上传端点的种类门槛：kind 必须在白名单内，且与实际种类一致。

    - 白名单收紧：旧实现只校验 `kind=image`，其余取值（含伪造的 `video`）不限，
      「声明与实际不符」的记录会流入消息协议让前端按错误种类渲染气泡；
    - `kind=file` 对实际种类不限：图片/音视频也允许按文件消息发送（下载语义，
      与旧行为一致）；`kind=image|video|audio` 必须与真实 MIME 判定一致。
    """
    kind = str(kind or "").strip().lower()
    if kind not in UPLOAD_KINDS:
        return False
    return kind == KIND_FILE or attachment_kind(upload) == kind


def mark_attachment_used(upload: Any) -> None:
    """附件转正：临时上传件随消息落库即转正式件，交给保留期策略管理。"""
    if upload is not None and upload.is_tmp:
        upload.is_tmp = False
        upload.save(update_fields=["is_tmp", "updated_time"])


def attachment_response(message: Any, request: Any) -> Any:
    """受鉴权取件响应。

    - 图片：`?size=thumb|preview` 取缩略图/预览缓存（JPEG，inline，浏览器直接渲染）；
    - 音/视频：按存储文件真实 MIME inline 返回（浏览器原生播放器直接播放）；
      Range 断点续播首版不做（nginx L7 场景待登记后续补齐）；
    - 其余：按附件下载（Content-Disposition: attachment），对象存储经 storage 原语读取。
    """
    upload = message.attachment
    if upload is None:
        return None
    kind = attachment_kind(upload)
    size = str(request.query_params.get("size") or SIZE_THUMB).lower()
    if kind == KIND_IMAGE:
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
    if kind in (KIND_VIDEO, KIND_AUDIO):
        # inline 渲染按存储的真实 MIME 下发（浏览器以该类型交给播放器，不做嗅探改判）；
        # nosniff 兜底：即便 MIME 记录被污染也不会被浏览器嗅探成 HTML 执行
        content_type = (upload.mime_type or "").split(";")[0].strip() or "application/octet-stream"
        response = FileResponse(storage_open(name, "rb"), as_attachment=False, content_type=content_type)
        response["Content-Disposition"] = "inline; filename*=UTF-8''{}".format(quote(upload.filename or ""))
        response["X-Content-Type-Options"] = "nosniff"
        return response
    response = FileResponse(
        storage_open(name, "rb"),
        as_attachment=True,
        content_type=upload.mime_type or "application/octet-stream",
    )
    response["Content-Disposition"] = "attachment; filename*=UTF-8''{}".format(quote(upload.filename or ""))
    return response


def attachment_payload(message: Any) -> dict[str, Any] | None:
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


class AttachmentUploadError(Exception):
    """聊天附件上传失败（``code`` = 业务码，``detail`` = 可读文案），视图按其回包。"""

    def __init__(self, code: int, detail: Any) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def store_message_attachment(user: Any, file_obj: Any, kind: str, request: Any = None) -> dict[str, Any]:
    """聊天附件上传落库编排：限额校验 → 落库（临时件）→ 种类匹配 → 缓存失效 → 审计留痕。

    复用文件中心的安全策略与落库内核；落库为临时件——发送消息时由服务端转正，
    未发送的临时件由每日临时文件清理回收。``kind`` 白名单 image|video|audio|file
    且必须与真实种类一致（不匹配即删记录并报错，防伪造 kind 让前端按错误种类
    渲染气泡）。

    成功返回渲染载荷（attachment_extra）；失败抛 AttachmentUploadError。
    """
    try:
        check_upload_limits(user, [file_obj])
    except UploadError as exc:
        raise AttachmentUploadError(exc.code, exc.detail) from exc
    try:
        upload, __ = store_upload_file(user, file_obj, is_tmp=True)
    except Exception:  # noqa: BLE001 落盘/写库失败按上传失败归一（细节进日志）
        logger.exception("chat attachment save failed user=%s", user)
        raise AttachmentUploadError(1001, _("Failed to save uploaded file")) from None
    kind = str(kind or "").strip().lower()
    if not validate_upload_kind(upload, kind):
        # 种类不符（声明 kind 与真实 MIME 判定不一致）：删除刚落的记录，不留无主上传件
        upload.hard_delete()
        raise AttachmentUploadError(1001, _("The upload kind does not match the file type"))
    invalidate_upload_stats_cache(user.pk)
    log_file_access(upload=upload, user=user, action="upload", request=request)
    return attachment_extra(upload)
