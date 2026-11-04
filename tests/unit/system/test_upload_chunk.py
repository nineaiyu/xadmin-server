# -*- coding:utf-8 -*-
"""分片上传 / 断点续传协议守护测试（协议内核见 file/utils/upload_chunk.py）。

覆盖：init 计划校验（大小上限 / 扩展名 / 分片数）、断点续传命中、分片幂等、
complete 合并落库与 md5 一致性、abort 清理、过期会话清理。
"""

import hashlib

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from django.utils.translation import gettext as _
from rest_framework.test import APIRequestFactory, force_authenticate

from file.models import UploadFile, UploadSession
from file.utils.upload_chunk import CHECKSUM_MISMATCH_CODE, part_storage_name
from file.utils.upload_store import UploadError
from file.views.admin.file import UploadFileViewSet

pytestmark = pytest.mark.django_db

FILE_URL = "/api/file/file"

CHUNK_SIZE = 4  # 测试口径用极小 chunk（走 init 入参直传，绕过 1MB 钳制由 mock 承担）


@pytest.fixture(autouse=True)
def _file_center_config(db):
    """与 test_upload_enhance 同口径：显式种 FILE_UPLOAD_SIZE 系统行。"""
    from common.core.config import SysConfig
    from system.models import SystemConfig

    SystemConfig.objects.create(key="FILE_UPLOAD_SIZE", value=10 * 1024 * 1024, inherit=True, is_active=True)
    yield
    SysConfig.set_value("FILE_STORAGE_QUOTA_MB", 0)
    SysConfig.set_value("FILE_UPLOAD_COUNT_LIMIT", 0)


@pytest.fixture
def _small_chunks(monkeypatch):
    """把分片尺寸钳制压到 4 字节，测试可用微小载荷走完整协议。"""
    from file.utils import upload_chunk

    monkeypatch.setattr(upload_chunk, "MIN_CHUNK_SIZE", 1)
    monkeypatch.setattr(upload_chunk, "MAX_CHUNK_SIZE", 1024)
    monkeypatch.setattr(upload_chunk, "DEFAULT_CHUNK_SIZE", 4)


def _view(action):
    return UploadFileViewSet.as_view({"post": action})


def _post(user, action, payload=None, files=None):
    factory = APIRequestFactory()
    if files:
        request = factory.post(f"{FILE_URL}/chunk/{action}", {**(payload or {}), **files}, format="multipart")
    else:
        request = factory.post(f"{FILE_URL}/chunk/{action}", payload or {}, format="json")
    force_authenticate(request, user=user)
    with transaction.atomic():
        return _view(action)(request)


DEFAULT_CONTENT = b"abcdefghij"


def _content_hash(content: bytes = DEFAULT_CONTENT) -> str:
    """整文件 SHA-256（与前端 computeFileHash 同算法：逐字节摘要）。"""
    return hashlib.sha256(content).hexdigest()


def _init(user, filename="big.bin", filesize=10, total_chunks=3, chunk_size=4, content_hash=None, **kwargs):
    """发起 chunk/init；成功返回 data 载荷，失败返回完整响应体（含 code/detail）。"""
    if content_hash is None:
        content_hash = _content_hash()
    response = _post(
        user,
        "chunk_init",
        {
            "filename": filename,
            "filesize": filesize,
            "total_chunks": total_chunks,
            "chunk_size": chunk_size,
            "content_hash": content_hash,
            **kwargs,
        },
    )
    assert response.status_code == 200, response.data
    return response.data.get("data") or response.data


def _part(user, session_pk, index, content):
    return _post(
        user,
        "chunk_part",
        {"session": session_pk, "index": index},
        files={"file": SimpleUploadedFile("part", content)},
    )


