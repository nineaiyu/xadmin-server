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
