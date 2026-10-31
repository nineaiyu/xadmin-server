# -*- coding: utf-8 -*-
"""文件预览响应类型守护：PDF 预览固定 application/pdf，不回显客户端上传的 mime。"""

import io

import pytest

from file.models import UploadFile
from file.services.file_impl import PREVIEW_STATE_PDF, resolve_preview
from file.utils.preview import KIND_PDF

pytestmark = pytest.mark.django_db


def test_pdf_preview_forces_application_pdf(monkeypatch, superuser):
    """`.pdf + text/html` 的上传记录在预览时必须按 application/pdf 返回。"""
    monkeypatch.setattr("file.services.file_impl.storage_exists", lambda name: True)
    monkeypatch.setattr("file.services.file_impl.storage_open", lambda name, mode: io.BytesIO(b"%PDF-1.4 fake"))
    upload = UploadFile.objects.create(
        creator=superuser,
        filename="evil.pdf",
        filepath="upload/evil.pdf",
        md5sum="a" * 32,
        filesize=12,
        mime_type="text/html",
    )

    state, payload = resolve_preview(upload, KIND_PDF)

    assert state == PREVIEW_STATE_PDF
    assert payload[1] == "application/pdf"


def test_upload_policy_blocks_executable_markup():
    """html/svg/xml 上传会形成同源存储型 XSS：扩展名策略必须拒绝。"""
    from file.utils.file_audit import validate_upload_extension

    for name in ("evil.html", "evil.htm", "evil.svg", "evil.xhtml", "evil.xml"):
        assert validate_upload_extension(name), name
    assert validate_upload_extension("report.pdf") == ""
