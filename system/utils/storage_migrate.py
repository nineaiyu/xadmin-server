#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""文件存储搬迁：本地磁盘 <-> 对象存储批量搬迁 + 校验。

搬迁口径：

- **幂等 / 断点续搬**：逐文件判断目标是否已存在且大小一致 → 跳过；重复执行安全，
  中断后重跑即从断点继续（不依赖额外状态表）；
- **默认不覆盖**：目标存在但大小不一致 → 记为 ``conflict``（需人工确认或
  ``--overwrite`` 显式覆盖），避免把目标端的较新内容静默冲掉；
- **校验**：``--verify`` 只比对存在性与大小；``--verify --md5`` 额外逐文件比对
  md5（需下载目标对象，慢，用于搬迁后的抽检 / 全量核对）。

对象范围：``UploadFile``（含软删记录）中 ``filepath`` 非空的行——历史数据的
物理文件也必须搬迁，否则回收站恢复 / 变更历史会丢文件。
"""

import hashlib

from django.core.files.base import File
from django.core.files.storage import FileSystemStorage, Storage

from common.storage import BACKEND_LOCAL, storage_config
from common.utils import get_logger

logger = get_logger(__name__)

COPY_CHUNK_SIZE = 1024 * 1024


def get_local_storage() -> FileSystemStorage:
    """本地磁盘后端（location = MEDIA_ROOT），搬迁的固定一端。"""
    from django.conf import settings

    return FileSystemStorage(location=str(settings.MEDIA_ROOT), base_url=str(settings.MEDIA_URL))


def get_remote_storage() -> Storage:
    """当前配置的远端后端；未启用对象存储时抛可读错误（命令据此退出）。"""
    from common.storage import build_delegate

    config = storage_config()
    if config.get("backend") == BACKEND_LOCAL:
        raise RuntimeError("未启用对象存储（FILE_STORAGE_BACKEND=s3 后可用）")
    delegate = build_delegate(config)
    if isinstance(delegate, FileSystemStorage):
        raise RuntimeError("对象存储后端不可用（缺 django-storages / 配置不全，见启动日志告警）")
    return delegate


def iter_upload_names(batch_size: int = 500, limit: int | None = None):
    """遍历需搬迁的存储对象名（``UploadFile.filepath.name`` 去重后按 pk 顺序）。"""
    from system.models import UploadFile

    seen = set()
    count = 0
    queryset = UploadFile.all_objects.exclude(filepath="").order_by("pk")
    for row in queryset.iterator(chunk_size=batch_size):
        name = getattr(row.filepath, "name", "") if row.filepath else ""
        if not name or name in seen:
            continue
        seen.add(name)
        yield row.pk, name
        count += 1
        if limit is not None and count >= limit:
            return


def _md5(file_obj) -> str:
    digest = hashlib.md5()  # noqa: S324 文件指纹（非安全用途），与 UploadFile.md5sum 口径一致
    while True:
        chunk = file_obj.read(COPY_CHUNK_SIZE)
        if not chunk:
            break
        digest.update(chunk)
    return digest.hexdigest()


def object_md5(storage: Storage, name: str) -> str | None:
    try:
        with storage.open(name, "rb") as file_obj:
            return _md5(file_obj)
    except Exception:  # noqa: BLE001 读取失败由调用方按校验失败处理
        logger.warning("compute md5 failed. storage:%s name:%s", storage.__class__.__name__, name, exc_info=True)
        return None


def _same_size(source: Storage, target: Storage, name: str) -> bool:
    try:
        return source.exists(name) and target.exists(name) and source.size(name) == target.size(name)
    except Exception:  # noqa: BLE001 任一端不可达按「不确定」处理（走复制分支并由 save 报错）
        return False


def _copy_object(source: Storage, target: Storage, name: str, overwrite: bool) -> str:
    """复制单个对象，返回 ``copied / skipped / conflict``。"""
    if not source.exists(name):
        return "missing_source"
    if target.exists(name):
        if _same_size(source, target, name):
            return "skipped"
        if not overwrite:
            return "conflict"
        target.delete(name)
    with source.open(name, "rb") as file_obj:
        target.save(name, File(file_obj, name=name))
    return "copied"


def _verify_object(source: Storage, target: Storage, name: str, check_md5: bool) -> str:
    """校验单个对象，返回 ``ok / missing / size_mismatch / md5_mismatch``。"""
    if not target.exists(name):
        return "missing"
    try:
        if source.exists(name) and source.size(name) != target.size(name):
            return "size_mismatch"
    except Exception:  # noqa: BLE001 源不可达时以目标可读为准
        pass
    if check_md5:
        source_md5 = object_md5(source, name)
        target_md5 = object_md5(target, name)
        if source_md5 and target_md5 and source_md5 != target_md5:
            return "md5_mismatch"
    return "ok"


def migrate_uploads(
    source: Storage,
    target: Storage,
    *,
    dry_run: bool = False,
    limit: int | None = None,
    batch_size: int = 500,
    overwrite: bool = False,
    verify: bool = False,
    check_md5: bool = False,
) -> dict:
    """执行搬迁 / 校验，返回统计与失败明细。

    ``dry_run`` 只统计不写入（会读取源端做存在性 / 大小判断）。
    """
    stats = {
        "scanned": 0,
        "copied": 0,
        "skipped": 0,
        "conflict": 0,
        "missing_source": 0,
        "failed": 0,
        "verify_ok": 0,
        "verify_failed": 0,
        "details": [],
    }
    for _pk, name in iter_upload_names(batch_size=batch_size, limit=limit):
        stats["scanned"] += 1
        try:
            if verify:
                result = _verify_object(source, target, name, check_md5)
                if result == "ok":
                    stats["verify_ok"] += 1
                else:
                    stats["verify_failed"] += 1
                    stats["details"].append({"name": name, "result": result})
                continue
            if dry_run:
                result = "skipped" if _same_size(source, target, name) else "copied"
                stats[result] += 1
                continue
            result = _copy_object(source, target, name, overwrite)
            stats[result] = stats.get(result, 0) + 1
            if result in ("conflict", "missing_source"):
                stats["details"].append({"name": name, "result": result})
        except Exception as e:  # noqa: BLE001 单文件失败不中断整批（明细供人工重试）
            stats["failed"] += 1
            stats["details"].append({"name": name, "result": "failed", "error": str(e)})
            logger.warning("storage migrate failed. name:%s", name, exc_info=True)
    return stats


def summary_line(stats: dict, direction: str, dry_run: bool = False, verify: bool = False) -> str:
    """人类可读的统计行（命令输出用）。"""
    if verify:
        return (
            f"[storage_migrate] direction={direction} verify=true scanned={stats['scanned']} "
            f"ok={stats['verify_ok']} failed={stats['verify_failed']}"
        )
    prefix = "[storage_migrate] dry-run" if dry_run else "[storage_migrate]"
    return (
        f"{prefix} direction={direction} scanned={stats['scanned']} copied={stats['copied']} "
        f"skipped={stats['skipped']} conflict={stats['conflict']} "
        f"missing_source={stats['missing_source']} failed={stats['failed']}"
    )
