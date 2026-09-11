# -*- coding: utf-8 -*-
"""O2/O3 Office 在线预览（ADR-013）：类型分派 / 可用性降级 / 转换与缓存 / API 链路。

真实转换（LibreOffice）用例在未安装转换器的环境自动跳过；其余用例通过
monkeypatch 转换入口，保证 CI 无 LibreOffice 时链路逻辑仍被完整覆盖。
"""

import io
import os
import shutil
import zipfile

import pytest
from django.core.files.base import ContentFile
from rest_framework.test import APIRequestFactory, force_authenticate

from common.core.config import SysConfig
from system.models import UploadFile
from system.utils import preview as preview_module
from system.utils.preview import (
    KIND_OFFICE,
    PREVIEW_STATUS_READY,
    PREVIEW_STATUS_UNSUPPORTED,
    convert_office_to_pdf,
    ensure_office_pdf,
    office_cache_path,
    office_converter_bin,
    office_preview_available,
    preview_kind,
)
from system.views.admin.file import PREVIEW_PREPARING_CODE, UploadFileViewSet

pytestmark = pytest.mark.django_db

FILE_URL = "/api/system/file"

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

# 模块导入期只做文件系统探测：office_converter_bin() 会读 SysConfig（DB），不能在收集阶段调用
CONVERTER_AVAILABLE = any(
    (os.path.isabs(candidate) and os.access(candidate, os.X_OK)) or shutil.which(candidate)
    for candidate in preview_module.CONVERTER_CANDIDATES
)
requires_converter = pytest.mark.skipif(not CONVERTER_AVAILABLE, reason="本机未安装 LibreOffice（soffice）")


@pytest.fixture
def media_root(tmp_path, settings):
    settings.MEDIA_ROOT = str(tmp_path)
    return tmp_path


def make_upload(user, name, content, mime):
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