class TestChunkInit:
    def test_creates_session_and_resumes(self, superuser, _small_chunks):
        first = _init(superuser, filesize=10, total_chunks=3, chunk_size=4)
        assert first["created"] is True
        assert first["received"] == []
        # 同名同大小重新 init = 断点续传命中（收到已传分片清单）
        _part(superuser, first["session"], 0, b"abcd")
        second = _init(superuser, filesize=10, total_chunks=3, chunk_size=4)
        assert second["created"] is False
        assert second["session"] == first["session"]
        assert second["received"] == [0]

    def test_rejects_oversize_plan(self, superuser, _small_chunks):
        result = _init(superuser, filesize=99 * 1024 * 1024, total_chunks=1000, chunk_size=1024)
        assert result["code"] == 1003

    def test_rejects_bad_extension(self, superuser, _small_chunks):
        result = _init(superuser, filename="evil.sh", filesize=10, total_chunks=3, chunk_size=4)
        assert result["code"] == 1002

    def test_rejects_inconsistent_plan(self, superuser, _small_chunks):
        result = _init(superuser, filesize=10, total_chunks=2, chunk_size=4)
        assert result["code"] == 1002

    def test_same_name_size_different_content_starts_new_session(self, superuser, _small_chunks):
        """同名同大小但内容不同：不得命中旧会话（否则会拼出「旧前半 + 新后半」）。"""
        first = _init(superuser, filesize=10, total_chunks=3, chunk_size=4, content_hash=_content_hash(b"abcdefghij"))
        _part(superuser, first["session"], 0, b"abcd")

        other = _init(superuser, filesize=10, total_chunks=3, chunk_size=4, content_hash=_content_hash(b"ABCDEFGHIJ"))

        assert other["created"] is True
        assert other["session"] != first["session"]
        assert other["received"] == []

    def test_content_hash_required(self, superuser, _small_chunks):
        """旧客户端（不传指纹）被拒绝并给出可读原因，不静默降级成同名同大小口径。"""
        response = _post(
            superuser,
            "chunk_init",
            {"filename": "big.bin", "filesize": 10, "total_chunks": 3, "chunk_size": 4},
        )
        assert response.data["code"] == 1002
        assert str(response.data["detail"]) == str(_("File fingerprint is required, please refresh the page and retry"))
        assert not UploadSession.objects.exists()

    def test_malformed_content_hash_rejected(self, superuser, _small_chunks):
        response = _post(
            superuser,
            "chunk_init",
            {
                "filename": "big.bin",
                "filesize": 10,
                "total_chunks": 3,
                "chunk_size": 4,
                "content_hash": "not-a-hash",
            },
        )
        assert response.data["code"] == 1002
        assert str(response.data["detail"]) == str(_("Invalid file fingerprint"))

    def test_quota_and_count_include_soft_deleted_files(self, superuser, _small_chunks):
        """软删文件仍占磁盘：分片 init 必须按 all_objects 统计配额与数量。"""
        from django.core.files.base import ContentFile

        from common.core.config import SysConfig

        reused = UploadFile.objects.create(
            filename="old.bin",
            filesize=1,
            is_upload=True,
            creator=superuser,
            filepath=ContentFile(b"x", name="old.bin"),
        )
        reused.delete()
        SysConfig.set_value("FILE_UPLOAD_COUNT_LIMIT", 1)

        result = _init(superuser, filesize=10, total_chunks=3, chunk_size=4)

        assert result["code"] == 1004
        assert not UploadSession.objects.filter(creator=superuser).exists()


