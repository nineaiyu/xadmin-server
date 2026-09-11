# -*- coding: utf-8 -*-
"""F2 文件在线预览与缩略图：类型分派 / 鉴权 / 按需生成幂等 / 缓存回收。

落盘纪律的回归保护（四期 F2 教训）：预览缓存是派生产物，必须同时验证
「源文件删除 → 缓存联动清理」「孤儿清理」「保留期清理」三条回收路径，
否则缓存目录会成为新的磁盘泄漏源。
"""

import io
import os
import time

import pytest
from django.core.files.base import ContentFile
from rest_framework.test import APIRequestFactory, force_authenticate

from common.core.config import SysConfig
from system.models import UploadFile
from system.utils.ctasks import auto_clean_preview_cache
from system.utils import preview as preview_module
from system.utils.preview import (
    SIZE_PREVIEW,
    SIZE_THUMB,
    clean_preview_cache,
    preview_cache_path,
    preview_kind,
)
from system.views.admin.file import PREVIEW_UNSUPPORTED_CODE, UploadFileViewSet

pytestmark = pytest.mark.django_db

FILE_URL = "/api/system/file"

PNG_BYTES_TEMPLATE = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x08\x00\x00\x00\x08\x08\x02"
    b"\x00\x00\x00\x4b\x6d\x29\xdc\x00\x00\x00\x16IDAT\x08\xd7c\xfc\xcf\xc0\xf0\x9f"
    b"\x81\x81\x81\x11\x8c\x8a\x8a\x82\x81\x01\x00\x9d\x9d\x02\xfe\x8f\xbb\x31\x0f"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)


@pytest.fixture
def media_root(tmp_path, settings):
    settings.MEDIA_ROOT = str(tmp_path)
    return tmp_path


def make_upload(user, name, content, mime):
    """直接落盘建记录（不走上传接口，避免依赖上传配置种子）。"""
    upload = UploadFile(
        creator=user,
        filename=name,
        filesize=len(content),
        mime_type=mime,
        md5sum=f"{abs(hash((name, mime))) % 10**32:032d}",
        is_upload=True,
    )
    upload.filepath.save(name, ContentFile(content), save=True)
    return upload


def png_bytes():
    """8x8 合法 PNG（Pillow 可解码，用于验证真实缩略图生成）。"""
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), (200, 30, 30)).save(buffer, format="PNG")
    return buffer.getvalue()


def preview(upload, user, query=""):
    factory = APIRequestFactory()
    request = factory.get(f"{FILE_URL}/{upload.pk}/preview{query}")
    force_authenticate(request, user=user)
    return UploadFileViewSet.as_view({"get": "preview"})(request, pk=str(upload.pk))


class TestPreviewKind:
    @pytest.mark.parametrize(
        "mime,name,expected",
        [
            ("image/png", "a.png", "image"),
            ("image/jpeg", "a.jpg", "image"),
            ("application/pdf", "a.pdf", "pdf"),
            ("text/plain", "a.txt", "text"),
            ("application/json", "a.json", "text"),
            ("", "a.log", "text"),
            ("application/zip", "a.zip", None),
            ("application/octet-stream", "a.bin", None),
        ],
    )
    def test_dispatch(self, mime, name, expected):
        assert preview_kind(type("U", (), {"mime_type": mime, "filename": name})()) == expected


