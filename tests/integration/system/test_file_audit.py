# -*- coding: utf-8 -*-
"""文件访问审计与上传安全策略（F-8）集成测试。

口径钉死：
- 上传扩展名策略 fail-closed：黑名单优先、白名单非空时只允许名单内；
- 上传 / 下载 / 预览 / 删除四类动作留元数据（下载走受鉴权的 download 端点）；
- 访问记录端点返回逐动作计数。
"""

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from system.models import FileAccessLog

pytestmark = pytest.mark.django_db

UPLOAD_URL = "/api/system/file/upload"


def _upload(client, name="report.txt", content=b"hello world", content_type="text/plain"):
    file_obj = SimpleUploadedFile(name, content, content_type=content_type)
    return client.post(UPLOAD_URL, {"file": file_obj}, format="multipart")


class TestUploadPolicy:
    def test_blocked_extension_rejected(self, auth_client):
        resp = _upload(auth_client, name="evil.sh", content=b"echo hi", content_type="text/x-sh")
        assert resp.data["code"] == 1002, resp.data
        assert FileAccessLog.objects.filter(action=FileAccessLog.Action.UPLOAD).count() == 0

    def test_allow_list_enforced(self, auth_client, settings):
        settings.SECURITY_UPLOAD_ALLOW_EXTENSIONS = ["pdf"]
        resp = _upload(auth_client, name="note.txt")
        assert resp.data["code"] == 1002, resp.data
        resp = _upload(auth_client, name="note.pdf", content=b"%PDF-1.4", content_type="application/pdf")
        assert resp.data["code"] == 1000, resp.data

    def test_config_exposes_policy(self, auth_client):
        resp = auth_client.get("/api/system/file/config")
        assert resp.data["code"] == 1000, resp.data
        policy = resp.data["data"]["upload_policy"]
        assert "sh" in policy["block_extensions"]
        assert policy["allow_extensions"] == []


class TestFileAccessAudit:
    def test_upload_download_and_access_logs(self, auth_client):
        resp = _upload(auth_client)
        assert resp.data["code"] == 1000, resp.data
        upload_pk = resp.data["data"][0]["pk"]
        assert FileAccessLog.objects.filter(file_id=upload_pk, action=FileAccessLog.Action.UPLOAD).exists()

        resp = auth_client.get(f"/api/system/file/{upload_pk}/download")
        assert resp.status_code == 200
        assert FileAccessLog.objects.filter(file_id=upload_pk, action=FileAccessLog.Action.DOWNLOAD).exists()

        resp = auth_client.get(f"/api/system/file/{upload_pk}/access-logs")
        assert resp.data["code"] == 1000, resp.data
        assert resp.data["data"]["counts"]["upload"] == 1
        assert resp.data["data"]["counts"]["download"] == 1
        assert resp.data["data"]["results"][0]["action"]["value"] == "download"

    def test_delete_logged(self, auth_client):
        resp = _upload(auth_client, name="to-delete.txt")
        upload_pk = resp.data["data"][0]["pk"]
        resp = auth_client.delete(f"/api/system/file/{upload_pk}")
        assert resp.data["code"] == 1000, resp.data
        assert FileAccessLog.objects.filter(file_id=upload_pk, action=FileAccessLog.Action.DELETE).exists()

    def test_download_direct_falls_back_on_local_backend(self, auth_client):
        """预签名直连（P-4）：本地后端返回 direct=false（调用方回退服务端中转下载）。"""
        resp = _upload(auth_client, name="direct.txt")
        upload_pk = resp.data["data"][0]["pk"]
        resp = auth_client.get(f"/api/system/file/{upload_pk}/download?direct=1")
        assert resp.data["code"] == 1000, resp.data
        assert resp.data["data"]["direct"] is False
        assert resp.data["data"]["url"] == ""
        # 直连尝试同样先完成鉴权与审计（记一次下载）
        assert FileAccessLog.objects.filter(file_id=upload_pk, action=FileAccessLog.Action.DOWNLOAD).exists()

    def test_download_missing_file_logs_failure(self, auth_client, superuser):
        from system.models import UploadFile

        # md5sum 非空跳过 save() 的文件读取；filepath 指向不存在的文件用于覆盖失败分支
        row = UploadFile.objects.create(
            creator=superuser, filename="gone.txt", filepath="upload/gone.txt", md5sum="x", filesize=0
        )
        resp = auth_client.get(f"/api/system/file/{row.pk}/download")
        assert resp.data["code"] == 1001
        assert FileAccessLog.objects.filter(file_id=row.pk, action="download", result=False).exists()

    def test_clean_expired_logs(self, settings):
        from datetime import timedelta

        from django.utils import timezone

        from system.utils.file_audit import clean_expired_file_access_logs

        settings.FILE_ACCESS_LOG_KEEP_DAYS = 30
        old = FileAccessLog.objects.create(filename="old.txt", action=FileAccessLog.Action.DOWNLOAD)
        FileAccessLog.objects.filter(pk=old.pk).update(created_time=timezone.now() - timedelta(days=60))
        fresh = FileAccessLog.objects.create(filename="new.txt", action=FileAccessLog.Action.DOWNLOAD)
        removed = clean_expired_file_access_logs()
        assert removed == 1
        assert FileAccessLog.objects.filter(pk=fresh.pk).exists()