class TestChunkPart:
    def test_stores_and_is_idempotent(self, superuser, _small_chunks):
        plan = _init(superuser, filesize=10, total_chunks=3, chunk_size=4)
        session_pk = plan["session"]
        first = _part(superuser, session_pk, 0, b"abcd")
        assert first.data["data"]["received_count"] == 1
        # 同分片重传幂等（覆盖不重复计数）
        again = _part(superuser, session_pk, 0, b"abcd")
        assert again.data["data"]["received_count"] == 1
        other = _part(superuser, session_pk, 1, b"abcd")
        assert other.data["data"]["received_count"] == 2

    def test_rejects_out_of_range_index(self, superuser, _small_chunks):
        plan = _init(superuser, filesize=10, total_chunks=3, chunk_size=4)
        response = _part(superuser, plan["session"], 5, b"abcd")
        assert response.data["code"] == 1002

    def test_rejects_oversize_part(self, superuser, _small_chunks):
        plan = _init(superuser, filesize=10, total_chunks=3, chunk_size=4)
        response = _part(superuser, plan["session"], 0, b"a" * 100)
        assert response.data["code"] == 1002

    @pytest.mark.parametrize("bad_index", ["abc", "1.5", "-1", "", [1]])
    def test_rejects_malformed_index_as_business_error(self, superuser, _small_chunks, bad_index):
        """非数字/负分片索引走业务码而非 500（内核直调，绕过序列化层）。"""
        from file.utils.upload_chunk import store_part

        plan = _init(superuser, filesize=10, total_chunks=3, chunk_size=4)
        session = UploadSession.objects.get(pk=plan["session"])
        with pytest.raises(UploadError) as exc_info:
            store_part(superuser, session, bad_index, SimpleUploadedFile("part", b"abcd"))
        assert exc_info.value.code == 1002

    def test_other_user_cannot_feed_session(self, superuser, normal_user, _small_chunks):
        # API 层先被权限门拦下（normal_user 未授权文件中心）
        plan = _init(superuser, filesize=10, total_chunks=3, chunk_size=4)
        response = _part(normal_user, plan["session"], 0, b"abcd")
        assert response.status_code == 403
        # 权限门之内还有属主校验（纵深）：直接调内核断言
        from file.utils.upload_chunk import store_part

        session = UploadSession.objects.get(pk=plan["session"])
        with pytest.raises(UploadError) as exc_info:
            store_part(normal_user, session, 0, SimpleUploadedFile("part", b"abcd"))
        assert exc_info.value.code == 1002


class TestChunkComplete:
    def _full_upload(self, user, content=b"abcdefghij"):
        plan = _init(user, filesize=len(content), total_chunks=3, chunk_size=4)
        for index in range(3):
            chunk = content[index * 4 : (index + 1) * 4]
            response = _part(user, plan["session"], index, chunk)
            assert response.status_code == 200
        response = _post(user, "chunk_complete", {"pk": plan["session"]})
        assert response.status_code == 200, response.data
        return plan, response

    def test_merges_and_stores(self, superuser, _small_chunks):
        _plan, response = self._full_upload(superuser, b"abcdefghij")
        data = response.data["data"]
        assert data["filename"] == "big.bin"
        assert data["filesize"] == 10
        stored = UploadFile.objects.filter(pk=data["pk"]).first()
        assert stored and stored.md5sum
        with stored.filepath.open("rb") as fh:
            assert fh.read() == b"abcdefghij"
        session = UploadSession.objects.get(pk=_plan["session"])
        assert session.status == UploadSession.Status.COMPLETED
        assert session.upload_id == stored.pk
        assert session.parts.count() == 0
        # 分片存储对象已清理
        for index in range(3):
            from common.storage import storage_exists

            assert not storage_exists(part_storage_name(session.pk, index))

    def test_dedup_reuses_existing_copy(self, superuser, _small_chunks):
        _plan, response = self._full_upload(superuser, b"abcdefghij")
        first_md5 = response.data["data"]["md5sum"]
        _plan2, response2 = self._full_upload(superuser, b"abcdefghij")
        assert response2.data["data"]["md5sum"] == first_md5
        # 同内容两条记录复用同一物理文件
        pks = [response.data["data"]["pk"], response2.data["data"]["pk"]]
        assert UploadFile.objects.filter(pk__in=pks).values("filepath").distinct().count() == 1

    def test_md5_mismatch_rejected_and_session_kept_pending(self, superuser, _small_chunks):
        content = b"abcdefghij"
        plan = _init(superuser, filesize=len(content), total_chunks=3, chunk_size=4, md5="0" * 32)
        for index in range(3):
            _part(superuser, plan["session"], index, content[index * 4 : (index + 1) * 4])
        response = _post(superuser, "chunk_complete", {"pk": plan["session"], "md5": "1" * 32})
        assert response.data["code"] == CHECKSUM_MISMATCH_CODE
        assert UploadSession.objects.get(pk=plan["session"]).status == UploadSession.Status.PENDING
        assert not UploadFile.objects.filter(filename="big.bin").exists()

    def test_content_hash_mismatch_aborts_session(self, superuser, _small_chunks):
        """分片内容与 init 声明不符（串会话/损坏）：拒绝落库并作废会话（不可复用）。"""
        content = b"abcdefghij"
        plan = _init(
            superuser,
            filesize=len(content),
            total_chunks=3,
            chunk_size=4,
            content_hash=_content_hash(b"zzzzzzzzzz"),
        )
        for index in range(3):
            _part(superuser, plan["session"], index, content[index * 4 : (index + 1) * 4])

        response = _post(superuser, "chunk_complete", {"pk": plan["session"]})

        assert response.data["code"] == CHECKSUM_MISMATCH_CODE
        session = UploadSession.objects.get(pk=plan["session"])
        assert session.status == UploadSession.Status.ABORTED
        assert session.parts.count() == 0
        assert not UploadFile.objects.filter(filename="big.bin").exists()

    def test_incomplete_upload_rejected(self, superuser, _small_chunks):
        plan = _init(superuser, filesize=10, total_chunks=3, chunk_size=4)
        _part(superuser, plan["session"], 0, b"abcd")
        response = _post(superuser, "chunk_complete", {"pk": plan["session"]})
        assert response.data["code"] == 1002

    def test_other_user_cannot_complete(self, superuser, normal_user, _small_chunks):
        plan = _init(superuser, filesize=10, total_chunks=3, chunk_size=4)
        for index in range(3):
            _part(superuser, plan["session"], index, b"abcd")
        response = _post(normal_user, "chunk_complete", {"pk": plan["session"]})
        assert response.status_code == 403
        # 内核层属主校验（纵深）：superuser 的会话对 normal_user 不可完成
        from file.utils.upload_chunk import complete_session

        with pytest.raises(UploadError) as exc_info:
            complete_session(normal_user, int(plan["session"]))
        assert exc_info.value.code == 1002


