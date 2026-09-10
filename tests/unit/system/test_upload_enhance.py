# -*- coding: utf-8 -*-
"""文件中心增强（F2 裁剪版）：个人配额（存储/数量）+ 分类（字典驱动）+ stats。

去重/保留期清理在评审复盘后降级候选池（悬挂引用缺陷），不在本文件范围。
"""

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from rest_framework import status
from rest_framework.test import APIRequestFactory, force_authenticate

from system.models import DataDict, UploadFile
from system.utils.dict import invalid_dict_cache
from system.views.admin.file import UploadFileViewSet

pytestmark = pytest.mark.django_db

FILE_URL = "/api/system/file"


@pytest.fixture(autouse=True)
def _file_center_config(db):
    """准备上传链路依赖的系统配置，并在用例后复位配额。

    上传视图的 get_upload_max_size 依赖系统配置 FILE_UPLOAD_SIZE（真实环境由
    init_data 种子写入）。测试库无该行时 SysConfig/UserConfig 读取会回退为 {}
    字典，min(dict, dict) 直接 TypeError——这里显式种一行 inherit=True 的系统
    默认值，走「用户继承系统默认」的真实口径。
    """
    from common.core.config import SysConfig
    from system.models import SystemConfig

    SystemConfig.objects.create(key="FILE_UPLOAD_SIZE", value=10 * 1024 * 1024, inherit=True, is_active=True)
    yield
    SysConfig.set_value("FILE_STORAGE_QUOTA_MB", 0)
    SysConfig.set_value("FILE_UPLOAD_COUNT_LIMIT", 0)


def _mkfile(name, content=b"hello", mime="text/plain"):
    return SimpleUploadedFile(name, content, content_type=mime)


def _upload(user, *files):
    factory = APIRequestFactory()
    request = factory.post(f"{FILE_URL}/upload", {"file": list(files)}, format="multipart")
    force_authenticate(request, user=user)
    # 配额拒绝走 ApiResponse 直接返回（不抛异常），无需 atomic 包裹；统一包一层更稳
    with transaction.atomic():
        return UploadFileViewSet.as_view({"post": "upload"})(request)


def _stats(user):
    factory = APIRequestFactory()
    request = factory.get(f"{FILE_URL}/stats")
    force_authenticate(request, user=user)
    return UploadFileViewSet.as_view({"get": "stats"})(request)


def _patch_category_dict(code="image", label="图片"):
    """建 upload_category 字典类型 + 单项（分类写入路径约束来源）。"""
    parent = DataDict.objects.create(code="upload_category", label="文件分类", is_locked=True)
    DataDict.objects.create(parent=parent, code=code, label=label, value=code, sort=0, is_active=True)
    invalid_dict_cache()
    return parent


def _uploaded_file(user, name="a.txt", content=b"hello"):
    """经真实上传接口落库一条 UploadFile（含磁盘文件），返回该记录。"""
    response = _upload(user, _mkfile(name, content))
    assert response.data["code"] == 1000, response.data
    payload = response.data["data"]
    row = payload[0] if isinstance(payload, list) else payload
    return UploadFile.objects.get(pk=row["pk"])


class TestPhysicalFileGuard:
    """磁盘删除守护（先守护后功能：去重/保留期清理的前置条件）。"""

    def test_shared_file_is_kept_on_disk(self, superuser):
        """同路径/同 md5 的另一条活动记录仍在：删本条不删磁盘文件。"""
        import os

        first = _uploaded_file(superuser, content=b"same-bytes")
        shared_path = first.filepath.name
        # 模拟去重复用：第二条记录引用同一物理文件
        UploadFile.objects.create(
            creator=superuser,
            filename="copy.txt",
            filesize=first.filesize,
            mime_type="text/plain",
            md5sum=first.md5sum,
            filepath=shared_path,
            is_upload=True,
            is_tmp=False,
        )
        disk_path = first.filepath.path
        assert os.path.exists(disk_path)

        first.hard_delete()

        assert not UploadFile.objects.filter(pk=first.pk).exists()
        assert os.path.exists(disk_path), "磁盘文件被其他记录共用时不应删除"

    def test_business_referenced_file_is_kept_on_disk(self, superuser):
        """业务反向外键仍指向该附件：只删记录、保留磁盘文件。"""
        import os

        from system.models import ExportRecord

        upload = _uploaded_file(superuser, content=b"export-bytes")
        ExportRecord.objects.create(name="e2e-export.xlsx", file=upload)
        disk_path = upload.filepath.path

        upload.hard_delete()

        assert os.path.exists(disk_path), "仍被业务引用的文件不应删除"

    def test_unreferenced_file_is_removed_from_disk(self, superuser):
        """无任何引用时仍按原语义清理磁盘文件（守护不能把清理能力整体关掉）。"""
        import os

        upload = _uploaded_file(superuser, content=b"lonely-bytes")
        disk_path = upload.filepath.path
        assert os.path.exists(disk_path)

        upload.hard_delete()

        assert not os.path.exists(disk_path)


