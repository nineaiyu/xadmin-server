# -*- coding: utf-8 -*-
"""文件访问审计与上传安全策略集成测试。

口径钉死：
- 上传扩展名策略 fail-closed：黑名单优先、白名单非空时只允许名单内；
- 上传内容 magic 抽检 fail-closed：扩展名与实际内容类型不符拒绝、无法识别的
  二进制拒绝（伪装扩展名的可执行内容存储侧即拒）；
- 上传 / 下载 / 预览 / 删除四类动作留元数据（下载走受鉴权的 download 端点）；
- 访问记录端点返回逐动作计数。
"""

import io
import zipfile

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from file.models import FileAccessLog
from file.utils.file_audit import (
    KIND_AUDIO,
    KIND_BINARY,
    KIND_IMAGE,
    KIND_OOXML_WORD,
    KIND_PDF,
    KIND_TEXT,
    KIND_VIDEO,
    KIND_ZIP,
    detect_content_kind,
    read_file_head,
    validate_upload_content,
)

pytestmark = pytest.mark.django_db

UPLOAD_URL = "/api/file/file/upload"

PNG_HEAD = b"\x89PNG\r\n\x1a\n"


def _upload(client, name="report.txt", content=b"hello world", content_type="text/plain"):
    file_obj = SimpleUploadedFile(name, content, content_type=content_type)
    return client.post(UPLOAD_URL, {"file": file_obj}, format="multipart")


