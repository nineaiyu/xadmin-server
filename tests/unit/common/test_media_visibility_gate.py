# -*- coding: utf-8 -*-
"""模型图片可见性档位（MEDIA_MODEL_IMAGE_VISIBILITY）守护测试。

默认 public（站内公开契约，docs/ops/storage.md §5.1）零行为变化；
restricted 档按记录数据权限收敛（原图与缩略图一并，无行/不可见一律 404）。
以 identity.UserInfo.avatar（ProcessedImageField）为载体。
"""

import io

import pytest
from django.http import Http404
from django.test import RequestFactory
from PIL import Image

from common.utils.media import media_serve
from identity.models import UserInfo
from system.models import DataPermission

pytestmark = pytest.mark.django_db


def _png_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (255, 128, 0)).save(buf, format="PNG")
    return buf.getvalue()


def _avatar_for(user: UserInfo, media_root) -> str:
    """给 user.avatar 写入物理文件并返回原图相对路径（upload_directory_path 形态）。

    文件名不含下划线：缩略图形态 `<name>_<scale>.jpg` 按单个下划线切分（get_media_path
    口径），文件名带下划线会被判非缩略图形态。
    """
    name = f"identity/userinfo/0/{user.pk}/unitavatar{user.pk}.png"
    target = media_root / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(_png_bytes())
    user.avatar.name = name
    user.save(update_fields=["avatar"])
    return name


def _request_for(user: UserInfo, path: str):
    request = RequestFactory(HTTP_USER_AGENT="pytest-agent").get(f"/media/{path}")
    request.user = user
    return request


@pytest.fixture
def media_root(tmp_path, settings):
    settings.MEDIA_ROOT = str(tmp_path)
    return tmp_path


@pytest.fixture
def owner_avatar(superuser, media_root):
    """超管持有头像（受限档下超管恒可见）。"""
    return _avatar_for(superuser, media_root)


@pytest.fixture
def viewer_avatar(normal_user, media_root):
    """普通用户持有头像（默认无任何数据权限授权）。"""
    return _avatar_for(normal_user, media_root)


def _grant_own_records(user: UserInfo) -> None:
    """给用户授予「仅本人记录」的数据权限（identity.userinfo 按 admin 字段口径不一致——
    UserInfo 无 admin 归属字段，用 id 等于当前用户规则）。"""
    user.rules.add(
        DataPermission.objects.create(
            name=f"own-records-{user.pk}",
            rules=[
                {"table": "identity.userinfo", "field": "id", "type": "value.user.id", "value": "*", "match": "exact"}
            ],
        )
    )


class TestPublicDefault:
    """默认 public：站内公开契约零行为变化（受限档未开启时不做记录级过滤）。"""

    def test_viewer_without_grants_reads_others_original(self, normal_user, superuser, media_root, owner_avatar):
        request = _request_for(normal_user, owner_avatar)
        response = media_serve(request, owner_avatar, document_root=str(media_root))
        assert response.status_code == 200

    def test_viewer_without_grants_reads_others_thumbnail(self, normal_user, media_root, owner_avatar, settings):
        # 缩略图走 get_media_path 现场派生（imagekit 需要可解码原图）
        thumbnail_path = owner_avatar.replace(".png", "_1.jpg")
        request = _request_for(normal_user, thumbnail_path)
        response = media_serve(request, thumbnail_path, document_root=str(media_root))
        assert response.status_code == 200


class TestRestrictedGate:
    """restricted：按记录数据权限收敛，原图与缩略图一并。"""

    @pytest.fixture(autouse=True)
    def _restricted(self, settings):
        settings.MEDIA_MODEL_IMAGE_VISIBILITY = "restricted"

    def test_user_without_grants_gets_404_for_others_original(self, normal_user, media_root, owner_avatar):
        request = _request_for(normal_user, owner_avatar)
        with pytest.raises(Http404):
            media_serve(request, owner_avatar, document_root=str(media_root))

    def test_user_without_grants_gets_404_for_others_thumbnail(self, normal_user, media_root, owner_avatar):
        thumbnail_path = owner_avatar.replace(".png", "_1.jpg")
        request = _request_for(normal_user, thumbnail_path)
        with pytest.raises(Http404):
            media_serve(request, thumbnail_path, document_root=str(media_root))

    def test_superuser_still_reads(self, superuser, media_root, owner_avatar):
        request = _request_for(superuser, owner_avatar)
        response = media_serve(request, owner_avatar, document_root=str(media_root))
        assert response.status_code == 200

    def test_granted_user_reads_own_record(self, normal_user, media_root, viewer_avatar):
        _grant_own_records(normal_user)
        request = _request_for(normal_user, viewer_avatar)
        response = media_serve(request, viewer_avatar, document_root=str(media_root))
        assert response.status_code == 200

    def test_granted_user_cannot_read_others_record(self, normal_user, superuser, media_root, owner_avatar):
        _grant_own_records(normal_user)
        request = _request_for(normal_user, owner_avatar)
        with pytest.raises(Http404):
            media_serve(request, owner_avatar, document_root=str(media_root))

    def test_nonexistent_record_404(self, normal_user, media_root, owner_avatar):
        # UserInfo 主键是整数：换成不存在的整数 pk（保持 5 段路径形态）
        missing = owner_avatar.replace(f"/{owner_avatar.split('/')[-2]}/", "/99999999/")
        request = _request_for(normal_user, missing)
        with pytest.raises(Http404):
            media_serve(request, missing, document_root=str(media_root))

    def test_non_model_image_domain_unaffected(self, normal_user, media_root):
        """无 ProcessedImageField 的模型路径不在域内（受限档不干预，文件存在即直出）。"""
        name = "identity/department/0/1/legacy.png"
        target = media_root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(_png_bytes())
        request = _request_for(normal_user, name)
        response = media_serve(request, name, document_root=str(media_root))
        assert response.status_code == 200

    def test_pk0_unresolved_pre_save_preview_404(self, normal_user, media_root):
        """先传后存（pk=0）字段值无对应行：受限档 fail-closed（保存落库后恢复可见）。"""
        name = "identity/userinfo/0/0/unsigned_upload.png"
        target = media_root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(_png_bytes())
        request = _request_for(normal_user, name)
        with pytest.raises(Http404):
            media_serve(request, name, document_root=str(media_root))