class TestUploadDedup:
    """上传去重：同属主同 md5 复用物理文件（只建引用记录），跨用户不复用。"""

    def test_same_file_reuses_physical_copy(self, superuser):
        """同用户重复上传同一文件：新建记录但复用同一 filepath（不再落盘）。"""
        import os

        first = _uploaded_file(superuser, "dup.txt", content=b"dedup-bytes")
        assert os.path.exists(first.filepath.path)

        response = _upload(superuser, _mkfile("dup-copy.txt", b"dedup-bytes"))
        assert response.data["code"] == 1000, response.data
        second = UploadFile.objects.get(pk=response.data["data"][0]["pk"])

        assert second.md5sum == first.md5sum
        assert second.filepath.name == first.filepath.name
        # 两条记录共享同一物理文件（磁盘只有一份）
        assert UploadFile.objects.filter(md5sum=first.md5sum).count() == 2
        assert os.path.exists(first.filepath.path)

    def test_same_file_from_other_user_not_reused(self, superuser):
        """跨用户不复用：避免越权复用他人文件的存储路径。

        第二个属主用另一位超管：普通用户走 RBAC 菜单链路，测试里没有上传菜单权限。
        """
        from system.models import UserInfo

        other = UserInfo.objects.create_superuser(username="other_super", password="Test@123456")
        first = _uploaded_file(superuser, "cross.txt", content=b"cross-bytes")

        response = _upload(other, _mkfile("cross.txt", b"cross-bytes"))
        assert response.data["code"] == 1000, response.data
        second = UploadFile.objects.get(pk=response.data["data"][0]["pk"])

        assert second.md5sum == first.md5sum
        assert second.filepath.name != first.filepath.name

    def test_soft_deleted_source_not_reused(self, superuser):
        """回收站中的文件不参与复用（软删除记录不算活动副本）。"""
        first = _uploaded_file(superuser, "trash.txt", content=b"trash-bytes")
        first.delete()  # 软删除

        response = _upload(superuser, _mkfile("trash.txt", b"trash-bytes"))
        assert response.data["code"] == 1000, response.data
        second = UploadFile.objects.get(pk=response.data["data"][0]["pk"])

        assert second.filepath.name != first.filepath.name


