#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""分片上传 / 断点续传协议内核（file/views/admin/file.py 的 chunk-* action 复用）。

协议（全部挂在 ``/api/file/file/chunk/*``，权限口径与文件中心父级一致）：

    POST chunk/init      {filename, filesize, total_chunks, chunk_size, md5?}
                          → {pk, received:[已收分片索引]}（断点续传入口）
    POST chunk/part      multipart: session, index, file（单分片，幂等重传）
    POST chunk/complete  {pk, md5?} → UploadFile 载荷（与单请求 upload 同一序列化器）
    POST chunk/abort     {pk} → 清理分片与会话

安全口径与单请求上传完全同源，不出现第二套规则：

- 扩展名黑白名单与大小上限：init 期即校验（``validate_upload_extension`` /
  ``get_upload_max_size``），complete 合并后再次校验扩展名（纵深）；
- 配额与去重、分类、落库：complete 走既有 ``store_upload_file`` 管线
  （预计算 md5 传入，避免合并文件二次全量读）；
- 分片落存储（``upload_sessions/<pk>/part-<index>``），并发重传同分片由
  分片行唯一约束兜底幂等；完整性与断点状态以分片行（行数=总分片数、
  尺寸之和=文件大小）为准，不信任客户端清单。

过期未完成会话由 ``auto_clean_upload_sessions`` 定时清理（分片文件 + 会话行）。
"""

import hashlib
import mimetypes
import os
import tempfile
from typing import Any

from django.core.files.base import ContentFile, File
from django.core.files.storage import default_storage
from django.db import transaction
from django.db.models import Sum
from django.utils.translation import gettext_lazy as _

from common.core.throttle import allow_by_identity
from common.utils import get_logger
from file.models import UploadFile, UploadSession, UploadSessionPart
from file.utils.file_audit import validate_upload_extension
from file.utils.upload_store import (
    INVALID_CODE,
    QUOTA_EXCEEDED_CODE,
    SIZE_EXCEEDED_CODE,
    UploadError,
    get_upload_max_size,
    get_user_count_limit,
    get_user_quota_mb,
    sanitize_filename,
    store_upload_file,
)

logger = get_logger(__name__)

# 单分片尺寸钳制：过小 → 请求数爆炸；过大 → 单请求体过大（与单请求直传形态无异）
MIN_CHUNK_SIZE = 1 * 1024 * 1024
MAX_CHUNK_SIZE = 20 * 1024 * 1024
DEFAULT_CHUNK_SIZE = 5 * 1024 * 1024
# 分片数量上限：防 min chunk × 超大 filesize 的请求数滥用（10MB×20MB chunk 已覆盖政策上限）
MAX_TOTAL_CHUNKS = 20000
STORAGE_DIR = "upload_sessions"

# complete 一致性校验失败业务码：分片拼合结果与声明不符（内容损坏/串分片）
CHECKSUM_MISMATCH_CODE = 1007


def part_storage_name(session_pk: Any, index: int) -> str:
    """分片在存储侧的统一命名（local / s3 / mirror 后端同形）。"""
    return f"{STORAGE_DIR}/{session_pk}/part-{index:08d}"


def _validate_plan(filename: str, filesize: int, total_chunks: int, chunk_size: int, user_obj: Any) -> None:
    """init 期政策校验：扩展名 / 大小上限 / 配额 / 分片计划合理性，fail-closed。

    配额与数量上限在此前置判定（与单请求 ``check_upload_limits`` 同口径）——
    complete 阶段不再二次校验，避免「传完才被告知配额不足」的体验与存储浪费。
    """
    extension_error = validate_upload_extension(filename)
    if extension_error:
        raise UploadError(INVALID_CODE, extension_error)
    max_size = get_upload_max_size(user_obj)
    if filesize > max_size:
        raise UploadError(SIZE_EXCEEDED_CODE, _("upload file size cannot exceed {}").format(max_size))
    quota_mb = get_user_quota_mb(user_obj) or 0
    if quota_mb:
        used = UploadFile.objects.filter(creator=user_obj).aggregate(size=Sum("filesize"))["size"] or 0
        if used + filesize > quota_mb * 1024 * 1024:
            raise UploadError(
                QUOTA_EXCEEDED_CODE,
                _("Storage quota exceeded ({} MB), please clean up and retry").format(quota_mb),
            )
    count_limit = get_user_count_limit(user_obj) or 0
    if count_limit:
        used_count = UploadFile.objects.filter(creator=user_obj).count()
        if used_count + 1 > count_limit:
            raise UploadError(
                QUOTA_EXCEEDED_CODE,
                _("File count limit exceeded ({}), please clean up and retry").format(count_limit),
            )
    if filesize <= 0:
        raise UploadError(INVALID_CODE, _("Invalid upload plan"))
    if not MIN_CHUNK_SIZE <= chunk_size <= MAX_CHUNK_SIZE:
        raise UploadError(
            INVALID_CODE,
            _("Chunk size must be between {} and {} bytes").format(MIN_CHUNK_SIZE, MAX_CHUNK_SIZE),
        )
    expect_chunks = (filesize + chunk_size - 1) // chunk_size
    if total_chunks != expect_chunks:
        raise UploadError(
            INVALID_CODE,
            _("total_chunks does not match filesize/chunk_size (expected {})").format(expect_chunks),
        )
    if total_chunks > MAX_TOTAL_CHUNKS:
        raise UploadError(INVALID_CODE, _("Too many chunks (max {})").format(MAX_TOTAL_CHUNKS))


def init_session(
    user_obj: Any,
    *,
    filename: str,
    filesize: int,
    total_chunks: int,
    chunk_size: int,
    md5sum: str = "",
    mime_type: str = "",
) -> tuple[Any, list[Any], bool]:
    """创建或命中（断点续传）分片会话，返回 ``(session, received_indices, created)``。

    命中条件：同属主 + 同名同大小 + pending 的既有会话（刷新页面 / 网络中断后
    重新 init 即续传，不需要额外查询端点）。
    """
    filename = sanitize_filename(filename)
    chunk_size = min(max(int(chunk_size), MIN_CHUNK_SIZE), MAX_CHUNK_SIZE)
    _validate_plan(filename, filesize, int(total_chunks), chunk_size, user_obj)
    # 防双击/并发重复 init：同用户每秒一次；命中续传路径本就幂等，仅收敛写放大
    if not allow_by_identity(user_obj.pk, scope="upload_chunk_init", limit=5, window_seconds=1):
        raise UploadError(INVALID_CODE, _("Too many requests, please retry later"))
    existing = (
        UploadSession.objects.filter(
            creator=user_obj, filename=filename, filesize=filesize, status=UploadSession.Status.PENDING
        )
        .order_by("-created_time")
        .first()
    )
    if existing:
        received = list(existing.parts.values_list("index", flat=True))
        return existing, received, False
    session = UploadSession.objects.create(
        creator=user_obj,
        filename=filename,
        filesize=filesize,
        total_chunks=int(total_chunks),
        chunk_size=chunk_size,
        md5sum=str(md5sum or "")[:36],
        mime_type=str(mime_type or "")[:255],
        status=UploadSession.Status.PENDING,
    )
    return session, [], True


def store_part(user_obj: Any, session: Any, index: Any, file_obj: Any) -> int:
    """写单个分片（幂等：同 index 重传覆盖同名存储对象，行 get_or_create）。

    返回当前已收分片数。index 越界 / 分片超尺寸（末片除外）一律拒绝。
    """
    if session.creator_id != user_obj.pk or session.status != UploadSession.Status.PENDING:
        raise UploadError(INVALID_CODE, _("Invalid upload session"))
    index = int(index)
    if not 0 <= index < session.total_chunks:
        raise UploadError(INVALID_CODE, _("Chunk index out of range"))
    size = file_obj.size
    is_last = index == session.total_chunks - 1
    if size > session.chunk_size or (not is_last and size != session.chunk_size):
        raise UploadError(INVALID_CODE, _("Chunk size mismatch"))
    UploadSessionPart.objects.get_or_create(session=session, index=index, defaults={"size": size})
    name = part_storage_name(session.pk, index)
    if default_storage.exists(name):
        default_storage.delete(name)
    default_storage.save(name, ContentFile(file_obj.read()))
    received: int = session.parts.count()
    return received


@transaction.atomic  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
def complete_session(user_obj: Any, session_pk: Any, *, declared_md5: str = "") -> tuple[Any, Any]:
    """合并分片并走既有上传内核落库；会话行锁内串行（与并发 complete/abort 互斥）。

    校验分片完整性（行数 / 尺寸和 / 逐片尺寸）→ 流式合并并计算 md5 → 声明指纹
    一致性校验（可选）→ ``store_upload_file``（配额 / 去重 / 分类 / 落库）→
    清理分片（存储 + 行）→ 会话置 completed。
    """
    # 注意：锁定查询不得 select_related("upload")——upload 为可空 FK，会生成
    # LEFT OUTER JOIN，PG 拒绝对外连接可空侧加行锁（FOR UPDATE cannot be applied
    # to the nullable side of an outer join；sqlite 无锁语义静默通过，2026-10-01
    # nightly PG 档首轮暴露）。upload 列仅在 complete 时赋值、PENDING 会话恒为
    # NULL，此处预取本无消费者，直接去掉即可；of=("self",) 方案在 sqlite 门禁
    # 上会 NotSupportedError，不可用。
    session = UploadSession.objects.select_for_update().filter(pk=session_pk).first()
    if not session or session.creator_id != user_obj.pk:
        raise UploadError(INVALID_CODE, _("Invalid upload session"))
    if session.status != UploadSession.Status.PENDING:
        raise UploadError(INVALID_CODE, _("Upload session already finished"))
    extension_error = validate_upload_extension(session.filename)
    if extension_error:
        session.status = UploadSession.Status.ABORTED
        session.save(update_fields=["status", "updated_time"])
        _purge_parts(session)
        raise UploadError(INVALID_CODE, extension_error)

    parts = list(session.parts.order_by("index"))
    if len(parts) != session.total_chunks or [p.index for p in parts] != list(range(session.total_chunks)):
        raise UploadError(INVALID_CODE, _("Upload is incomplete"))
    if sum(p.size for p in parts) != session.filesize:
        raise UploadError(INVALID_CODE, _("Upload is incomplete"))

    declared = str(declared_md5 or session.md5sum or "").strip().lower()
    md5sum, upload = _merge_and_store(user_obj, session, declared)
    session.status = UploadSession.Status.COMPLETED
    session.upload = upload
    session.md5sum = md5sum
    session.save(update_fields=["status", "upload", "md5sum", "updated_time"])
    _purge_parts(session)
    return upload, session


def _merge_and_store(user_obj: Any, session: Any, declared_md5: str) -> tuple[str, Any]:
    """合并分片到临时文件并落库；md5 在合并时同步计算（整文件只读一遍）。"""
    digest = hashlib.md5()
    fd, temp_path = tempfile.mkstemp(prefix="xadmin-merge-")
    os.close(fd)
    try:
        with open(temp_path, "wb") as target:
            for part in session.parts.order_by("index"):
                name = part_storage_name(session.pk, part.index)
                if not default_storage.exists(name):
                    raise UploadError(INVALID_CODE, _("Upload is incomplete"))
                with default_storage.open(name, "rb") as part_file:
                    for chunk in iter(lambda: part_file.read(1024 * 1024), b""):
                        target.write(chunk)
                        digest.update(chunk)
        md5sum = digest.hexdigest()
        if declared_md5 and declared_md5 != md5sum:
            raise UploadError(CHECKSUM_MISMATCH_CODE, _("Upload checksum mismatch, please retry"))
        mime_type = session.mime_type or (mimetypes.guess_type(session.filename)[0] or "application/octet-stream")
        with open(temp_path, "rb") as raw:
            # 内核按 UploadedFile 鸭子类型消费（name / size / content_type / chunks()）：
            # File 子类注入 content_type，size 由临时文件实测（与合并结果一致）
            merged = _MergedFile(raw, name=session.filename, content_type=mime_type)
            # md5 已在合并期计算：传入内核避免二次全量读
            upload, _dedup_hit = store_upload_file(user_obj, merged, md5sum=md5sum)
        return md5sum, upload
    finally:
        try:
            os.unlink(temp_path)
        except OSError:
            logger.warning(f"merge temp file cleanup failed: {temp_path}")


class _MergedFile(File):
    """合并产物到上传内核的适配：UploadedFile 鸭子类型（content_type 注入）。"""

    def __init__(self, file: Any, name: str, content_type: str) -> None:
        super().__init__(file, name=name)
        self.content_type = content_type


def abort_session(user_obj: Any, session_pk: Any) -> bool:
    """放弃会话：清理分片（存储 + 行）并置 aborted；不存在/非本人返回 False。"""
    with transaction.atomic():
        session = UploadSession.objects.select_for_update().filter(pk=session_pk).first()
        if not session or session.creator_id != user_obj.pk:
            return False
        if session.status != UploadSession.Status.PENDING:
            return True
        session.status = UploadSession.Status.ABORTED
        session.save(update_fields=["status", "updated_time"])
        _purge_parts(session)
    return True


def _purge_parts(session: Any) -> None:
    """删除会话的全部分片（存储对象 + 行）；存储清理失败只告警（行已删，不阻塞）。"""
    names = [part_storage_name(session.pk, part.index) for part in session.parts.all()]
    session.parts.all().delete()
    for name in names:
        try:
            default_storage.delete(name)
        except Exception:  # noqa: BLE001 存储清理失败不影响会话状态流转
            logger.warning(f"upload part cleanup failed: {name}")


def auto_clean_upload_sessions(clean_day: int = 1) -> dict[str, int]:
    """清理过期分片会话：pending 超 N 天（分片 + 行）与全部终态会话行。

    分片是临时数据，会话完成即清理；本任务兜底「客户端中途放弃」与
    「complete/abort 从未到达」两类残留，防止存储与表被未完成会话蚕食。
    """
    from datetime import timedelta

    from django.utils import timezone

    deadline = timezone.now() - timedelta(days=clean_day)
    stale = UploadSession.objects.filter(status=UploadSession.Status.PENDING, created_time__lte=deadline)
    count = 0
    for session in stale.iterator():
        with transaction.atomic():
            locked = UploadSession.objects.select_for_update().filter(pk=session.pk).first()
            if not locked or locked.status != UploadSession.Status.PENDING:
                continue
            _purge_parts(locked)
            locked.delete()
            count += 1
    finished = UploadSession.objects.filter(
        status__in=[UploadSession.Status.COMPLETED, UploadSession.Status.ABORTED],
        created_time__lte=deadline,
    )
    finished_count = finished.delete()[0]
    return {"stale_aborted": count, "finished_deleted": finished_count}
