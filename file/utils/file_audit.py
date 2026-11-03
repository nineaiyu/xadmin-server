#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""文件访问审计与上传安全策略。

- ``log_file_access``：上传 / 下载 / 预览 / 删除四类动作的元数据留痕
  （日志写入全程吞异常，绝不阻断文件主链路）；
- ``validate_upload_extension``：上传扩展名策略（黑名单优先，白名单非空时只允许名单内），
  fail-closed；用于通用文件上传（图片字段上传另有独立的魔数 + 重编码链路）；
- ``validate_upload_content``：上传内容与扩展名的一致性校验（文件头 magic 抽检）——
  伪装扩展名的二进制内容（如可执行文件改名 .png）在存储侧即拒绝，不再只依赖
  响应侧隔离（nosniff + 可执行类型降级附件）；判定 fail-closed：无法识别的二进制
  内容一律拒绝，文本内容不设扩展名映射限制（避免误伤 .conf/.ini 等自定义文本文件）。
"""

import os
from typing import Any

from django.conf import settings
from django.utils.translation import gettext_lazy as _

from common.utils import get_logger

logger = get_logger(__name__)


def log_file_access(
    *,
    upload: Any = None,
    user: Any = None,
    action: Any,
    request: Any = None,
    result: Any = True,
    detail: str = "",
    filename: str = "",
) -> None:
    """写一条文件访问日志（失败只记 warning）。"""
    from file.models import FileAccessLog

    try:
        from approval.services.approval.display import user_display
        from common.utils.request import get_request_ip

        ip = get_request_ip(request) if request is not None else ""
        FileAccessLog.objects.create(
            file=upload,
            filename=(filename or getattr(upload, "filename", "") or "")[:255],
            user=user if getattr(user, "pk", None) else None,
            user_display=user_display(user) if getattr(user, "pk", None) else "",
            action=action,
            ipaddress=ip or "",
            result=bool(result),
            detail=(detail or "")[:255],
        )
    except Exception:  # noqa: BLE001 审计写入失败不影响文件主链路
        logger.warning("write file access log failed. action:%s", action, exc_info=True)


def _extension_list(value: Any) -> set[str]:
    return {str(item).strip().lower().lstrip(".") for item in (value or []) if str(item).strip()}


def get_upload_policy() -> dict[str, list[str]]:
    """当前上传扩展名策略（黑名单 / 白名单），供前端提示与校验同源。"""
    return {
        "block_extensions": sorted(_extension_list(getattr(settings, "SECURITY_UPLOAD_BLOCK_EXTENSIONS", []))),
        "allow_extensions": sorted(_extension_list(getattr(settings, "SECURITY_UPLOAD_ALLOW_EXTENSIONS", []))),
    }


def validate_upload_extension(filename: Any) -> str:
    """校验上传文件扩展名，返回错误文案（通过返回空串）。

    fail-closed 规则：白名单非空 → 必须命中白名单；命中黑名单一律拒绝（黑名单优先，
    防止「白名单里误加 exe」类配置把危险类型放进来）。
    """
    ext = os.path.splitext(str(filename or ""))[1].lower().lstrip(".")
    policy = get_upload_policy()
    if ext in set(policy["block_extensions"]):
        return str(_("File type .{} is not allowed to upload").format(ext or "?"))
    allow = set(policy["allow_extensions"])
    if allow and ext not in allow:
        return str(_("Only the following file types are allowed: {}").format(", ".join(sorted(allow))))
    return ""


# ---------------------------------------------------------------------------
# 上传内容 magic 抽检（存储侧拒绝伪装扩展名的二进制内容）
# ---------------------------------------------------------------------------

#: 嗅探读取的文件头字节数：除魔数前缀外还要覆盖 OOXML 包的内部条目名
#: （``[Content_Types].xml`` / ``word/`` 等出现在 zip 本地头里，通常在前几 KB）
MAGIC_SNIFF_BYTES = 16 * 1024

# 内容类型标识
KIND_IMAGE = "image"
KIND_PDF = "pdf"
KIND_OLE = "ole"
KIND_ZIP = "zip"
KIND_OOXML = "ooxml"
KIND_OOXML_WORD = "ooxml-word"
KIND_OOXML_EXCEL = "ooxml-excel"
KIND_OOXML_PPT = "ooxml-ppt"
KIND_RAR = "rar"
KIND_7Z = "7z"
KIND_GZIP = "gzip"
KIND_BZIP2 = "bzip2"
KIND_XZ = "xz"
KIND_TAR = "tar"
KIND_AUDIO = "audio"
KIND_VIDEO = "video"
KIND_TEXT = "text"
KIND_BINARY = "binary"

#: 固定前缀魔数（顺序无关，命中即返回）；RIFF / zip / MP3 / MP4 / BMP 家族的
#: 多段判定在 detect_content_kind 内单独处理
_MAGIC_PREFIXES: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", KIND_IMAGE),  # PNG
    (b"\xff\xd8\xff", KIND_IMAGE),  # JPEG
    (b"GIF87a", KIND_IMAGE),
    (b"GIF89a", KIND_IMAGE),
    (b"\x00\x00\x01\x00", KIND_IMAGE),  # ICO
    (b"%PDF-", KIND_PDF),
    (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", KIND_OLE),  # Office 97-2003（doc/xls/ppt）
    (b"Rar!\x1a\x07", KIND_RAR),
    (b"7z\xbc\xaf\x27\x1c", KIND_7Z),
    (b"\x1f\x8b", KIND_GZIP),
    (b"BZh", KIND_BZIP2),
    (b"\xfd7zXZ\x00", KIND_XZ),
    (b"fLaC", KIND_AUDIO),
    (b"OggS", KIND_AUDIO),
)

#: 扩展名 → 允许的内容类型集合；未映射的扩展名按「文本放行、二进制拒绝」兜底
_EXTENSION_KIND_RULES: tuple[tuple[frozenset[str], frozenset[str]], ...] = (
    (frozenset({"png", "jpg", "jpeg", "gif", "webp", "bmp", "ico"}), frozenset({KIND_IMAGE})),
    (frozenset({"pdf"}), frozenset({KIND_PDF})),
    # OOXML 与普通 zip 共用 PK 头：docx 等的判定看包内标记，合法包不能被误拒
    (frozenset({"docx", "docm", "dotx"}), frozenset({KIND_OOXML_WORD, KIND_OOXML})),
    (frozenset({"xlsx", "xlsm", "xltx"}), frozenset({KIND_OOXML_EXCEL, KIND_OOXML})),
    (frozenset({"pptx", "pptm"}), frozenset({KIND_OOXML_PPT, KIND_OOXML})),
    (frozenset({"doc", "xls", "ppt"}), frozenset({KIND_OLE})),
    # docx 改名 .zip 仍是一个合法压缩包（反向：普通 zip 改名 .docx 按「类型不符」拒绝）
    (frozenset({"zip"}), frozenset({KIND_ZIP, KIND_OOXML, KIND_OOXML_WORD, KIND_OOXML_EXCEL, KIND_OOXML_PPT})),
    (frozenset({"rar"}), frozenset({KIND_RAR})),
    (frozenset({"7z"}), frozenset({KIND_7Z})),
    (frozenset({"gz", "tgz"}), frozenset({KIND_GZIP})),
    (frozenset({"bz2"}), frozenset({KIND_BZIP2})),
    (frozenset({"xz"}), frozenset({KIND_XZ})),
    (frozenset({"tar"}), frozenset({KIND_TAR})),
    (frozenset({"mp3", "wav", "flac", "ogg", "m4a", "aac"}), frozenset({KIND_AUDIO})),
    (frozenset({"mp4", "webm", "mkv", "mov", "avi"}), frozenset({KIND_VIDEO})),
    (
        frozenset(
            {"txt", "md", "csv", "tsv", "log", "json", "yaml", "yml", "toml", "ini", "conf", "sql", "vtt", "srt"}
        ),
        frozenset({KIND_TEXT}),
    ),
)

EXTENSION_CONTENT_KINDS: dict[str, frozenset[str]] = {
    ext: kinds for exts, kinds in _EXTENSION_KIND_RULES for ext in exts
}


def read_file_head(file_obj: Any, size: int = MAGIC_SNIFF_BYTES) -> bytes | None:
    """读取文件头字节并恢复原读指针（后续 md5 / 落盘读取不受影响）。

    读取失败返回 None：调用方按「内容不可判定」fail-closed 拒绝，不静默放行。
    """
    try:
        position = file_obj.tell()
    except Exception:  # noqa: BLE001 不可定位（流式句柄）：读后不回退
        position = None
    try:
        file_obj.seek(0)
        head = file_obj.read(size)
    except Exception:  # noqa: BLE001 读取失败按不可判定处理
        return None
    finally:
        if position is not None:
            try:
                file_obj.seek(position)
            except Exception:  # noqa: BLE001 回退失败不再传播（读出的头仍可用于判定）
                pass
    return bytes(head)


def detect_content_kind(head: bytes | None) -> str:
    """文件头 → 内容类型标识（纯函数，供校验与测试消费）。

    判定顺序：固定前缀魔数 → BMP / zip 容器（zip **先**判 OOXML 包内标记、**后**
    回落普通压缩包，防止 docx 被当「类型不符」误拒）→ ID3/MP3 帧 → MP4 家族 →
    Matroska → RIFF 家族 → tar → 文本启发（无 NUL 字节）→ 未知二进制。
    空文件与 BOM 开头按文本放行；None（不可读）由调用方拒绝。
    """
    if not head:
        return KIND_TEXT
    if head[:3] in (b"\xef\xbb\xbf", b"\xff\xfe", b"\xfe\xff"):
        return KIND_TEXT
    for prefix, kind in _MAGIC_PREFIXES:
        if head.startswith(prefix):
            return kind
    # BMP：BM 签名 + 保留段为 0（仅凭 "BM" 两字节会把 "BMX 排班.txt" 类文本误判成图片）
    if head[:2] == b"BM" and head[6:10] == b"\x00\x00\x00\x00":
        return KIND_IMAGE
    if head[:4] in (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"):
        if b"[Content_Types].xml" in head:
            for marker, kind in ((b"word/", KIND_OOXML_WORD), (b"xl/", KIND_OOXML_EXCEL), (b"ppt/", KIND_OOXML_PPT)):
                if marker in head:
                    return kind
            return KIND_OOXML
        return KIND_ZIP
    # MP3：ID3v2 标签头或 MPEG 音频帧同步字节
    if head[:3] == b"ID3" or head[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2", b"\xff\xe2"):
        return KIND_AUDIO
    # ISO BMFF 家族（mp4/m4a/mov）：4 字节长度 + ftyp + brand
    if head[4:8] == b"ftyp":
        return KIND_AUDIO if head[8:12].startswith(b"M4A") else KIND_VIDEO
    if head.startswith(b"\x1a\x45\xdf\xa3"):
        return KIND_VIDEO  # Matroska / WebM
    if head[:4] == b"RIFF" and head[8:12] in (b"WAVE", b"WEBP", b"AVI "):
        return {b"WAVE": KIND_AUDIO, b"WEBP": KIND_IMAGE, b"AVI ": KIND_VIDEO}[head[8:12]]
    if head[257:262] == b"ustar":
        return KIND_TAR
    if b"\x00" in head:
        return KIND_BINARY
    return KIND_TEXT


def validate_upload_content(filename: Any, file_obj: Any) -> str:
    """校验上传内容与扩展名一致，返回错误文案（通过返回空串）。

    fail-closed 规则：

    - 文件头不可读取（异常句柄）：拒绝；
    - 扩展名有类型映射：实际内容类型必须在映射集合内（伪装扩展名拒绝）；
    - 扩展名未映射：文本内容放行（自定义文本文件不受影响），二进制内容一律拒绝
      （无法证明内容与扩展名相符，按「无法识别的二进制」处理）。
    """
    ext = os.path.splitext(str(filename or ""))[1].lower().lstrip(".")
    head = read_file_head(file_obj)
    if head is None:
        return str(_("Unrecognized file content is not allowed to upload"))
    kind = detect_content_kind(head)
    expected = EXTENSION_CONTENT_KINDS.get(ext)
    if expected is None:
        if kind == KIND_BINARY:
            return str(_("Unrecognized file content is not allowed to upload"))
        return ""
    if kind not in expected:
        return str(_("File content does not match the .{} file extension").format(ext or "?"))
    return ""


# 清理批次大小（与操作日志清理同量级）
FILE_LOG_CLEAN_BATCH = 2000


def clean_expired_file_access_logs() -> int:
    """按 FILE_ACCESS_LOG_KEEP_DAYS 分批清理文件访问日志（0 = 不清理），返回删除行数。"""
    from datetime import timedelta

    from django.utils import timezone

    from file.models import FileAccessLog

    keep_days = int(getattr(settings, "FILE_ACCESS_LOG_KEEP_DAYS", 180) or 0)
    if keep_days <= 0:
        return 0
    cutoff = timezone.now() - timedelta(days=keep_days)
    total = 0
    while True:
        pks = list(
            FileAccessLog.objects.filter(created_time__lt=cutoff).values_list("pk", flat=True)[:FILE_LOG_CLEAN_BATCH]
        )
        if not pks:
            break
        FileAccessLog.objects.filter(pk__in=pks).delete()
        total += len(pks)
    logger.info("clean %s file access logs (keep %s days)", total, keep_days)
    return total
