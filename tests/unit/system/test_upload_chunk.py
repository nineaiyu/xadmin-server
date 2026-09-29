# -*- coding:utf-8 -*-
"""分片上传 / 断点续传协议守护测试（协议内核见 system/utils/upload_chunk.py）。

覆盖：init 计划校验（大小上限 / 扩展名 / 分片数）、断点续传命中、分片幂等、
complete 合并落库与 md5 一致性、abort 清理、过期会话清理。
"""

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from rest_framework.test import APIRequestFactory, force_authenticate

from system.models import UploadFile, UploadSession
from system.utils.upload_chunk import CHECKSUM_MISMATCH_CODE, part_storage_name
from system.utils.upload_store import UploadError
from system.views.admin.file import UploadFileViewSet

pytestmark = pytest.mark.django_db

FILE_URL = "/api/system/file"

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
    from system.utils import upload_chunk

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


def _init(user, filename="big.bin", filesize=10, total_chunks=3, chunk_size=4, **kwargs):
    """发起 chunk/init；成功返回 data 载荷，失败返回完整响应体（含 code/detail）。"""
    response = _post(
        user,
        "chunk_init",
        {"filename": filename, "filesize": filesize, "total_chunks": total_chunks, "chunk_size": chunk_size, **kwargs},
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

    def test_other_user_cannot_feed_session(self, superuser, normal_user, _small_chunks):
        # API 层先被权限门拦下（normal_user 未授权文件中心）
        plan = _init(superuser, filesize=10, total_chunks=3, chunk_size=4)
        response = _part(normal_user, plan["session"], 0, b"abcd")
        assert response.status_code == 403
        # 权限门之内还有属主校验（纵深）：直接调内核断言
        from system.utils.upload_chunk import store_part

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
        from system.utils.upload_chunk import complete_session

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


class TestCleanup:
    def test_stale_pending_sessions_purged(self, superuser, _small_chunks):
        from datetime import timedelta

        from django.utils import timezone

        from system.utils.upload_chunk import auto_clean_upload_sessions

        plan = _init(superuser, filesize=10, total_chunks=3, chunk_size=4)
        _part(superuser, plan["session"], 0, b"abcd")
        session = UploadSession.objects.get(pk=plan["session"])
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

        from system.utils.upload_chunk import auto_clean_upload_sessions

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