class TestKeepDaysCleanup:
    """保留期清理（FILE_KEEP_DAYS）：只清非临时、无业务引用的历史文件，并走磁盘守护。"""

    @staticmethod
    def _formal_file(user, name, content, age_days=0):
        """构造一条「已挂到业务上」的正式文件（is_tmp=False），可选置为历史时间。"""
        import datetime

        from django.utils import timezone

        record = _uploaded_file(user, name=name, content=content)
        UploadFile.all_objects.filter(pk=record.pk).update(is_tmp=False)
        if age_days:
            UploadFile.all_objects.filter(pk=record.pk).update(
                created_time=timezone.now() - datetime.timedelta(days=age_days)
            )
        record.refresh_from_db()
        return record

    def test_zero_means_no_cleanup(self, superuser):
        """0 = 不清理（默认值，避免误删历史文件）。"""
        from system.utils.ctasks import auto_clean_upload_file

        record = self._formal_file(superuser, "keep-zero.txt", b"keep-zero", age_days=30)
        assert auto_clean_upload_file(keep_days=0) == 0
        assert UploadFile.objects.filter(pk=record.pk).exists()

    def test_removes_expired_unreferenced(self, superuser):
        """超保留期且无引用：记录与磁盘文件一并清理。"""
        import os

        from system.utils.ctasks import auto_clean_upload_file

        record = self._formal_file(superuser, "expired.txt", b"expired-bytes", age_days=30)
        disk_path = record.filepath.path
        assert os.path.exists(disk_path)

        assert auto_clean_upload_file(keep_days=7) == 1
        assert not UploadFile.objects.filter(pk=record.pk).exists()
        assert not os.path.exists(disk_path)

    def test_keeps_business_referenced(self, superuser):
        """有业务引用：记录与磁盘文件都保留（否则外键 SET_NULL 断链）。"""
        import os

        from system.models import ExportRecord
        from system.utils.ctasks import auto_clean_upload_file

        record = self._formal_file(superuser, "referenced.txt", b"referenced-bytes", age_days=30)
        ExportRecord.objects.create(name="keep.xlsx", file=record)
        disk_path = record.filepath.path

        assert auto_clean_upload_file(keep_days=7) == 0
        assert UploadFile.objects.filter(pk=record.pk).exists()
        assert os.path.exists(disk_path)

    def test_ignores_tmp_and_recent(self, superuser):
        """临时文件与未到期文件不归本任务处理。"""
        import datetime

        from django.utils import timezone

        from system.utils.ctasks import auto_clean_upload_file

        tmp = _uploaded_file(superuser, name="tmp.txt", content=b"tmp-bytes")  # is_tmp=True
        UploadFile.all_objects.filter(pk=tmp.pk).update(created_time=timezone.now() - datetime.timedelta(days=30))
        recent = self._formal_file(superuser, "recent.txt", b"recent-bytes")

        assert auto_clean_upload_file(keep_days=7) == 0
        assert UploadFile.objects.filter(pk=tmp.pk).exists()
        assert UploadFile.objects.filter(pk=recent.pk).exists()


def test_upload_without_quota_unlimited(superuser):
    """默认 0 = 不限：正常上传落库。"""
    response = _upload(superuser, _mkfile("a.txt"))
    assert response.data["code"] == 1000
    assert UploadFile.objects.filter(creator=superuser, is_upload=True).count() == 1


def test_storage_quota_exceeded_rejects_without_saving(superuser):
    """存储配额超限：1004 且不落盘（含同请求多文件累计口径）。"""
    from common.core.config import SysConfig

    # 预置接近配额的既有记录（filesize 为声明值，无需真实文件）
    UploadFile.objects.create(
        creator=superuser,
        filename="big.bin",
        filesize=1024 * 1024 - 50,
        mime_type="application/octet-stream",
        md5sum="b" * 32,
    )
    SysConfig.set_value("FILE_STORAGE_QUOTA_MB", 1)

    response = _upload(superuser, _mkfile("a.txt", b"x" * 100))
    assert response.data["code"] == 1004
    assert UploadFile.objects.filter(creator=superuser, filename="a.txt").count() == 0


def test_count_limit_exceeded_rejects(superuser):
    """数量上限超限：1004 且不落盘。"""
    from common.core.config import SysConfig

    UploadFile.objects.create(
        creator=superuser, filename="one.txt", filesize=1, mime_type="text/plain", md5sum="c" * 32
    )
    SysConfig.set_value("FILE_UPLOAD_COUNT_LIMIT", 1)

    response = _upload(superuser, _mkfile("a.txt"))
    assert response.data["code"] == 1004
    assert UploadFile.objects.filter(creator=superuser, filename="a.txt").count() == 0


def test_count_limit_allows_until_reached(superuser):
    """数量上限内可连续上传，同请求多文件累计计数。"""
    from common.core.config import SysConfig

    SysConfig.set_value("FILE_UPLOAD_COUNT_LIMIT", 2)
    response = _upload(superuser, _mkfile("a.txt"), _mkfile("b.txt"))
    assert response.data["code"] == 1000
    assert UploadFile.objects.filter(creator=superuser, is_upload=True).count() == 2

    with transaction.atomic():
        third = _upload(superuser, _mkfile("c.txt"))
    assert third.data["code"] == 1004


