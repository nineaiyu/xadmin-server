# -*- coding: utf-8 -*-
"""common/utils/media.py：媒体文件响应（目录拒绝、404、304 与文件流）。"""
import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import Http404, HttpResponseNotModified
from django.test import RequestFactory

from common.utils.media import get_media_path, media_serve


@pytest.fixture
def media_root(tmp_path, settings):
    settings.MEDIA_ROOT = str(tmp_path)
    return tmp_path


@pytest.fixture
def image_file(media_root):
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 32
    file = SimpleUploadedFile("logo.png", png, content_type="image/png")
    target = media_root / "logo.png"
    target.write_bytes(png)
    return target


@pytest.fixture
def rf():
    return RequestFactory(HTTP_USER_AGENT="pytest-agent")


class TestMediaServe:
    def test_serve_existing_file(self, rf, media_root, image_file):
        request = rf.get("/media/logo.png")
        response = media_serve(request, "logo.png", document_root=str(media_root))
        assert response.status_code == 200
        assert response["Content-Type"] == "image/png"
        assert "Last-Modified" in response.headers
        assert b"".join(response.streaming_content) == image_file.read_bytes()

    def test_directory_index_forbidden(self, rf, media_root):
        request = rf.get("/media/sub/")
        with pytest.raises(Http404):
            media_serve(request, "sub", document_root=str(media_root))

    def test_missing_file_raises_404(self, rf, media_root):
        request = rf.get("/media/nope.png")
        with pytest.raises(Http404):
            media_serve(request, "nope.png", document_root=str(media_root))

    def test_not_modified_returns_304(self, rf, media_root, image_file):
        from django.utils.http import http_date

        request = rf.get(
            "/media/logo.png", HTTP_IF_MODIFIED_SINCE=http_date(image_file.stat().st_mtime + 10)
        )
        response = media_serve(request, "logo.png", document_root=str(media_root))
        assert isinstance(response, HttpResponseNotModified)


class TestGetMediaPath:
    def test_non_thumbnail_path_returns_none(self):
        # 路径段数不足 5 时不处理缩略图逻辑
        assert get_media_path("a/b.png") is None

    def test_thumbnail_without_size_suffix_returns_none(self, django_db_blocker):
        # 5 段路径但末段不含 "_" 尺寸后缀 → None
        with django_db_blocker.unblock():
            assert get_media_path("system/userinfo/avatar/0/pic.png") is None
