# -*- coding: utf-8 -*-
"""文件存储搬迁守护测试。

用两个本地 FileSystemStorage 模拟「源 / 目标」两端（不依赖真实 S3），覆盖：
幂等搬迁（断点续搬）/ 冲突不覆盖 / --overwrite / dry-run / verify / 去重与 limit。
"""

import hashlib

import pytest
from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage

from system.models import UploadFile
from system.utils.storage_migrate import (
    migrate_uploads,
    object_md5,
    summary_line,
)

pytestmark = pytest.mark.django_db


def _make_storage(tmp_path, name: str) -> FileSystemStorage:
    location = tmp_path / name
    location.mkdir(parents=True, exist_ok=True)
    return FileSystemStorage(location=str(location), base_url=f"/{name}/")


def _create_upload(storage, name: str, content: bytes, creator=None) -> UploadFile:
    saved = storage.save(name, ContentFile(content))
    return UploadFile.objects.create(
        filepath=saved,
        filename=saved.rsplit("/", 1)[-1],
        filesize=len(content),
        md5sum=hashlib.md5(content).hexdigest(),  # noqa: S324 文件指纹（非安全用途）
        creator=creator,
        modifier=creator,
    )


class TestMigrateUploads:
    def test_push_copies_then_skips(self, tmp_path, superuser):
        source = _make_storage(tmp_path, "src")
        target = _make_storage(tmp_path, "dst")
        _create_upload(source, "a/one.txt", b"one", superuser)
        _create_upload(source, "a/two.txt", b"two", superuser)

        stats = migrate_uploads(source, target)
        assert stats["scanned"] == 2
        assert stats["copied"] == 2
        assert stats["failed"] == 0
        assert target.exists("a/one.txt") and target.exists("a/two.txt")
        with target.open("a/one.txt", "rb") as file:
            assert file.read() == b"one"

        # 幂等：第二次全部跳过（断点续搬语义）
        stats = migrate_uploads(source, target)
        assert stats["copied"] == 0
        assert stats["skipped"] == 2

    def test_conflict_not_overwritten_by_default(self, tmp_path, superuser):
        source = _make_storage(tmp_path, "src")
        target = _make_storage(tmp_path, "dst")
        _create_upload(source, "c/doc.txt", b"new-content", superuser)
        target.save("c/doc.txt", ContentFile(b"other-length-content"))

        stats = migrate_uploads(source, target)
        assert stats["conflict"] == 1
        assert stats["copied"] == 0
        with target.open("c/doc.txt", "rb") as file:
            assert file.read() == b"other-length-content"

        # --overwrite 显式覆盖
        stats = migrate_uploads(source, target, overwrite=True)
        assert stats["copied"] == 1
        with target.open("c/doc.txt", "rb") as file:
            assert file.read() == b"new-content"

    def test_dry_run_does_not_write(self, tmp_path, superuser):
        source = _make_storage(tmp_path, "src")
        target = _make_storage(tmp_path, "dst")
        _create_upload(source, "d/doc.txt", b"content", superuser)

        stats = migrate_uploads(source, target, dry_run=True)
        assert stats["copied"] == 1
        assert target.exists("d/doc.txt") is False

    def test_limit_and_dedup(self, tmp_path, superuser):
        source = _make_storage(tmp_path, "src")
        target = _make_storage(tmp_path, "dst")
        upload = _create_upload(source, "e/one.txt", b"one", superuser)
        # 同一物理文件被两条记录引用：只搬一次
        UploadFile.objects.create(
            filepath=upload.filepath.name,
            filename="one.txt",
            filesize=3,
            md5sum=hashlib.md5(b"one").hexdigest(),  # noqa: S324
            creator=superuser,
        )
        _create_upload(source, "e/two.txt", b"two", superuser)

        assert migrate_uploads(source, target, limit=1)["scanned"] == 1


class TestVerify:
    def test_verify_reports_missing_and_mismatch(self, tmp_path, superuser):
        source = _make_storage(tmp_path, "src")
        target = _make_storage(tmp_path, "dst")
        _create_upload(source, "v/ok.txt", b"same", superuser)
        _create_upload(source, "v/missing.txt", b"abc", superuser)
        _create_upload(source, "v/short.txt", b"0123456789", superuser)
        target.save("v/ok.txt", ContentFile(b"same"))
        target.save("v/short.txt", ContentFile(b"01"))

        stats = migrate_uploads(source, target, verify=True)
        assert stats["verify_ok"] == 1
        assert stats["verify_failed"] == 2
        results = {detail["name"]: detail["result"] for detail in stats["details"]}
        assert results["v/missing.txt"] == "missing"
        assert results["v/short.txt"] == "size_mismatch"

        # md5 比对：源内容不一致的目标文件（大小相同）也能被抓出
        target.save("v/md5.txt", ContentFile(b"1234"))
        _create_upload(source, "v/md5.txt", b"5678", superuser)
        stats = migrate_uploads(source, target, verify=True, check_md5=True)
        results = {detail["name"]: detail["result"] for detail in stats["details"]}
        assert results["v/md5.txt"] == "md5_mismatch"

    def test_object_md5(self, tmp_path):
        storage = _make_storage(tmp_path, "src")
        storage.save("m/a.txt", ContentFile(b"abcdef"))
        assert object_md5(storage, "m/a.txt") == hashlib.md5(b"abcdef").hexdigest()  # noqa: S324
        assert object_md5(storage, "m/none.txt") is None


def test_summary_line_contains_counts():
    stats = {
        "scanned": 3,
        "copied": 2,
        "skipped": 1,
        "conflict": 0,
        "missing_source": 0,
        "failed": 0,
        "verify_ok": 0,
        "verify_failed": 0,
        "details": [],
    }
    line = summary_line(stats, "push", dry_run=True)
    assert "direction=push" in line and "copied=2" in line and "dry-run" in line
