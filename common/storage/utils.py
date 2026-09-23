#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""存储访问适配层（P-4）：屏蔽本地 / 对象存储差异的统一入口。

业务侧**不应**直接使用 ``filepath.path``（对象存储无本地路径），统一走本模块：

- :func:`storage_exists` / :func:`storage_open` / :func:`storage_size`：与后端无关的读写原语；
- :func:`storage_local_path`：需要本地路径的场景（PIL 预览 / 文本读取 / PDF 转换），
  远端后端会先把对象下载到本地缓存（``MEDIA_ROOT/storage_cache``，按最近使用保留）；
- :func:`storage_is_local` / :func:`storage_backend_name`：分支决策（缩略图等本地专属逻辑）；
- :func:`storage_presigned_url`：对象存储预签名直连（仅 `s3` 后端可用，其余返回 None 回退中转）；
- :func:`storage_probe`：健康探测（本地 = MEDIA_ROOT 可写；远端 = 一次 exists 往返）。
"""

import hashlib
import os
import shutil
import time
from collections.abc import Iterator
from urllib.parse import quote

from django.conf import settings
from django.core.files.storage import FileSystemStorage, default_storage

from common.utils import get_logger

logger = get_logger(__name__)

#: 远端对象本地缓存目录（MEDIA_ROOT 下，与预览缓存并列；由 clean_storage_cache 回收）
STORAGE_CACHE_DIR_NAME = "storage_cache"
#: 缓存下载分块大小（1MB）：大文件流式落盘，不整份读进内存
COPY_CHUNK_SIZE = 1024 * 1024
#: 预签名直连 URL 默认有效期（秒）：短时效，签发前必须已完成应用鉴权与审计
PRESIGN_DEFAULT_EXPIRES = 600


def storage_is_local() -> bool:
    """当前生效存储是否本地文件系统（可插拔后端的统一判据）。"""
    flag = getattr(default_storage, "is_local", None)
    if isinstance(flag, bool):
        return flag
    return isinstance(default_storage, FileSystemStorage)


def storage_backend_name() -> str:
    """当前后端名（local / s3 / 其它类名）。"""
    name = getattr(default_storage, "backend_name", None)
    if isinstance(name, str):
        return name
    if isinstance(default_storage, FileSystemStorage):
        return "local"
    return default_storage.__class__.__name__


def storage_exists(name: str | None) -> bool:
    if not name:
        return False
    try:
        return bool(default_storage.exists(name))
    except Exception:  # noqa: BLE001 存储抖动按「不存在」降级，由调用方给可读提示
        logger.warning("storage exists failed. name:%s", name, exc_info=True)
        return False


def storage_open(name: str, mode: str = "rb"):
    """打开存储对象（本地 / 远端统一入口；返回类文件对象）。"""
    return default_storage.open(name, mode)


def storage_size(name: str) -> int:
    try:
        return int(default_storage.size(name))
    except Exception:  # noqa: BLE001
        return 0


def storage_cache_dir() -> str:
    return os.path.join(str(settings.MEDIA_ROOT), STORAGE_CACHE_DIR_NAME)


def _cache_path(name: str) -> str:
    digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:16]
    return os.path.join(storage_cache_dir(), digest, os.path.basename(name))


def storage_local_path(name: str | None) -> str | None:
    """本地绝对路径；对象存储后端先下载到本地缓存（幂等，命中直接返回）。

    返回 ``None`` 表示不可用（本地缺失 / 远端下载失败），调用方按「文件缺失」降级。
    """
    if not name:
        return None
    if storage_is_local():
        try:
            path = default_storage.path(name)
        except Exception:  # noqa: BLE001 本地后端 path 异常按缺失处理
            return None
        return path if os.path.exists(path) else None

    target = _cache_path(name)
    if os.path.exists(target) and os.path.getsize(target) == storage_size(name):
        _touch(target)
        return target
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        tmp_path = f"{target}.{os.getpid()}.{int(time.time() * 1000)}.tmp"
        with storage_open(name, "rb") as source, open(tmp_path, "wb") as out:
            shutil.copyfileobj(source, out, length=COPY_CHUNK_SIZE)
        os.replace(tmp_path, target)
        return target
    except Exception:  # noqa: BLE001 下载失败不抛给调用方（预览/预览转换按缺失降级）
        logger.warning("download storage object to local cache failed. name:%s", name, exc_info=True)
        return None


def _touch(path: str):
    try:
        os.utime(path, None)
    except OSError:
        pass


def iter_cached_files() -> Iterator[tuple[str, float]]:
    """遍历本地缓存文件：``(绝对路径, mtime)``（供保留期清理）。"""
    root = storage_cache_dir()
    if not os.path.isdir(root):
        return
    for dirpath, _dirnames, filenames in os.walk(root):
        for filename in filenames:
            path = os.path.join(dirpath, filename)
            try:
                yield path, os.path.getmtime(path)
            except OSError:  # noqa: BLE001 文件在遍历中被清理
                continue


def clean_storage_cache(keep_days: int = 7, batch: int = 2000) -> int:
    """清理远端对象本地缓存（按文件 mtime 保留期淘汰），返回删除文件数。"""
    if not keep_days or keep_days <= 0:
        return 0
    deadline = time.time() - keep_days * 86400
    removed = 0
    for path, mtime in iter_cached_files():
        if removed >= batch:
            break
        if mtime >= deadline:
            continue
        try:
            os.remove(path)
            removed += 1
        except OSError:  # noqa: BLE001
            continue
    # 清理空目录（下载目录按内容哈希分散，长期不清理会残留空壳）
    root = storage_cache_dir()
    if os.path.isdir(root):
        for name in os.listdir(root):
            directory = os.path.join(root, name)
            try:
                if os.path.isdir(directory) and not os.listdir(directory):
                    os.rmdir(directory)
            except OSError:  # noqa: BLE001
                continue
    if removed:
        logger.info("clean storage cache removed:%s", removed)
    return removed


def storage_url(name: str) -> str:
    """存储对象的访问 URL（本地 = MEDIA_URL 相对地址；远端 = 对象存储 / CDN 地址）。"""
    try:
        return default_storage.url(name)
    except Exception:  # noqa: BLE001
        return ""


def storage_presigned_url(
    name: str | None, expires: int = PRESIGN_DEFAULT_EXPIRES, download_filename: str | None = None
) -> str | None:
    """对象存储预签名直连 URL（P-4）：**仅 `s3` 后端可用**，其余返回 ``None`` 由调用方回退。

    - 用途：大文件下载 / 预览绕过服务端中转（浏览器直连对象存储，签名短时效）；
    - 本地 / `mirror` 后端无预签名概念（文件就在本地盘）→ 返回 ``None``；
    - ``boto3`` 为可选依赖（extras: storage），缺失时返回 ``None``（链路自动回退，不报错）；
    - 预签名地址会在一段时间内绕过应用鉴权 → **仅由受鉴权端点在完成鉴权与审计后签发**，
      且默认有效期短（10 分钟）。
    """
    if not name or storage_is_local():
        return None
    from common.storage.backend import BACKEND_S3, storage_config

    config = storage_config()
    if config.get("backend") != BACKEND_S3 or not config.get("bucket"):
        return None
    try:
        import boto3
        from botocore.config import Config as BotoConfig
    except ImportError:  # 可选依赖未安装（extras: storage）
        logger.info("presigned url unavailable: boto3 not installed")
        return None
    try:
        client = boto3.client(
            "s3",
            endpoint_url=config.get("endpoint") or None,
            aws_access_key_id=config.get("access_key") or None,
            aws_secret_access_key=config.get("secret_key") or None,
            region_name=config.get("region") or None,
            config=BotoConfig(s3={"addressing_style": config.get("addressing_style") or "auto"}),
        )
        params = {"Bucket": config["bucket"], "Key": name}
        if download_filename:
            params["ResponseContentDisposition"] = f"attachment; filename*=UTF-8''{quote(download_filename)}"
        return client.generate_presigned_url("get_object", Params=params, ExpiresIn=int(expires))
    except Exception as e:  # noqa: BLE001 签名失败按不可用处理（调用方回退服务端中转）
        logger.warning("generate presigned url failed. name:%s error:%s", name, e)
        return None


def storage_probe():
    """存储后端可达性探测，返回 ``(ok, cost)``（与 health 探测同口径）。"""
    t1 = time.time()
    try:
        if storage_is_local():
            root = str(settings.MEDIA_ROOT)
            os.makedirs(root, exist_ok=True)
            probe = os.path.join(root, ".storage_probe")
            with open(probe, "w", encoding="utf-8") as file:
                file.write("ok")
            os.remove(probe)
            return True, time.time() - t1
        # 远端：一次 exists 往返（对象是否存在都算可达，异常才算不可达）
        default_storage.exists(".storage-probe")
        return True, time.time() - t1
    except Exception as e:  # noqa: BLE001
        return False, str(e)
