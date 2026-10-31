#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""受保护媒体的签名 URL（敏感命名空间的文件级授权）。

口径：

- 敏感命名空间（导出 / 导入报告 / 聊天附件等统一落在 ``file/uploadfile``，以及对象存储
  本地缓存 ``storage_cache``）不满足「登录即可读」：链接必须携带 ``exp``（Unix 时间戳）
  与 ``sig``（HMAC-SHA256，密钥取 ``SECRET_KEY``）；
- ``MEDIA_SIGNED_URL_REQUIRED`` 默认开启（安全默认）；显式关闭后回落「登录态」粒度，
  仅供仍依赖裸链接的兼容期部署使用；
- 签名覆盖「归一化后的媒体相对路径 + 过期时间」，篡改路径或改用其它文件的签名均失败；
  比较使用 ``hmac.compare_digest``，过期即拒绝。
"""

import hashlib
import hmac
import posixpath
import time
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from common.settings_contract import kernel_required_setting, kernel_setting

#: 需要签名授权的敏感命名空间（相对 MEDIA_ROOT 的首段目录）
SENSITIVE_MEDIA_PREFIXES = frozenset({"file/uploadfile", "storage_cache"})

MEDIA_EXP_PARAM = "exp"
MEDIA_SIG_PARAM = "sig"


def normalize_media_path(path: Any) -> str:
    """媒体路径归一：去掉绝对前缀与前导斜杠，与 ``/media/`` 路由传入的 path 同口径。"""
    return posixpath.normpath(str(path or "")).lstrip("/")


def is_sensitive_media_path(path: Any) -> bool:
    normalized = normalize_media_path(path)
    return any(normalized == prefix or normalized.startswith(f"{prefix}/") for prefix in SENSITIVE_MEDIA_PREFIXES)


def media_signature_required() -> bool:
    return bool(kernel_setting("MEDIA_SIGNED_URL_REQUIRED"))


def _signing_key() -> bytes:
    return str(kernel_required_setting("SECRET_KEY")).encode()


def _signature(path: str, expires: int) -> str:
    message = f"{path}\n{expires}".encode()
    return hmac.new(_signing_key(), message, hashlib.sha256).hexdigest()


def signed_media_query(path: Any, ttl: int | None = None) -> str:
    """生成敏感路径的签名查询串（``exp=...&sig=...``）；非敏感路径返回空串。"""
    normalized = normalize_media_path(path)
    if not is_sensitive_media_path(normalized):
        return ""
    lifetime = int(kernel_setting("MEDIA_SIGNED_URL_TTL")) if ttl is None else int(ttl)
    expires = int(time.time()) + lifetime
    return urlencode({MEDIA_EXP_PARAM: expires, MEDIA_SIG_PARAM: _signature(normalized, expires)})


def sign_media_path(path: Any, ttl: int | None = None) -> str:
    """为敏感相对路径追加签名参数；非敏感路径原样返回。"""
    query = signed_media_query(path, ttl)
    if not query:
        return str(path or "")
    separator = "&" if "?" in str(path) else "?"
    return f"{path}{separator}{query}"


def _media_relative_path(url: str) -> str | None:
    """从 ``/media/`` URL 取相对路径；对象存储 / CDN 等非本地上传地址返回 None。

    本地存储的 ``FieldFile.url`` 可能带前导斜杠也可能不带（``MEDIA_URL`` 的写法决定），
    两种形态都按同一相对路径签名。
    """
    try:
        media_prefix = str(kernel_required_setting("MEDIA_URL")).strip("/")
    except Exception:  # noqa: BLE001 配置异常按非本地上传地址处理（不签名）
        return None
    parts = urlsplit(str(url))
    for prefix in (f"/{media_prefix}/", f"{media_prefix}/"):
        if parts.path.startswith(prefix):
            return parts.path[len(prefix) :]
    return None


def sign_media_url(url: Any, ttl: int | None = None) -> str:
    """为 ``/media/...`` 形式的 URL 追加签名参数；其余地址原样返回。

    ``get_file_absolute_uri`` / ``storage_url`` 等 URL 生成入口统一经此，
    下载 / 预览端点签发的媒体链接与 ``media_serve`` 的校验口径保持同源。
    """
    text = str(url or "")
    relative = _media_relative_path(text)
    if relative is None:
        return text
    query = signed_media_query(relative, ttl)
    if not query:
        return text
    parts = urlsplit(text)
    merged = urlencode([*parse_qsl(parts.query, keep_blank_values=True), *parse_qsl(query, keep_blank_values=True)])
    return urlunsplit((parts.scheme, parts.netloc, parts.path, merged, parts.fragment))


def verify_media_signature(path: Any, expires: Any, signature: Any) -> bool:
    normalized = normalize_media_path(path)
    if not is_sensitive_media_path(normalized):
        return True
    try:
        expires_int = int(expires)
    except (TypeError, ValueError):
        return False
    if expires_int <= int(time.time()):
        return False
    return hmac.compare_digest(_signature(normalized, expires_int), str(signature or ""))


def verify_media_request(path: Any, request: Any) -> bool:
    """媒体请求放行判定：非敏感路径或兼容期关闭签名时直接通过。"""
    normalized = normalize_media_path(path)
    if not is_sensitive_media_path(normalized) or not media_signature_required():
        return True
    get_params = getattr(request, "GET", None)
    if get_params is None:
        return False
    return verify_media_signature(normalized, get_params.get(MEDIA_EXP_PARAM), get_params.get(MEDIA_SIG_PARAM))