def test_quota_zero_means_unlimited(superuser):
    """显式配置 0 = 不限（与未配置同语义）。"""
    from common.core.config import SysConfig

    SysConfig.set_value("FILE_STORAGE_QUOTA_MB", 0)
    SysConfig.set_value("FILE_UPLOAD_COUNT_LIMIT", 0)
    response = _upload(superuser, _mkfile("a.txt"), _mkfile("b.txt"), _mkfile("c.txt"))
    assert response.data["code"] == 1000
    assert UploadFile.objects.filter(creator=superuser, is_upload=True).count() == 3


def test_category_write_constrained_by_dict(superuser):
    """category 写入路径（字典驱动守护）：字典项可写、字典外 invalid_choice、空值可写。"""
    _patch_category_dict()
    upload = UploadFile.objects.create(
        creator=superuser, filename="a.txt", filesize=1, mime_type="text/plain", md5sum="d" * 32, is_upload=True
    )

    factory = APIRequestFactory()
    request = factory.patch(f"{FILE_URL}/{upload.pk}", {"category": "image"}, format="json")
    force_authenticate(request, user=superuser)
    response = UploadFileViewSet.as_view({"patch": "partial_update"})(request, pk=upload.pk)
    assert response.data["code"] == 1000
    assert response.data["data"]["category"]["value"] == "image"

    # 字典外取值：invalid_choice（DictChoiceField 写入约束）
    request = factory.patch(f"{FILE_URL}/{upload.pk}", {"category": "bogus"}, format="json")
    force_authenticate(request, user=superuser)
    with transaction.atomic():
        response = UploadFileViewSet.as_view({"patch": "partial_update"})(request, pk=upload.pk)
    assert response.status_code == status.HTTP_400_BAD_REQUEST

    # 清空（null）合法
    request = factory.patch(f"{FILE_URL}/{upload.pk}", {"category": None}, format="json")
    force_authenticate(request, user=superuser)
    response = UploadFileViewSet.as_view({"patch": "partial_update"})(request, pk=upload.pk)
    assert response.data["code"] == 1000
    assert response.data["data"]["category"] is None


def test_stats_counts_size_and_usage_rate(superuser, normal_user):
    """stats：count/total_size 按本人聚合；配额 0 = usage_rate 0；他人数据不计入。"""
    from django.core.cache import cache

    from common.core.config import SysConfig

    UploadFile.objects.create(creator=superuser, filename="a", filesize=100, mime_type="t", md5sum="e" * 32)
    UploadFile.objects.create(creator=superuser, filename="b", filesize=200, mime_type="t", md5sum="f" * 32)
    UploadFile.objects.create(creator=normal_user, filename="c", filesize=999999, mime_type="t", md5sum="0" * 32)

    SysConfig.set_value("FILE_STORAGE_QUOTA_MB", 1)
    cache.clear()  # stats 10s 短缓存 + 配置缓存复位

    response = _stats(superuser)
    assert response.data["code"] == 1000
    data = response.data["data"]
    assert data["count"] == 2
    assert data["total_size"] == 300
    assert data["quota_mb"] == 1
    assert data["usage_rate"] == round(300 / (1024 * 1024) * 100, 2)

    # 配额 0 = 不限：usage_rate 恒 0
    SysConfig.set_value("FILE_STORAGE_QUOTA_MB", 0)
    cache.clear()
    response = _stats(superuser)
    assert response.data["data"]["usage_rate"] == 0
    assert response.data["data"]["quota_mb"] == 0


def test_category_filter(superuser):
    """列表按 category 精确过滤（iexact）。"""
    UploadFile.objects.create(
        creator=superuser, filename="a", filesize=1, mime_type="t", md5sum="1" * 32, category="image"
    )
    UploadFile.objects.create(
        creator=superuser, filename="b", filesize=1, mime_type="t", md5sum="2" * 32, category="video"
    )

    factory = APIRequestFactory()
    request = factory.get(FILE_URL, {"category": "IMAGE"})
    force_authenticate(request, user=superuser)
    response = UploadFileViewSet.as_view({"get": "list"})(request)
    results = response.data["data"]["results"]
    assert [item["filename"] for item in results] == ["a"]
