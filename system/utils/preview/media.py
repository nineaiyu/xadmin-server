# -*- coding: utf-8 -*-
"""文件在线预览：类型判定、图片缩略图、文本读取与缓存回收。"""

import os
import time
import uuid

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from common.utils import get_logger

from .constants import (
    CACHE_DIR_NAME,
    GENERATE_LOCK_TIMEOUT,
    GENERATE_WAIT_SECONDS,
    KIND_IMAGE,
    KIND_OFFICE,
    KIND_PDF,
    KIND_TEXT,
    OFFICE_EXTENSIONS,
    OFFICE_MIME_TYPES,
    SIZE_PREVIEW,
    SIZE_THUMB,
    TEXT_EXTENSIONS,
    TEXT_MIME_EXACT,
    _config,
)

logger = get_logger(__name__)


def preview_kind(upload) -> str | None:
    """判定预览类型；`None` 表示不支持预览（前端应只给下载入口）。

    Office 判定排在文本之后：csv 等"表格类文本"仍按文本预览（体验更好且零转换成本）。
    """
    return preview_kind_of(getattr(upload, "mime_type", ""), getattr(upload, "filename", ""))


def preview_kind_of(mime_type, filename) -> str | None:
    """按 MIME + 文件名判定预览类型（纯函数）。

    独立成纯函数的原因：上传自动分类（system/utils/upload_category.py）要在落库前
    用同一套判定把 pdf/office/文本归为「文档」，避免两处规则各自演化后漂移。
    """
    mime = (mime_type or "").lower()
    filename = (filename or "").lower()
    if mime.startswith("image/"):
        return KIND_IMAGE
    if mime == "application/pdf" or filename.endswith(".pdf"):
        return KIND_PDF
    if mime.startswith("text/") or mime in TEXT_MIME_EXACT:
        return KIND_TEXT
    if filename.endswith(TEXT_EXTENSIONS):
        return KIND_TEXT
    if mime in OFFICE_MIME_TYPES or filename.endswith(OFFICE_EXTENSIONS):
        return KIND_OFFICE
    return None


def preview_cache_dir() -> str:
    return os.path.join(str(settings.MEDIA_ROOT), CACHE_DIR_NAME)


def preview_cache_path(upload, size: str = SIZE_THUMB) -> str:
    """缓存路径：`preview_cache/<pk>/<size>.jpg`（与源文件一对一可推导）。"""
    return os.path.join(preview_cache_dir(), str(upload.pk), f"{size}.jpg")


def source_path(upload) -> str | None:
    """源文件在磁盘上的绝对路径；缺失或不存在返回 None。"""
    filepath = getattr(upload, "filepath", None)
    if not filepath:
        return None
    path = filepath.path
    return path if os.path.exists(path) else None


def _width_for(size: str) -> int:
    if size == SIZE_PREVIEW:
        return _config("FILE_PREVIEW_IMAGE_WIDTH", 1280)
    return _config("FILE_PREVIEW_THUMB_WIDTH", 240)


def ensure_image_cache(upload, size: str = SIZE_THUMB) -> str | None:
    """按需生成图片预览缓存并返回缓存文件路径（已存在则直接返回）。

    并发去重：同一 (pk, size) 只有第一个请求生成，其余短等其产出；
    写入用临时文件 + `os.replace` 原子替换，避免读到半截文件。
    """
    if size not in (SIZE_THUMB, SIZE_PREVIEW):
        return None
    source = source_path(upload)
    if not source:
        return None

    target = preview_cache_path(upload, size)
    if os.path.exists(target):
        return target

    lock_key = f"preview_generating_{upload.pk}_{size}"
    if not cache.add(lock_key, "1", timeout=GENERATE_LOCK_TIMEOUT):
        # 已有请求在生成：短等其落盘，超时后自行生成（不阻塞用户）
        deadline = time.monotonic() + GENERATE_WAIT_SECONDS
        while time.monotonic() < deadline:
            if os.path.exists(target):
                return target
            time.sleep(0.1)

    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        tmp_path = f"{target}.{uuid.uuid4().hex}.tmp"
        _generate_jpeg(source, tmp_path, _width_for(size))
        os.replace(tmp_path, target)
        return target
    except Exception as exc:  # noqa: BLE001 生成失败不影响下载，只记录
        logger.warning(f"generate preview cache failed. file:{upload.pk} size:{size} error:{exc}")
        return None
    finally:
        cache.delete(lock_key)


