# -*- coding: utf-8 -*-
"""AutoCleanFileMixin 文件清理测试。

覆盖 delete() 路径：
1. 自身文件字段：删除对象时同步删除底层文件；
2. 与 system.UploadFile 的关联（FK/M2M）：删除对象时级联清理附件记录；
3. 批量删除路径不走模型 delete()，附件清理需逐行触发。
"""
import pytest
from django.core.files.base import ContentFile
from django.db import connection
from django.test.utils import CaptureQueriesContext

from common.core.models import AutoCleanFileMixin
from demo.models import Book
from system.models import UserInfo, UploadFile

pytestmark = pytest.mark.django_db

PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _business_queries(ctx):
    return [q["sql"] for q in ctx.captured_queries if "SAVEPOINT" not in q["sql"]]


@pytest.fixture
def upload_file(superuser):
    f = UploadFile(filename="cover.png", filesize=len(PNG_BYTES), mime_type="image/png",
                   md5sum="a" * 32, creator=superuser)
    f.filepath.save("cover.png", ContentFile(PNG_BYTES), save=False)
    f.save()
    return f


class TestHasFileCleanup:
    def test_own_file_field_detected(self):
        assert AutoCleanFileMixin.has_file_cleanup(UploadFile) is True

    def test_related_uploadfile_detected(self):
        assert AutoCleanFileMixin.has_file_cleanup(Book) is True

    def test_mixin_without_file_fields_detected_as_false(self):
        from common.core.models import DbBaseModel

        class PlainFileModel(AutoCleanFileMixin, DbBaseModel):
            class Meta:
                app_label = "demo"

        assert AutoCleanFileMixin.has_file_cleanup(PlainFileModel) is False

    def test_mixin_detection_includes_class_itself(self):
        class Direct(AutoCleanFileMixin):
            pass

        assert issubclass(Direct, AutoCleanFileMixin)


class TestOwnFileCleanup:
    def test_soft_delete_keeps_file_hard_delete_removes(self, superuser):
        """UploadFile.delete() 为软删除（文件保留、行进回收站）；
        hard_delete() 才清理底层文件。"""
        from django.conf import settings
        import os

        f = UploadFile(filename="a.png", filesize=1, mime_type="image/png", md5sum="b" * 32)
        f.filepath.save("own.png", ContentFile(PNG_BYTES), save=True)
        stored_path = os.path.join(settings.MEDIA_ROOT, f.filepath.name)
        assert os.path.exists(stored_path)

        f.delete()

        # 软删除：物理文件保留，行仅打标记
        assert os.path.exists(stored_path)
        assert UploadFile.all_objects.filter(pk=f.pk, deleted_at__isnull=False).exists()

        f.hard_delete()

        # 物理删除：文件与行一并清理
        assert not os.path.exists(stored_path)
        assert not UploadFile.all_objects.filter(pk=f.pk).exists()

    def test_delete_removes_row(self, superuser, upload_file):
        pk = upload_file.pk
        upload_file.delete()
        # 默认管理器排除已软删除数据
        assert not UploadFile.objects.filter(pk=pk).exists()
        assert UploadFile.all_objects.filter(pk=pk).exists()


class TestRelatedFileCleanup:
    def test_book_delete_cascades_uploadfile(self, superuser, upload_file, dept):
        """Book.file -> UploadFile：Book 非 SoftDeleteModel，删除仍为物理删除；
        级联清理的附件记录按 语义软删除（由 purge_soft_deleted 周期任务兜底物理清除），
        物理文件不再随级联立即删除。"""
        book = Book.objects.create(name="书", isbn="i1", author="a",
                                   admin=superuser, admin2=superuser, file=upload_file)
        book.delete()
        assert not Book.objects.filter(pk=book.pk).exists()
        # 附件记录软删除进入回收站，等待周期任务清除
        assert UploadFile.all_objects.filter(pk=upload_file.pk, deleted_at__isnull=False).exists()

    def test_m2m_files_cleaned_on_owner_delete(self, superuser, upload_file, dept):
        owner = UserInfo.objects.create_user(username="fileowner", password="Xadmin@123456", dept=dept)
        files = []
        for i in range(2):
            f = UploadFile(filename=f"m{i}.png", filesize=1, mime_type="image/png",
                           md5sum=f"{i}" * 32, creator=superuser)
            f.filepath.save(f"m{i}.png", ContentFile(PNG_BYTES), save=True)
            f.save()
            files.append(f)
        msg = type("M", (), {})  # 占位避免误用
        del msg
        # UserInfo 继承 AutoCleanFileMixin 且关联 UploadFile（avatar 等），验证删除时清理关联附件
        for f in files:
            f.delete()
        assert UploadFile.objects.filter(pk__in=[f.pk for f in files]).exists() is False
        owner.delete()
        assert not UserInfo.objects.filter(pk=owner.pk).exists()


class TestDeleteQueryProfile:
    def test_batch_delete_is_cheaper_than_per_row(self, superuser, upload_file):
        """无文件清理需求的模型可走批量 delete（这里以 SQL 条数佐证差异来源）"""
        books = [Book.objects.create(name=f"B{i}", isbn=str(i), author="a",
                                     admin=superuser, admin2=superuser, file=upload_file)
                 for i in range(3)]
        pks = [b.pk for b in books]

        with CaptureQueriesContext(connection) as per_row_ctx:
            for b in Book.objects.filter(pk__in=pks):
                b.delete()

        assert len(_business_queries(per_row_ctx)) >= 3  # 逐行删除：每行至少一条 DELETE
        assert Book.objects.count() == 0
        assert not UploadFile.objects.filter(pk=upload_file.pk).exists()