def _docx_bytes() -> bytes:
    """合法 docx（zip 容器 + OOXML 包内标记），供「判定顺序」用例消费。"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as package:
        package.writestr("[Content_Types].xml", "<Types/>")
        package.writestr("word/document.xml", "<document/>")
    return buffer.getvalue()


class TestUploadPolicy:
    def test_blocked_extension_rejected(self, auth_client):
        resp = _upload(auth_client, name="evil.sh", content=b"echo hi", content_type="text/x-sh")
        assert resp.data["code"] == 1002, resp.data
        rejected = FileAccessLog.objects.filter(action=FileAccessLog.Action.UPLOAD, result=False)
        assert rejected.count() == 1
        assert rejected.first().filename == "evil.sh"

    def test_allow_list_enforced(self, auth_client, settings):
        settings.SECURITY_UPLOAD_ALLOW_EXTENSIONS = ["pdf"]
        resp = _upload(auth_client, name="note.txt")
        assert resp.data["code"] == 1002, resp.data
        resp = _upload(auth_client, name="note.pdf", content=b"%PDF-1.4", content_type="application/pdf")
        assert resp.data["code"] == 1000, resp.data

    def test_config_exposes_policy(self, auth_client):
        resp = auth_client.get("/api/file/file/config")
        assert resp.data["code"] == 1000, resp.data
        policy = resp.data["data"]["upload_policy"]
        assert "sh" in policy["block_extensions"]
        assert policy["allow_extensions"] == []


class TestContentMagic:
    """内容 magic 抽检：伪装扩展名拒绝 / 合法文件不误伤 / 未知二进制拒绝。"""

    def test_detect_content_kind_signatures(self):
        assert detect_content_kind(PNG_HEAD) == KIND_IMAGE
        assert detect_content_kind(b"\xff\xd8\xff\xe0jfi") == KIND_IMAGE
        assert detect_content_kind(b"%PDF-1.4\n%report") == KIND_PDF
        assert detect_content_kind(b"PK\x03\x04plain-archive") == KIND_ZIP
        assert detect_content_kind(_docx_bytes()) == KIND_OOXML_WORD
        assert detect_content_kind(b"ID3\x03tagged") == KIND_AUDIO
        assert detect_content_kind(b"\x00\x00\x00\x18ftypisom") == KIND_VIDEO
        assert detect_content_kind(b"\x1a\x45\xdf\xa3webm") == KIND_VIDEO
        assert detect_content_kind(b"\xef\xbb\xbf" + "你好".encode()) == KIND_TEXT
        assert detect_content_kind(b"plain text") == KIND_TEXT
        assert detect_content_kind(b"") == KIND_TEXT
        assert detect_content_kind(b"\x00\x01binary-mess") == KIND_BINARY
        # BMP 两字节魔数不单判：文本以 "BM" 开头不得误判成图片
        assert detect_content_kind("BMX 排班表".encode()) == KIND_TEXT
        assert detect_content_kind(b"BM\x36\x00\x00\x00\x00\x00\x00\x00") == KIND_IMAGE

    def test_valid_docx_is_not_rejected(self):
        """判定顺序：zip 容器先看 OOXML 包内标记——合法 docx 不能按「类型不符」拒。"""
        assert validate_upload_content("report.docx", SimpleUploadedFile("report.docx", _docx_bytes())) == ""
        # 普通压缩包改名 .docx（无 OOXML 标记）：类型不符拒绝
        assert validate_upload_content("fake.docx", SimpleUploadedFile("fake.docx", b"PK\x03\x04plain")) != ""

    def test_exe_renamed_as_image_rejected(self):
        """伪装扩展名（可执行内容改名 .png）：存储侧即拒。"""
        exe_head = b"MZ\x90\x00\x03\x00\x00\x00\x04\x00\x00\x00\xff\xff"
        assert validate_upload_content("avatar.png", SimpleUploadedFile("avatar.png", exe_head)) != ""

    def test_unmapped_extension_text_allowed_binary_rejected(self):
        """未映射扩展名：文本放行（自定义文本文件不受影响）、二进制拒绝（fail-closed）。"""
        assert validate_upload_content("notes.dat", SimpleUploadedFile("notes.dat", b"plain config")) == ""
        assert validate_upload_content("blob.dat", SimpleUploadedFile("blob.dat", b"\x00\x01\x02")) != ""

    def test_text_file_with_image_extension_rejected(self):
        assert validate_upload_content("fake.png", SimpleUploadedFile("fake.png", b"not an image")) != ""

    def test_unreadable_head_rejected(self):
        """文件头不可读（异常句柄）：fail-closed 拒绝，不静默放行。"""

        class _Broken:
            def tell(self):
                raise OSError("no tell")

            def seek(self, offset):
                raise OSError("no seek")

        assert read_file_head(_Broken()) is None
        assert validate_upload_content("x.txt", _Broken()) != ""

    def test_upload_endpoint_rejects_mismatched_content(self, auth_client):
        """端到端：.png 名 + 文本内容 → 1002（留失败审计）；真实 PNG 头 → 1000。"""
        resp = _upload(auth_client, name="fake.png", content=b"just text", content_type="image/png")
        assert resp.data["code"] == 1002, resp.data
        rejected = FileAccessLog.objects.filter(filename="fake.png", action=FileAccessLog.Action.UPLOAD, result=False)
        assert rejected.count() == 1

        resp = _upload(auth_client, name="real.png", content=PNG_HEAD + b"body", content_type="image/png")
        assert resp.data["code"] == 1000, resp.data


class TestFileAccessAudit:
    def test_upload_download_and_access_logs(self, auth_client):
        resp = _upload(auth_client)
        assert resp.data["code"] == 1000, resp.data
        upload_pk = resp.data["data"][0]["pk"]
        assert FileAccessLog.objects.filter(file_id=upload_pk, action=FileAccessLog.Action.UPLOAD).exists()

        resp = auth_client.get(f"/api/file/file/{upload_pk}/download")
        assert resp.status_code == 200
        assert FileAccessLog.objects.filter(file_id=upload_pk, action=FileAccessLog.Action.DOWNLOAD).exists()

        resp = auth_client.get(f"/api/file/file/{upload_pk}/access-logs")
        assert resp.data["code"] == 1000, resp.data
        assert resp.data["data"]["counts"]["upload"] == 1
        assert resp.data["data"]["counts"]["download"] == 1
        assert resp.data["data"]["results"][0]["action"]["value"] == "download"

    def test_delete_logged(self, auth_client):
        resp = _upload(auth_client, name="to-delete.txt")
        upload_pk = resp.data["data"][0]["pk"]
        resp = auth_client.delete(f"/api/file/file/{upload_pk}")
        assert resp.data["code"] == 1000, resp.data
        assert FileAccessLog.objects.filter(file_id=upload_pk, action=FileAccessLog.Action.DELETE).exists()

    def test_download_direct_falls_back_on_local_backend(self, auth_client):
        """预签名直连：本地后端返回 direct=false（调用方回退服务端中转下载）。"""
        resp = _upload(auth_client, name="direct.txt")
        upload_pk = resp.data["data"][0]["pk"]
        resp = auth_client.get(f"/api/file/file/{upload_pk}/download?direct=1")
        assert resp.data["code"] == 1000, resp.data
        assert resp.data["data"]["direct"] is False
        assert resp.data["data"]["url"] == ""
        # 直连尝试同样先完成鉴权与审计（记一次下载）
        assert FileAccessLog.objects.filter(file_id=upload_pk, action=FileAccessLog.Action.DOWNLOAD).exists()

    def test_download_missing_file_logs_failure(self, auth_client, superuser):
        from file.models import UploadFile

        # md5sum 非空跳过 save() 的文件读取；filepath 指向不存在的文件用于覆盖失败分支
        row = UploadFile.objects.create(
            creator=superuser, filename="gone.txt", filepath="upload/gone.txt", md5sum="x", filesize=0
        )
        resp = auth_client.get(f"/api/file/file/{row.pk}/download")
        assert resp.data["code"] == 1001
        assert FileAccessLog.objects.filter(file_id=row.pk, action="download", result=False).exists()

    def test_clean_expired_logs(self, settings):
        from datetime import timedelta

        from django.utils import timezone

        from file.utils.file_audit import clean_expired_file_access_logs

        settings.FILE_ACCESS_LOG_KEEP_DAYS = 30
        old = FileAccessLog.objects.create(filename="old.txt", action=FileAccessLog.Action.DOWNLOAD)
        FileAccessLog.objects.filter(pk=old.pk).update(created_time=timezone.now() - timedelta(days=60))
        fresh = FileAccessLog.objects.create(filename="new.txt", action=FileAccessLog.Action.DOWNLOAD)
        removed = clean_expired_file_access_logs()
        assert removed == 1
        assert FileAccessLog.objects.filter(pk=fresh.pk).exists()