class TestChunkAbort:
    def test_aborts_and_cleans_parts(self, superuser, _small_chunks):
        plan = _init(superuser, filesize=10, total_chunks=3, chunk_size=4)
        for index in range(2):
            _part(superuser, plan["session"], index, b"abcd")
        response = _post(superuser, "chunk_abort", {"pk": plan["session"]})
        assert response.status_code == 200
        session = UploadSession.objects.get(pk=plan["session"])
        assert session.status == UploadSession.Status.ABORTED
        assert session.parts.count() == 0
        from common.storage import storage_exists

        assert not storage_exists(part_storage_name(session.pk, 0))

    def test_nonexistent_session_is_noop(self, superuser, _small_chunks):
        response = _post(superuser, "chunk_abort", {"pk": 99999999})
        assert response.status_code == 200

    def test_malformed_session_pk_is_rejected(self, superuser, _small_chunks):
        response = _post(superuser, "chunk_abort", {"pk": "00000000-0000-0000"})
        assert response.data["code"] == 1001


class TestTwoPhaseMerge:
    """两段式合并守护（审查修复 BE-M3）：CAS 认领互斥、崩溃残留恢复与收口幂等。"""

    def _uploaded_session(self, user, content=b"abcdefghij"):
        """完成分片传输（不 complete），返回 PENDING 会话。"""
        plan = _init(user, filesize=len(content), total_chunks=3, chunk_size=4)
        for index in range(3):
            chunk = content[index * 4 : (index + 1) * 4]
            assert _part(user, plan["session"], index, chunk).status_code == 200
        return plan["session"]

    @staticmethod
    def _to_merging(session_pk, *, stale):
        """把会话推入 MERGING 认领态（stale=True 时认领时间已超 MERGE_STALE_SECONDS）。"""
        from datetime import timedelta

        from django.utils import timezone

        from file.utils import upload_chunk

        delta = timedelta(seconds=upload_chunk.MERGE_STALE_SECONDS * 2) if stale else timedelta(seconds=1)
        UploadSession.objects.filter(pk=session_pk).update(
            status=UploadSession.Status.MERGING, updated_time=timezone.now() - delta
        )

    def test_fresh_merging_rejects_concurrent_complete_and_abort(self, superuser, _small_chunks):
        """新鲜认领（他方合并进行中）：并发 complete 与 abort 都被拒（可读业务码）。"""
        from django.utils.translation import gettext as _

        from file.utils.upload_chunk import complete_session

        session_pk = self._uploaded_session(superuser)
        self._to_merging(session_pk, stale=False)

        with pytest.raises(UploadError) as complete_exc:
            complete_session(superuser, int(session_pk))
        assert str(_("Upload is being merged, please retry later")) in str(complete_exc.value.detail)

        response = _post(superuser, "chunk_abort", {"pk": session_pk})
        assert response.data["code"] == 1002  # 视图层把「合并中不可作废」转业务码而非 500
        # 认领未被并发请求破坏：会话仍为 MERGING、分片未被动
        session = UploadSession.objects.get(pk=session_pk)
        assert session.status == UploadSession.Status.MERGING
        assert session.parts.count() == 3

    def test_stale_merging_reclaimed_and_completed(self, superuser, _small_chunks):
        """崩溃残留恢复：认领超时的 MERGING 会被下一次 complete 重认领并正常收口。"""
        from file.utils.upload_chunk import complete_session

        session_pk = self._uploaded_session(superuser)
        self._to_merging(session_pk, stale=True)

        upload, session = complete_session(superuser, int(session_pk))

        assert session.status == UploadSession.Status.COMPLETED
        assert session.upload_id == upload.pk
        with upload.filepath.open("rb") as fh:
            assert fh.read() == b"abcdefghij"
        assert session.parts.count() == 0

    def test_stale_merging_abort_recovers(self, superuser, _small_chunks):
        """崩溃残留的用户侧出口：认领超时的 MERGING 可被 abort 作废回收。"""
        from file.utils.upload_chunk import abort_session

        session_pk = self._uploaded_session(superuser)
        self._to_merging(session_pk, stale=True)

        assert abort_session(superuser, int(session_pk)) is True

        session = UploadSession.objects.get(pk=session_pk)
        assert session.status == UploadSession.Status.ABORTED
        assert session.parts.count() == 0

    def test_stale_merging_reclaimed_by_cleanup(self, superuser, _small_chunks):
        """清理兜底：认领超时超保留期的 MERGING 残留由清理任务回收（分片 + 行）。"""
        from datetime import timedelta

        from django.utils import timezone

        from file.utils.upload_chunk import auto_clean_upload_sessions

        session_pk = self._uploaded_session(superuser)
        UploadSession.objects.filter(pk=session_pk).update(
            status=UploadSession.Status.MERGING, updated_time=timezone.now() - timedelta(days=2)
        )

        result = auto_clean_upload_sessions(clean_day=1)

        assert result["stale_aborted"] == 1
        assert not UploadSession.objects.filter(pk=session_pk).exists()
        from common.storage import storage_exists

        assert not storage_exists(part_storage_name(session_pk, 0))

    def test_fresh_merging_not_reclaimed_by_cleanup(self, superuser, _small_chunks):
        """清理不误伤：认领仍新鲜（可能在合并中）的 MERGING 不被清理回收。"""
        from datetime import timedelta

        from django.utils import timezone

        from file.utils.upload_chunk import auto_clean_upload_sessions

        session_pk = self._uploaded_session(superuser)
        # 会话创建时间早已过期，但认领（updated_time）是新鲜的：不回收
        UploadSession.objects.filter(pk=session_pk).update(
            status=UploadSession.Status.MERGING, created_time=timezone.now() - timedelta(days=2)
        )

        result = auto_clean_upload_sessions(clean_day=1)

        assert result["stale_aborted"] == 0
        assert UploadSession.objects.filter(pk=session_pk).exists()

    def test_finalize_race_returns_winner_product(self, superuser, _small_chunks, monkeypatch):
        """收口竞态幂等：他方先收口（同 md5 去重命中同一产物行）时，复用库内终态。"""
        from django.core.files.base import ContentFile
        from django.utils import timezone

        from file.utils import upload_chunk

        # 会话保持 PENDING 入口（认领由 complete 正常完成），竞态在合并返回前注入
        session_pk = self._uploaded_session(superuser)

        def fake_merge(user_obj, session, parts, declared_md5):
            rival = UploadFile.objects.create(
                filename=session.filename,
                filesize=session.filesize,
                is_upload=True,
                is_tmp=True,
                creator=user_obj,
                md5sum="0" * 32,
                filepath=ContentFile(b"rival", name="rival.bin"),
            )
            # 模拟并发重认领窗口：合并返回前他方已收口
            UploadSession.objects.filter(pk=session.pk, status=UploadSession.Status.MERGING).update(
                status=UploadSession.Status.COMPLETED, upload=rival, md5sum="0" * 32, updated_time=timezone.now()
            )
            return "0" * 32, rival

        monkeypatch.setattr(upload_chunk, "_merge_and_store", fake_merge)
        upload, session = upload_chunk.complete_session(superuser, int(session_pk))

        # 败方 CAS 不命中 → 复用他方终态与产物（不重复挂接、不报 500）
        assert session.status == UploadSession.Status.COMPLETED
        assert session.upload_id == upload.pk
        assert UploadSession.objects.filter(pk=session_pk).count() == 1

    def test_merge_failure_reverts_claim_to_pending(self, superuser, _small_chunks, monkeypatch):
        """合并期可重试失败（存储抖动类）：认领退回 PENDING，会话可再次 complete。"""
        from file.utils import upload_chunk

        session_pk = self._uploaded_session(superuser)

        def boom(*args, **kwargs):
            raise RuntimeError("storage hiccup")

        monkeypatch.setattr(upload_chunk, "_merge_and_store", boom)
        with pytest.raises(RuntimeError):
            upload_chunk.complete_session(superuser, int(session_pk))

        session = UploadSession.objects.get(pk=session_pk)
        assert session.status == UploadSession.Status.PENDING  # 认领已退回，可重试

        # 解除桩后重试 complete 成功（分片原样保留）
        monkeypatch.undo()
        upload, session = upload_chunk.complete_session(superuser, int(session_pk))
        assert session.status == UploadSession.Status.COMPLETED
        with upload.filepath.open("rb") as fh:
            assert fh.read() == b"abcdefghij"