class TestPreviewAction:
    def test_image_thumb_generated_and_cached(self, superuser, media_root):
        upload = make_upload(superuser, "pic.png", png_bytes(), "image/png")
        response = preview(upload, superuser, query=f"?size={SIZE_THUMB}")

        assert response.status_code == 200
        assert response["Content-Disposition"].startswith("inline")
        assert response["Content-Type"] == "image/jpeg"

        cache_path = preview_cache_path(upload, SIZE_THUMB)
        assert os.path.exists(cache_path)

        # 幂等：再次请求直接命中缓存，不重新生成（用生成次数而非 mtime 判定：
        # 命中时会 touch 刷新 mtime，供保留期按"最近使用"淘汰）
        calls = []
        original = preview_module._generate_jpeg
        preview_module._generate_jpeg = lambda *args, **kwargs: (
            calls.append(1),
            original(*args, **kwargs),
        )[1]
        try:
            preview(upload, superuser, query=f"?size={SIZE_THUMB}")
        finally:
            preview_module._generate_jpeg = original
        assert calls == []
        # 大图档位独立缓存
        preview(upload, superuser, query=f"?size={SIZE_PREVIEW}")
        assert os.path.exists(preview_cache_path(upload, SIZE_PREVIEW))

    def test_invalid_size_rejected(self, superuser, media_root):
        """非图片的 size 参数不生效；非法档位不静默成功。"""
        upload = make_upload(superuser, "pic.png", png_bytes(), "image/png")
        response = preview(upload, superuser, query="?size=bogus")
        assert response.data["code"] == PREVIEW_UNSUPPORTED_CODE

    def test_text_preview_truncated(self, superuser, media_root):
        upload = make_upload(superuser, "a.txt", b"hello preview\n" * 10, "text/plain")
        response = preview(upload, superuser)
        assert response.status_code == 200
        assert response["Content-Type"].startswith("text/plain")
        assert b"hello preview" in response.content

    def test_text_preview_truncation_header(self, superuser, media_root, monkeypatch):
        """超上限即截断：响应头 X-Preview-Truncated 供前端提示"过大，请下载"。"""
        monkeypatch.setattr(type(SysConfig), "FILE_PREVIEW_TEXT_MAX_BYTES", property(lambda self: 100), raising=False)
        upload = make_upload(superuser, "big.txt", b"x" * 5000, "text/plain")
        response = preview(upload, superuser)
        assert response["X-Preview-Truncated"] == "1"
        assert len(response.content) == 100

    def test_binary_text_file_not_previewable(self, superuser, media_root):
        """后缀像文本但内容含 NUL：判定为二进制，不给预览（避免吐乱码）。"""
        upload = make_upload(superuser, "a.log", b"abc\x00\x01\x02", "text/plain")
        response = preview(upload, superuser)
        assert response.data["code"] == PREVIEW_UNSUPPORTED_CODE

    def test_pdf_inline(self, superuser, media_root):
        upload = make_upload(superuser, "a.pdf", b"%PDF-1.4 fake", "application/pdf")
        response = preview(upload, superuser)
        assert response.status_code == 200
        assert response["Content-Disposition"].startswith("inline")

    def test_unsupported_type_business_code(self, superuser, media_root):
        upload = make_upload(superuser, "a.zip", b"PK\x03\x04", "application/zip")
        response = preview(upload, superuser)
        assert response.data["code"] == PREVIEW_UNSUPPORTED_CODE
        assert response.data["detail"]

    def test_other_users_file_not_previewable(self, superuser, normal_user, media_root):
        """越权：他人文件走数据权限（默认拒绝），不能凭 pk 预览。"""
        upload = make_upload(superuser, "pic.png", png_bytes(), "image/png")
        response = preview(upload, normal_user)
        assert response.status_code in (400, 403, 404)


class TestPreviewCacheRecycle:
    def test_hard_delete_removes_cache(self, superuser, media_root):
        upload = make_upload(superuser, "pic.png", png_bytes(), "image/png")
        cache_path = preview_cache_path(upload, SIZE_THUMB)
        preview(upload, superuser, query=f"?size={SIZE_THUMB}")
        assert os.path.exists(cache_path)

        upload.hard_delete()
        assert not os.path.exists(cache_path)

    def test_soft_delete_keeps_cache(self, superuser, media_root):
        """软删除可恢复：缓存保留，恢复后仍能直接命中。"""
        upload = make_upload(superuser, "pic.png", png_bytes(), "image/png")
        preview(upload, superuser, query=f"?size={SIZE_THUMB}")
        cache_path = preview_cache_path(upload, SIZE_THUMB)

        upload.delete()
        assert os.path.exists(cache_path)

    def test_orphan_and_expired_cleanup(self, superuser, media_root):
        kept = make_upload(superuser, "kept.png", png_bytes(), "image/png")
        preview(kept, superuser, query=f"?size={SIZE_THUMB}")

        orphan = make_upload(superuser, "orphan.png", png_bytes(), "image/png")
        preview(orphan, superuser, query=f"?size={SIZE_THUMB}")
        # queryset delete 不走实例 hard_delete 钩子（模拟历史遗留/异常退出留下的孤儿）
        UploadFile.all_objects.filter(pk=orphan.pk).delete()
        assert os.path.exists(preview_cache_path(orphan, SIZE_THUMB))

        # 保留期 0 = 不按时间淘汰，只清孤儿
        result = clean_preview_cache(keep_days=0)
        assert result["removed_orphan"] == 1
        assert result["removed_expired"] == 0
        assert not os.path.exists(preview_cache_path(orphan, SIZE_THUMB))
        assert os.path.exists(preview_cache_path(kept, SIZE_THUMB))

        # 把 kept 的缓存 mtime 推到保留期外 → 按过期清理
        old = time.time() - 10 * 86400
        os.utime(preview_cache_path(kept, SIZE_THUMB), (old, old))
        result = clean_preview_cache(keep_days=7)
        assert result["removed_expired"] == 1
        assert not os.path.exists(preview_cache_path(kept, SIZE_THUMB))

    def test_clean_task_returns_removed_count(self, superuser, media_root):
        upload = make_upload(superuser, "pic.png", png_bytes(), "image/png")
        preview(upload, superuser, query=f"?size={SIZE_THUMB}")
        UploadFile.all_objects.filter(pk=upload.pk).delete()
        assert auto_clean_preview_cache(keep_days=0) == 1
