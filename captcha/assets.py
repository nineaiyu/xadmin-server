#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""验证码背景资产注册表。

资产随包发布，或通过 ``CAPTCHA_ASSET_DIR`` 在**运行期只读挂载**（离线导入命令
可在构建/运维阶段写入该目录）；manifest 记录许可、来源与 sha256，运行期首次加载
按需比对（``CAPTCHA_ASSET_VERIFY_SHA256``，默认开启）。manifest 为空时回退
程序化底图，行为与旧版本一致。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import random
import threading
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from django.conf import settings
from PIL import Image, ImageOps

from common.utils import get_logger

logger = get_logger(__name__)

MANIFEST_NAME = "manifest.json"
#: 已校验资产的 (size, mtime) 指纹：运行期只读挂载时命中缓存，不重复读盘
_verified: dict[str, tuple[int, float]] = {}
_verify_lock = threading.Lock()
# 资产清单短缓存：挑战接口每次都会取一张底图，manifest 读盘 + 全量 stat 不能
# 落在每条匿名请求上；文件变化最多滞后 ASSET_CACHE_TTL 秒
_ASSET_CACHE_TTL = 60
_asset_cache: dict[str, Any] = {"key": None, "expires": 0.0, "value": []}
_asset_cache_lock = threading.Lock()


@dataclass(frozen=True)
class BackgroundAsset:
    id: str
    path: Path
    orientation: str = ""
    sha256: str = ""


def asset_root() -> Path:
    configured = str(getattr(settings, "CAPTCHA_ASSET_DIR", "") or "").strip()
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parent / "resources" / "backgrounds"


def load_manifest(root: Path | None = None) -> dict[str, Any]:
    path = (root or asset_root()) / MANIFEST_NAME
    if not path.is_file():
        return {"pack_id": "procedural", "version": "0", "license": "procedural", "files": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"pack_id": "procedural", "version": "0", "license": "procedural", "files": []}
    return data if isinstance(data, dict) else {}


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _asset_trusted(asset: BackgroundAsset) -> bool:
    """运行期校验资产完整性（manifest 未登记 sha256 时跳过以兼容旧包）。"""
    expected = str(asset.sha256 or "").strip().lower()
    if not expected or not bool(getattr(settings, "CAPTCHA_ASSET_VERIFY_SHA256", True)):
        return True
    try:
        stat = asset.path.stat()
    except OSError:
        return False
    key = str(asset.path)
    with _verify_lock:
        if _verified.get(key) == (stat.st_size, stat.st_mtime):
            return True
    try:
        actual = _file_sha256(asset.path)
    except OSError:
        return False
    if not hmac.compare_digest(actual, expected):
        logger.warning("captcha asset checksum mismatch, skipped: %s", key)
        return False
    with _verify_lock:
        _verified[key] = (stat.st_size, stat.st_mtime)
    return True


def background_assets() -> list[BackgroundAsset]:
    configured_dir = str(getattr(settings, "CAPTCHA_ASSET_DIR", "") or "").strip()
    pack = str(getattr(settings, "CAPTCHA_BACKGROUND_PACK", "builtin") or "builtin").strip()
    if pack in {"", "procedural"}:
        return []
    root = asset_root()
    manifest_path = root / MANIFEST_NAME
    try:
        stat = manifest_path.stat()
        cache_key = (str(root), stat.st_mtime_ns, stat.st_size)
    except OSError:
        cache_key = (str(root), 0, 0)
    now = time.monotonic()
    with _asset_cache_lock:
        if _asset_cache["key"] == cache_key and _asset_cache["expires"] > now:
            return list(_asset_cache["value"])
    manifest = load_manifest(root)
    # 自定义只读目录本身就是选中的资产包，不要求 pack_id 与内置名一致
    if not configured_dir and str(manifest.get("pack_id") or "builtin") not in {pack, "builtin"}:
        return []
    assets: list[BackgroundAsset] = []
    for item in manifest.get("files") or []:
        if not isinstance(item, dict):
            continue
        raw = str(item.get("file") or "").strip()
        if not raw or Path(raw).is_absolute() or ".." in Path(raw).parts:
            continue
        path = (root / raw).resolve()
        if not path.is_relative_to(root.resolve()) or not path.is_file():
            continue
        asset = BackgroundAsset(
            id=str(item.get("id") or path.stem),
            path=path,
            orientation=str(item.get("orientation") or ""),
            sha256=str(item.get("sha256") or ""),
        )
        if not _asset_trusted(asset):
            continue
        assets.append(asset)
    with _asset_cache_lock:
        _asset_cache.update(key=cache_key, expires=now + _ASSET_CACHE_TTL, value=assets)
    return assets


@lru_cache(maxsize=64)
def _load_image(path: str) -> Image.Image:
    with Image.open(path) as image:
        return ImageOps.exif_transpose(image).convert("RGB")


def select_background(
    size: tuple[int, int],
    rng: random.Random | None = None,
    orientation: str = "",
) -> tuple[Image.Image | None, str]:
    """随机取一张图库图片并裁切；返回 (图片, 资产 id)，无图库/失败返回 (None, "")。"""
    rng = rng or random.Random()
    assets = background_assets()
    if orientation:
        preferred = [item for item in assets if item.orientation == orientation]
        assets = preferred or assets
    if not assets:
        return None, ""
    asset = rng.choice(assets)
    try:
        image = ImageOps.fit(_load_image(str(asset.path)), size, method=Image.Resampling.BICUBIC)
        return image, asset.id
    except (OSError, ValueError):
        return None, ""


def load_background(
    size: tuple[int, int],
    rng: random.Random | None = None,
    orientation: str = "",
) -> Image.Image | None:
    """随机取一张图库图片并裁切到目标尺寸；无图库/读取失败返回 None。"""
    image, _asset_id = select_background(size, rng, orientation)
    return image