class TestCleanup:
    def test_stale_pending_sessions_purged(self, superuser, _small_chunks):
        from datetime import timedelta

        from django.utils import timezone

        from file.utils.upload_chunk import auto_clean_upload_sessions

        plan = _init(superuser, filesize=10, total_chunks=3, chunk_size=4)
        _part(superuser, plan["session"], 0, b"abcd")
        session = UploadSession.objects.get(pk=plan["session"])
        # 分片必须已落行：上传失败（如限流 429）会在此暴露，而不是等清理断言时才表现为
        # 「文件仍在」——历史上顺序/并行依赖的假失败即由此而来
        assert session.parts.filter(index=0).exists()
        UploadSession.objects.filter(pk=session.pk).update(created_time=timezone.now() - timedelta(days=2))

        from common.storage import storage_exists

        assert storage_exists(part_storage_name(session.pk, 0))
        result = auto_clean_upload_sessions(clean_day=1)
        assert result["stale_aborted"] == 1
        assert not storage_exists(part_storage_name(session.pk, 0))
        assert not UploadSession.objects.filter(pk=session.pk).exists()

    def test_completed_session_row_reaped(self, superuser, _small_chunks):
        from datetime import timedelta

        from django.utils import timezone

        from file.utils.upload_chunk import auto_clean_upload_sessions

        _plan, response = self._full_upload_of(superuser)
        session = UploadSession.objects.get(pk=_plan["session"])
        UploadSession.objects.filter(pk=session.pk).update(created_time=timezone.now() - timedelta(days=2))
        auto_clean_upload_sessions(clean_day=1)
        assert not UploadSession.objects.filter(pk=session.pk).exists()
        # 落库的正式文件不受会话清理影响
        assert UploadFile.objects.filter(pk=response.data["data"]["pk"]).exists()

    def _full_upload_of(self, user):
        content = b"abcdefghij"
        plan = _init(user, filesize=len(content), total_chunks=3, chunk_size=4)
        for index in range(3):
            _part(user, plan["session"], index, content[index * 4 : (index + 1) * 4])
        response = _post(user, "chunk_complete", {"pk": plan["session"]})
        assert response.status_code == 200
        return plan, response