def _generate_jpeg(source_path_: str, target_path: str, width: int) -> None:
    """等比缩放到目标宽度并写成 JPEG（统一格式，避免 PNG/GIF 体积失控）。"""
    from PIL import Image, ImageOps

    with Image.open(source_path_) as img:
        # 手机/相机照片的方向信息在 EXIF 里，不纠正会出现"躺着"的缩略图
        img = ImageOps.exif_transpose(img)
        if img.width > width:
            img.thumbnail((width, width))
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        img.save(target_path, "JPEG", quality=85)


def read_text_preview(upload, max_bytes: int | None = None) -> tuple[str, bool]:
    """读取文本预览内容：`(内容, 是否截断)`。

    二进制文件（含 NUL 字节）返回空内容 + 未截断，由调用方按「不支持预览」处理。
    """
    if max_bytes is None:
        max_bytes = _config("FILE_PREVIEW_TEXT_MAX_BYTES", 256 * 1024)
    source = source_path(upload)
    if not source:
        return "", False
    try:
        with open(source, "rb") as file:
            raw = file.read(max_bytes + 1)
    except OSError as exc:
        logger.warning(f"read text preview failed. file:{upload.pk} error:{exc}")
        return "", False

    if b"\x00" in raw[:4096]:
        return "", False
    truncated = len(raw) > max_bytes
    return raw[:max_bytes].decode("utf-8", errors="replace"), truncated


def touch_preview_cache(path: str) -> None:
    """命中缓存时刷新 mtime：保留期清理按「最近使用」淘汰，常用预览不会被误删。"""
    try:
        os.utime(path, None)
    except OSError:
        pass


def remove_preview_cache(upload) -> int:
    """删除某个源文件的全部预览缓存（源文件删除时联动），返回删除条数。"""
    return remove_preview_cache_by_pk(upload.pk)


def remove_preview_cache_by_pk(pk) -> int:
    """按主键删除预览缓存目录。

    独立成函数的原因：Django 的 `Model.delete()` 会把实例 `pk` 置为 None，
    而缓存目录按 pk 推导，删除后再取值会得到 `.../None/`（静默漏删）。
    """
    import shutil

    directory = os.path.join(preview_cache_dir(), str(pk))
    if not os.path.isdir(directory):
        return 0
    shutil.rmtree(directory, ignore_errors=True)
    return 1


def clean_preview_cache(keep_days: int | None = None, batch: int = 2000) -> dict:
    """清理预览缓存：孤儿（源记录已不存在）+ 超保留期（默认 FILE_PREVIEW_CACHE_KEEP_DAYS）。

    :return: `{"scanned": n, "removed_orphan": n, "removed_expired": n}`
    """
    from system.models import UploadFile

    if keep_days is None:
        keep_days = _config("FILE_PREVIEW_CACHE_KEEP_DAYS", 7)

    root = preview_cache_dir()
    if not os.path.isdir(root):
        return {"scanned": 0, "removed_orphan": 0, "removed_expired": 0}

    deadline = None
    if keep_days and keep_days > 0:
        deadline = time.time() - keep_days * 86400

    scanned = orphan = expired = 0
    for name in os.listdir(root)[:batch]:
        directory = os.path.join(root, name)
        if not os.path.isdir(directory):
            continue
        scanned += 1
        exists = UploadFile.all_objects.filter(pk=name).exists()
        if not exists:
            _remove_dir(directory)
            orphan += 1
            continue
        if deadline is not None and _dir_mtime(directory) < deadline:
            # 保留期内被访问过则刷新 mtime（预览命中时由调用方 touch）
            _remove_dir(directory)
            expired += 1
    logger.info(f"clean preview cache scanned:{scanned} orphan:{orphan} expired:{expired}")
    return {"scanned": scanned, "removed_orphan": orphan, "removed_expired": expired}


def _remove_dir(path: str) -> None:
    import shutil

    shutil.rmtree(path, ignore_errors=True)


def _dir_mtime(path: str) -> float:
    try:
        return max(os.path.getmtime(os.path.join(root, file)) for root, _dirs, files in os.walk(path) for file in files)
    except (OSError, ValueError):
        return timezone.now().timestamp()
