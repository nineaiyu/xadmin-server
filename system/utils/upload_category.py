#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""上传文件自动分类：按 MIME 与扩展名推断到 upload_category 字典项。

背景：历史上上传记录的 category 恒为空，每条都要人工补分类；本模块在落库前
尽力推断，让「上传图片 → 分类=图片」这类直观预期自动成立。

**字典是分类的唯一事实来源**：分类选项由管理员在字典页增删改，推断结果可能
不在字典中（如字典未配置 audio）——此时按 other 回退，仍不可用时留空（None），
绝不把字典外取值写进数据库（DictChoiceField 只约束接口写入路径，DB 层无约束）。

判定顺序与 ``system.utils.preview.preview_kind_of`` 的分派保持同源：
先图片/视频/音频（MIME 前缀 + 扩展名），再压缩包，最后复用预览判定把
pdf/office/文本归为文档，全部落空归「其他」。
"""

from system.utils.dict import get_dict_items
from system.utils.preview import preview_kind_of

__all__ = [
    "UPLOAD_CATEGORY_DICT",
    "guess_upload_category",
    "resolve_upload_category",
]

#: 分类字典 code（与 UploadFileSerializer.category 的 DictChoiceField 同源）
UPLOAD_CATEGORY_DICT = "upload_category"

CATEGORY_IMAGE = "image"
CATEGORY_DOCUMENT = "document"
CATEGORY_VIDEO = "video"
CATEGORY_ARCHIVE = "archive"
CATEGORY_AUDIO = "audio"
CATEGORY_OTHER = "other"

# MIME 缺失或不可信（部分浏览器给 octet-stream）时按扩展名兜底
IMAGE_EXTENSIONS = (
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".webp",
    ".bmp",
    ".svg",
    ".ico",
    ".tif",
    ".tiff",
    ".heic",
    ".heif",
    ".avif",
)
VIDEO_EXTENSIONS = (
    ".mp4",
    ".m4v",
    ".mov",
    ".avi",
    ".mkv",
    ".wmv",
    ".flv",
    ".webm",
    ".mpg",
    ".mpeg",
    ".3gp",
    ".rmvb",
    ".asf",
    ".ogv",
)
AUDIO_EXTENSIONS = (
    ".mp3",
    ".wav",
    ".flac",
    ".aac",
    ".ogg",
    ".oga",
    ".m4a",
    ".wma",
    ".ape",
)
ARCHIVE_EXTENSIONS = (
    ".zip",
    ".rar",
    ".7z",
    ".tar",
    ".gz",
    ".tgz",
    ".bz2",
    ".tbz",
    ".tbz2",
    ".xz",
    ".txz",
    ".zst",
    ".lz",
    ".lzma",
    ".cab",
    ".iso",
    ".jar",
    ".war",
)
ARCHIVE_MIME_TYPES = (
    "application/zip",
    "application/x-zip-compressed",
    "application/x-rar-compressed",
    "application/vnd.rar",
    "application/x-7z-compressed",
    "application/x-tar",
    "application/gzip",
    "application/x-gzip",
    "application/x-bzip2",
    "application/x-xz",
    "application/zstd",
    "application/x-compressed",
    "application/java-archive",
)


def _normalize(mime_type) -> str:
    """规范化 MIME：去参数（``text/plain; charset=utf-8``）并统一小写。"""
    return (mime_type or "").split(";")[0].strip().lower()


def guess_upload_category(filename, mime_type) -> str:
    """按 MIME + 扩展名推断语义分类 code（**不保证该 code 存在于字典中**）。

    兜底返回 ``other``（「其他」即未知类型的归类），由 resolve 决定是否可用。
    """
    name = (filename or "").lower()
    mime = _normalize(mime_type)
    if mime.startswith("image/") or name.endswith(IMAGE_EXTENSIONS):
        return CATEGORY_IMAGE
    if mime.startswith("video/") or name.endswith(VIDEO_EXTENSIONS):
        return CATEGORY_VIDEO
    if mime.startswith("audio/") or name.endswith(AUDIO_EXTENSIONS):
        return CATEGORY_AUDIO
    if mime in ARCHIVE_MIME_TYPES or name.endswith(ARCHIVE_EXTENSIONS):
        return CATEGORY_ARCHIVE
    if preview_kind_of(mime, filename) is not None:
        return CATEGORY_DOCUMENT
    return CATEGORY_OTHER


def resolve_upload_category(filename, mime_type) -> str | None:
    """推断 + 字典校验：返回可直接落库的分类值；字典中无处可归时返回 None。

    去重命中与正常落盘两条路径共用本函数（结果一致，仅物理文件复用方式不同）。
    """
    guess = guess_upload_category(filename, mime_type)
    available = {item["value"] for item in get_dict_items(UPLOAD_CATEGORY_DICT) if item["value"]}
    if guess in available:
        return guess
    # 推断类型未配置（如音频无独立分类）时归入「其他」；字典连「其他」都没有则留空
    if CATEGORY_OTHER in available:
        return CATEGORY_OTHER
    return None
