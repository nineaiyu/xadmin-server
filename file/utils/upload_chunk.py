#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""分片上传 / 断点续传协议内核（file/views/admin/file.py 的 chunk-* action 复用）。

协议（全部挂在 ``/api/file/file/chunk/*``，权限口径与文件中心父级一致）：

    POST chunk/init      {filename, filesize, total_chunks, chunk_size, content_hash, md5?}
                          → {pk, received:[已收分片索引]}（断点续传入口）

``content_hash`` = 客户端整文件 SHA-256（64 位十六进制），是断点续传的会话键：
命中条件为「同属主 + 同名同大小 + 同内容指纹」，complete 合并后复算比对。
    POST chunk/part      multipart: session, index, file（单分片，幂等重传）
    POST chunk/complete  {pk, md5?} → UploadFile 载荷（与单请求 upload 同一序列化器）
    POST chunk/abort     {pk} → 清理分片与会话

安全口径与单请求上传完全同源，不出现第二套规则：

- 扩展名黑白名单与大小上限：init 期即校验（``validate_upload_extension`` /
  ``get_upload_max_size``），complete 合并后再次校验扩展名（纵深）；
- 内容 magic 抽检：init 只有元信息（无内容），校验落在 complete——
  ``validate_upload_content`` 直接读第一分片头部，伪装扩展名的二进制内容
  在落库前即拒（会话按不可复用作废并清理分片，与扩展名失败同口径）；
- 配额与去重、分类、落库：complete 走既有 ``store_upload_file`` 管线
  （预计算 md5 传入，避免合并文件二次全量读）；
- 分片落存储（``upload_sessions/<pk>/part-<index>``），并发重传同分片由
  分片行唯一约束兜底幂等；完整性与断点状态以分片行（行数=总分片数、
  尺寸之和=文件大小）为准，不信任客户端清单。

complete 为**两段式事务**（审查修复 BE-M3）：短事务 CAS 认领 ``PENDING →
MERGING`` → 锁外合并 I/O → 短事务收口 ``MERGING → COMPLETED``——合并时长不再
占用数据库事务与会话行锁；崩溃残留（认领超时）可重试 complete 续跑 / abort
作废 / 清理任务按保留期回收。过期未完成会话由 ``auto_clean_upload_sessions``
定时清理（分片文件 + 会话行）。
"""

import hashlib
import mimetypes
import os
import re
import tempfile
from typing import Any

from django.core.files.base import ContentFile, File
from django.core.files.storage import default_storage
from django.db import models, transaction
from django.db.models import Sum
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from common.core.throttle import allow_by_identity
from common.utils import get_logger
from file.models import UploadFile, UploadSession, UploadSessionPart
from file.utils.file_audit import validate_upload_content, validate_upload_extension
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

# MERGING 认领的过期阈值（秒）：超过即视为「合并进程崩溃」的残留——下一次
# complete 可重新认领续跑（分片未删）、abort 可作废回收、清理任务按保留期兜底。
# 取值口径：> 常规 HTTP 请求内的合并时长（受 gunicorn/网关超时约束），< 清理任务的天级兜底。
MERGE_STALE_SECONDS = 10 * 60


class ContentHashMismatch(UploadError):
    """合并结果与 init 声明的内容指纹不一致：会话里的分片属于另一份内容，不可复用。"""


# 内容指纹：客户端整文件 SHA-256 的十六进制（与前端 utils/upload/chunked.ts 同算法）
CONTENT_HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def normalize_content_hash(value: Any) -> str:
    """校验并归一内容指纹（小写十六进制 SHA-256）。

    init 必填：断点续传的命中条件必须是「同一份内容」，只按同名同大小命中的
    旧口径会把两个文件的分片拼在一起。旧客户端（不传指纹）在这里被拒绝，
    提示刷新后重试，不给「静默降级成不安全口径」的余地。
    """
    raw = str(value or "").strip().lower()
    if not raw:
        raise UploadError(INVALID_CODE, _("File fingerprint is required, please refresh the page and retry"))
    if not CONTENT_HASH_PATTERN.match(raw):
        raise UploadError(INVALID_CODE, _("Invalid file fingerprint"))
    return raw


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
    # 配额/数量按全量行统计（含回收站）：软删只标记、物理文件仍占磁盘，
    # 按活动行统计会允许「上传→删除→再上传」绕过上限（与单请求上传同口径）。
    owner_files = UploadFile.all_objects.filter(creator=user_obj)
    if quota_mb:
        used = owner_files.aggregate(size=Sum("filesize"))["size"] or 0
        if used + filesize > quota_mb * 1024 * 1024:
            raise UploadError(
                QUOTA_EXCEEDED_CODE,
                _("Storage quota exceeded ({} MB), please clean up and retry").format(quota_mb),
            )
    count_limit = get_user_count_limit(user_obj) or 0
    if count_limit:
        used_count = owner_files.count()
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
    content_hash: str = "",
) -> tuple[Any, list[Any], bool]:
    """创建或命中（断点续传）分片会话，返回 ``(session, received_indices, created)``。

    命中条件：同属主 + 同名同大小 + **同内容指纹** + pending 的既有会话（刷新页面 /
    网络中断后重新 init 即续传，不需要额外查询端点）。内容指纹必填（见
    ``normalize_content_hash``）：同名同大小但内容不同的两个文件必须落到两个会话。
    """
    filename = sanitize_filename(filename)
    try:
        filesize = int(filesize)
        total_chunks = int(total_chunks)
        chunk_size = int(chunk_size)
    except (TypeError, ValueError):
        raise UploadError(INVALID_CODE, _("Invalid upload plan")) from None
    # 不静默夹取：超范围直接拒绝，避免客户端按被改写后的 chunk_size 计算出误导错误
    _validate_plan(filename, filesize, total_chunks, chunk_size, user_obj)
    content_hash = normalize_content_hash(content_hash)
    # 防双击/并发重复 init：同用户每秒一次；命中续传路径本就幂等，仅收敛写放大
    if not allow_by_identity(user_obj.pk, scope="upload_chunk_init", limit=5, window_seconds=1):
        raise UploadError(INVALID_CODE, _("Too many requests, please retry later"))
    existing = (
        UploadSession.objects.filter(
            creator=user_obj,
            filename=filename,
            filesize=filesize,
            content_hash=content_hash,
            status=UploadSession.Status.PENDING,
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
        content_hash=content_hash,
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
    try:
        index = int(index)
    except (TypeError, ValueError):
        # 非数字分片索引按业务错误拒绝（裸 ValueError 会冒泡成 500）
        raise UploadError(INVALID_CODE, _("Invalid chunk index")) from None
    if not 0 <= index < session.total_chunks:
        raise UploadError(INVALID_CODE, _("Chunk index out of range"))
    size = file_obj.size
    is_last = index == session.total_chunks - 1
    if size > session.chunk_size or (not is_last and size != session.chunk_size):
        raise UploadError(INVALID_CODE, _("Chunk size mismatch"))
    UploadSessionPart.objects.update_or_create(session=session, index=index, defaults={"size": size})
    name = part_storage_name(session.pk, index)
    if default_storage.exists(name):
        default_storage.delete(name)
    default_storage.save(name, ContentFile(file_obj.read()))
    received: int = session.parts.count()
    return received


def _claim_for_complete(user_obj: Any, session_pk: Any) -> tuple[Any, Any]:
    """complete 的阶段一 + 认领：读校验（属主 / 扩展名 / magic / 分片行完整性）与
    CAS ``PENDING → MERGING``，返回 ``(session, parts)``。失败口径与历史一致：
    扩展名 / magic 失败作废会话并清理分片，分片不完整保持 PENDING 可续传；
    新鲜 MERGING（他方合并中）与终态给出可读错误；认领超时的 MERGING 残留先原子
    退回 PENDING 再按正常流程续跑（认领时间前置条件防并发重开）。
    """
    session = UploadSession.objects.filter(pk=session_pk).first()
    if not session or session.creator_id != user_obj.pk:
        raise UploadError(INVALID_CODE, _("Invalid upload session"))
    if session.status == UploadSession.Status.MERGING and _merge_claim_expired(session):
        reopened = UploadSession.objects.filter(
            pk=session.pk, status=UploadSession.Status.MERGING, updated_time__lt=_merge_stale_cutoff()
        ).update(status=UploadSession.Status.PENDING, updated_time=timezone.now())
        if not reopened:
            raise UploadError(INVALID_CODE, _("Upload session already finished"))
        session = UploadSession.objects.filter(pk=session_pk).first()
    if session is None or session.status != UploadSession.Status.PENDING:
        if session is not None and session.status == UploadSession.Status.MERGING:
            # 新鲜认领（他方合并进行中）：与认领 CAS 失败同一可读口径
            raise UploadError(INVALID_CODE, _("Upload is being merged, please retry later"))
        raise UploadError(INVALID_CODE, _("Upload session already finished"))
    extension_error = validate_upload_extension(session.filename)
    if extension_error:
        _transition_and_purge(session, UploadSession.Status.ABORTED)
        raise UploadError(INVALID_CODE, extension_error)
    # 内容 magic 抽检：读第一分片头部（与合并产物同头），伪装扩展名的二进制内容
    # 落库前即拒；会话不可复用（内容已在分片里，重传 complete 恒失败），按扩展名
    # 失败同口径作废并清理分片
    head_name = part_storage_name(session.pk, 0)
    if not default_storage.exists(head_name):
        raise UploadError(INVALID_CODE, _("Upload is incomplete"))
    with default_storage.open(head_name, "rb") as head_file:
        content_error = validate_upload_content(session.filename, head_file)
    if content_error:
        _transition_and_purge(session, UploadSession.Status.ABORTED)
        raise UploadError(INVALID_CODE, content_error)
    parts = list(session.parts.order_by("index"))
    if len(parts) != session.total_chunks or [p.index for p in parts] != list(range(session.total_chunks)):
        raise UploadError(INVALID_CODE, _("Upload is incomplete"))
    if sum(p.size for p in parts) != session.filesize:
        raise UploadError(INVALID_CODE, _("Upload is incomplete"))
    # 认领 CAS：并发 complete 只有一方进入合并（失败方按库内现状给出可读错误）
    claimed = UploadSession.objects.filter(
        pk=session.pk, status=UploadSession.Status.PENDING, creator_id=session.creator_id
    ).update(status=UploadSession.Status.MERGING, updated_time=timezone.now())
    if not claimed:
        refreshed = UploadSession.objects.filter(pk=session_pk).first()
        if refreshed is not None and refreshed.status == UploadSession.Status.MERGING:
            raise UploadError(INVALID_CODE, _("Upload is being merged, please retry later"))
        raise UploadError(INVALID_CODE, _("Upload session already finished"))
    return session, parts


def complete_session(user_obj: Any, session_pk: Any, *, declared_md5: str = "") -> tuple[Any, Any]:
    """合并分片并走既有上传内核落库（两段式事务，审查修复 BE-M3）。

    旧实现把「锁会话行 → 合并全部分片 → 落库」包在同一个数据库事务内（合并
    时长随文件体积线性增长，形成分钟级长事务与行锁持有）。现改为 CAS 状态机：
    短事务认领（``_claim_for_complete``）→ 锁外合并 I/O → 短事务收口
    ``MERGING → COMPLETED``；收口 CAS 失败即他方已收口（同 md5 同属主去重命中
    同一产物行），复用库内终态。失败口径：内容指纹不符 → 作废会话并清理分片；
    声明 md5 不符等其余异常 → 认领退回 PENDING（可重试）。
    """
    session, parts = _claim_for_complete(user_obj, session_pk)
    declared = str(declared_md5 or session.md5sum or "").strip().lower()
    try:
        md5sum, upload = _merge_and_store(user_obj, session, parts, declared)
    except ContentHashMismatch:
        # 分片属于另一份内容（串会话/损坏）：整个会话不可复用，作废并清理分片
        _transition_and_purge(session, UploadSession.Status.ABORTED, from_status=UploadSession.Status.MERGING)
        raise
    except Exception:
        # 其余失败（声明 md5 不符 / 存储抖动）：会话仍可复用——认领退回 PENDING
        UploadSession.objects.filter(pk=session_pk, status=UploadSession.Status.MERGING).update(
            status=UploadSession.Status.PENDING, updated_time=timezone.now()
        )
        raise
    # 收口 CAS：并发重认领窗口内他方先收口时，同 md5 同属主去重命中同一产物行，
    # 复用库内终态（不重复挂接）；分片行删除在收口事务内，存储对象删除在提交后
    with transaction.atomic():
        finalized = UploadSession.objects.filter(pk=session_pk, status=UploadSession.Status.MERGING).update(
            status=UploadSession.Status.COMPLETED, upload=upload, md5sum=md5sum, updated_time=timezone.now()
        )
        if finalized:
            UploadSessionPart.objects.filter(session_id=session_pk).delete()
    if not finalized:
        refreshed = UploadSession.objects.filter(pk=session_pk).first()
        if refreshed is not None and refreshed.status == UploadSession.Status.COMPLETED and refreshed.upload_id:
            return refreshed.upload, refreshed
        logger.warning("chunk upload finalize lost race, session superseded: %s", session_pk)
        raise UploadError(INVALID_CODE, _("Upload session already finished"))
    _delete_storage_objects([part_storage_name(session_pk, part.index) for part in parts])
    session = UploadSession.objects.filter(pk=session_pk).first()
    return upload, session


def _merge_and_store(user_obj: Any, session: Any, parts: Any, declared_md5: str) -> tuple[str, Any]:
    """合并分片到临时文件并落库；md5 与内容指纹在合并时同步计算（整文件只读一遍）。

    在**会话事务之外**执行（两段式的阶段二）：不持行锁、不占数据库事务；
    ``store_upload_file`` 落临时行（is_tmp），收口失败/崩溃的孤儿由
    ``auto_clean_tmp_file`` 按保留期回收（同 md5 同属主重试会去重命中）。
    """
    digest = hashlib.md5()
    content_digest = hashlib.sha256()
    fd, temp_path = tempfile.mkstemp(prefix="xadmin-merge-")
    os.close(fd)
    try:
        with open(temp_path, "wb") as target:
            for part in parts:
                name = part_storage_name(session.pk, part.index)
                if not default_storage.exists(name):
                    raise UploadError(INVALID_CODE, _("Upload is incomplete"))
                with default_storage.open(name, "rb") as part_file:
                    for chunk in iter(lambda: part_file.read(1024 * 1024), b""):
                        target.write(chunk)
                        digest.update(chunk)
                        content_digest.update(chunk)
        # 内容指纹是断点续传的唯一正确性依据：合并结果必须与 init 声明一致，
        # 否则说明这份会话里的分片属于另一份内容（同名同大小也不能复用）
        if content_digest.hexdigest() != session.content_hash:
            raise ContentHashMismatch(CHECKSUM_MISMATCH_CODE, _("Upload checksum mismatch, please retry"))
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
    """放弃会话：清理分片（存储 + 行）并置 aborted；不存在/非本人返回 False。

    - PENDING → ABORTED（既有语义，CAS 替代行锁）；
    - 认领超时的 MERGING（合并中断残留）→ ABORTED：崩溃后的用户侧出口；
    - 进行中的 MERGING（新鲜认领）→ 抛 UploadError：合并正在收口，不可中途作废
      （清理在途分片会与合并读取竞争）；视图层按业务码转可读响应。
    """
    session = UploadSession.objects.filter(pk=session_pk).first()
    if not session or session.creator_id != user_obj.pk:
        return False
    if session.status == UploadSession.Status.MERGING:
        if _merge_claim_expired(session):
            return _transition_and_purge(
                session, UploadSession.Status.ABORTED, from_status=UploadSession.Status.MERGING
            )
        raise UploadError(INVALID_CODE, _("Upload is being merged, please retry later"))
    if session.status != UploadSession.Status.PENDING:
        return True
    return _transition_and_purge(session, UploadSession.Status.ABORTED)


def _merge_stale_cutoff() -> Any:
    """MERGING 认领的过期时刻（now - MERGE_STALE_SECONDS）。"""
    return timezone.now() - timezone.timedelta(seconds=MERGE_STALE_SECONDS)


def _merge_claim_expired(session: Any) -> bool:
    """会话的 MERGING 认领是否已过期（阶段二/三之间进程崩溃的判定口径）。"""
    updated_time = getattr(session, "updated_time", None)
    return bool(updated_time and updated_time < _merge_stale_cutoff())


def _transition_and_purge(session: Any, to_status: Any, from_status: Any = None) -> bool:
    """CAS 状态迁移并清理分片（行删除在事务内，存储对象删除在事务提交后）。

    迁移条件默认 PENDING（complete 前置校验的作废路径）；合并期作废显式传
    ``from_status=MERGING``。CAS 不命中（他方已迁移）返回 False，不清理分片。
    """
    if from_status is None:
        from_status = UploadSession.Status.PENDING
    names: list[str] = []
    with transaction.atomic():
        updated = UploadSession.objects.filter(pk=session.pk, status=from_status).update(
            status=to_status, updated_time=timezone.now()
        )
        if updated:
            names = [
                part_storage_name(session.pk, index)
                for index in UploadSessionPart.objects.filter(session_id=session.pk)
                .values_list("index", flat=True)
                .order_by("index")
            ]
            UploadSessionPart.objects.filter(session_id=session.pk).delete()
    if updated:
        _delete_storage_objects(names)
    return bool(updated)


def _purge_parts(session: Any) -> None:
    """删除会话的全部分片（存储对象 + 行）；存储清理失败只告警（行已删，不阻塞）。"""
    names = [part_storage_name(session.pk, part.index) for part in session.parts.all()]
    session.parts.all().delete()
    _delete_storage_objects(names)


def _delete_storage_objects(names: Any) -> None:
    """批量删除分片存储对象；单个失败只告警，不影响其余对象与其余流程。"""
    for name in names:
        try:
            default_storage.delete(name)
        except Exception:  # noqa: BLE001 存储清理失败不影响会话状态流转
            logger.warning(f"upload part cleanup failed: {name}")


def auto_clean_upload_sessions(clean_day: int = 1) -> dict[str, int]:
    """清理过期分片会话：pending/merging 残留超 N 天（分片 + 行）与全部终态会话行。

    分片是临时数据，会话完成即清理；本任务兜底「客户端中途放弃」「complete/abort
    从未到达」与「合并进程崩溃后的 MERGING 残留」三类残留，防止存储与表被未完成
    会话蚕食。MERGING 按**认领时间**（updated_time）判过期：认领仍新鲜的会话可能
    正在合并，不做回收。
    """
    from datetime import timedelta

    deadline = timezone.now() - timedelta(days=clean_day)
    stale = UploadSession.objects.filter(
        models.Q(status=UploadSession.Status.PENDING, created_time__lte=deadline)
        | models.Q(status=UploadSession.Status.MERGING, updated_time__lte=deadline)
    )
    recoverable = (UploadSession.Status.PENDING, UploadSession.Status.MERGING)
    count = 0
    for session in stale.iterator():
        with transaction.atomic():
            locked = UploadSession.objects.select_for_update().filter(pk=session.pk).first()
            if not locked or locked.status not in recoverable:
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