def docx_bytes(text="XADMIN OFFICE PREVIEW"):
    """最小可用 docx（OOXML zip 三件套）：LibreOffice 可直接打开。"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument'
            '.wordprocessingml.document.main+xml"/></Types>',
        )
        archive.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships'
            '/officeDocument" Target="word/document.xml"/></Relationships>',
        )
        archive.writestr(
            "word/document.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
            f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>",
        )
    return buffer.getvalue()


def xlsx_bytes():
    import openpyxl

    buffer = io.BytesIO()
    workbook = openpyxl.Workbook()
    workbook.active["A1"] = "xadmin"
    workbook.save(buffer)
    return buffer.getvalue()


def patch_config(monkeypatch, key, value):
    """patch SysConfig property（单测动态改配置的既定范式）。"""
    monkeypatch.setattr(type(SysConfig), key, property(lambda self: value), raising=False)


def patch_converter(monkeypatch, available=True):
    monkeypatch.setattr(
        preview_module, "office_converter_bin", (lambda: "/usr/bin/soffice") if available else (lambda: None)
    )


def fake_convert(upload):
    """假转换：直接在缓存路径写一个最小 PDF（用于链路断言，不依赖 LibreOffice）。"""
    target = office_cache_path(upload)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "wb") as handle:
        handle.write(b"%PDF-1.4 fake")
    return target


def preview(upload, user, query=""):
    factory = APIRequestFactory()
    request = factory.get(f"{FILE_URL}/{upload.pk}/preview{query}")
    force_authenticate(request, user=user)
    return UploadFileViewSet.as_view({"get": "preview"})(request, pk=str(upload.pk))


class TestOfficeKindDispatch:
    @pytest.mark.parametrize(
        "mime,name,expected",
        [
            (DOCX_MIME, "a.docx", KIND_OFFICE),
            ("application/msword", "a.doc", KIND_OFFICE),
            (XLSX_MIME, "a.xlsx", KIND_OFFICE),
            ("application/vnd.ms-powerpoint", "a.ppt", KIND_OFFICE),
            ("application/octet-stream", "a.pptx", KIND_OFFICE),
            ("", "a.odt", KIND_OFFICE),
            # 文本优先：csv 仍按文本预览（零转换成本）
            ("text/csv", "a.csv", "text"),
            ("", "a.csv", "text"),
            ("application/pdf", "a.pdf", "pdf"),
        ],
    )
    def test_dispatch(self, mime, name, expected):
        assert preview_kind(UploadFile(filename=name, mime_type=mime)) == expected


class TestOfficeAvailability:
    def test_disabled_by_switch(self, monkeypatch, superuser, media_root):
        upload = make_upload(superuser, "a.docx", docx_bytes(), DOCX_MIME)
        patch_converter(monkeypatch)
        patch_config(monkeypatch, "FILE_OFFICE_PREVIEW_ENABLED", False)
        assert office_preview_available(upload) is False
        assert ensure_office_pdf(upload) == (None, PREVIEW_STATUS_UNSUPPORTED)

    def test_converter_missing(self, monkeypatch, superuser, media_root):
        upload = make_upload(superuser, "a.docx", docx_bytes(), DOCX_MIME)
        patch_converter(monkeypatch, available=False)
        assert office_preview_available(upload) is False
        response = preview(upload, superuser)
        assert response.status_code == 200
        assert response.data["code"] == 1005  # 降级为「不支持预览」，下载不受影响

    def test_size_limit(self, monkeypatch, superuser, media_root):
        upload = make_upload(superuser, "a.docx", docx_bytes(), DOCX_MIME)
        patch_converter(monkeypatch)
        patch_config(monkeypatch, "FILE_OFFICE_MAX_BYTES", 10)
        assert office_preview_available(upload) is False

    def test_converter_bin_resolution(self, monkeypatch):
        # 显式配置优先（路径必须真实可执行，防配置错误导致误判可用）
        patch_config(monkeypatch, "FILE_OFFICE_SOFFICE_BIN", "/bin/sh")
        assert office_converter_bin() == "/bin/sh"
        # 配置指向不存在的路径 → 回退自动探测（本机装了 LibreOffice 则是真实路径）
        patch_config(monkeypatch, "FILE_OFFICE_SOFFICE_BIN", "/nonexistent/soffice")
        resolved = office_converter_bin()
        if CONVERTER_AVAILABLE:
            assert resolved and resolved != "/nonexistent/soffice"


class TestOfficeConvertAndServe:
    def test_cache_hit_served(self, monkeypatch, superuser, media_root):
        upload = make_upload(superuser, "a.docx", docx_bytes(), DOCX_MIME)
        patch_converter(monkeypatch)
        fake_convert(upload)
        path, status = ensure_office_pdf(upload)
        assert status == PREVIEW_STATUS_READY and os.path.exists(path)
        response = preview(upload, superuser)
        assert response.status_code == 200
        assert response["Content-Type"] == "application/pdf"
        assert response["Content-Disposition"].startswith("inline")

    def test_eager_task_converts_and_serves(self, monkeypatch, superuser, media_root):
        """eager celery 下请求触发任务即完成转换（链路：任务 → 缓存 → inline 响应）。"""
        upload = make_upload(superuser, "a.docx", docx_bytes(), DOCX_MIME)
        patch_converter(monkeypatch)
        monkeypatch.setattr(preview_module, "convert_office_to_pdf", fake_convert)
        response = preview(upload, superuser)
        assert response.status_code == 200
        assert response["Content-Type"] == "application/pdf"
        assert os.path.exists(office_cache_path(upload))

    def test_preparing_when_conversion_not_finished(self, monkeypatch, superuser, media_root):
        """请求侧等待窗口耗尽 → 业务码 1006（前端稍后重试）。"""
        from system.tasks import convert_office_preview_task

        upload = make_upload(superuser, "a.docx", docx_bytes(), DOCX_MIME)
        patch_converter(monkeypatch)
        patch_config(monkeypatch, "FILE_OFFICE_WAIT_SECONDS", 0)
        monkeypatch.setattr(convert_office_preview_task, "apply_async", lambda *args, **kwargs: None)
        response = preview(upload, superuser)
        # HTTP 425 + 业务码 1006：前端 http 层对该状态不弹全局错误，由抽屉轮询重试
        assert response.status_code == 425
        assert response.data["code"] == PREVIEW_PREPARING_CODE
        assert response.data["data"]["status"] == "preparing"
        # 转换锁已占位（避免并发重复派发）
        from django.core.cache import cache

        assert cache.get(f"office_converting_{upload.pk}")

    def test_conversion_failure_degrades(self, monkeypatch, superuser, media_root):
        """转换失败（产物缺失）→ 1005，不 500、不影响下载。"""
        upload = make_upload(superuser, "a.docx", docx_bytes(), DOCX_MIME)
        patch_converter(monkeypatch)
        patch_config(monkeypatch, "FILE_OFFICE_WAIT_SECONDS", 0)
        monkeypatch.setattr(preview_module, "convert_office_to_pdf", lambda _upload: None)
        response = preview(upload, superuser)
        assert response.data["code"] == 1005

    def test_cache_recycled_with_source(self, monkeypatch, superuser, media_root):
        """落盘纪律：源文件硬删 → 转换产物随缓存目录联动清理。"""
        upload = make_upload(superuser, "a.docx", docx_bytes(), DOCX_MIME)
        patch_converter(monkeypatch)
        fake_convert(upload)
        target = office_cache_path(upload)
        assert os.path.exists(target)
        upload.hard_delete()
        assert not os.path.exists(target)


@requires_converter
class TestRealConversion:
    """真实 LibreOffice 转换（无转换器环境自动跳过）：docx/xlsx 常见样本。"""

    def test_docx_to_pdf(self, superuser, media_root):
        upload = make_upload(superuser, "sample.docx", docx_bytes("XADMIN HELLO"), DOCX_MIME)
        target = convert_office_to_pdf(upload)
        assert target and os.path.exists(target)
        with open(target, "rb") as handle:
            assert handle.read(5) == b"%PDF-"

    def test_xlsx_to_pdf(self, superuser, media_root):
        upload = make_upload(superuser, "sample.xlsx", xlsx_bytes(), XLSX_MIME)
        target = convert_office_to_pdf(upload)
        assert target and os.path.exists(target)
        with open(target, "rb") as handle:
            assert handle.read(5) == b"%PDF-"

    def test_timeout_degrades(self, monkeypatch, superuser, media_root):
        """超时保护：极短超时下转换失败但不抛异常（降级为不可预览）。"""
        patch_config(monkeypatch, "FILE_OFFICE_CONVERT_TIMEOUT", 0)
        upload = make_upload(superuser, "slow.docx", docx_bytes(), DOCX_MIME)
        assert convert_office_to_pdf(upload) is None
        assert shutil.which("soffice") or True  # 仅断言未抛异常
