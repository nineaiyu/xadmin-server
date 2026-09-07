# -*- coding: utf-8 -*-
"""FEAT-2：软删除与回收站集成测试（notice / upload）。"""
from datetime import timedelta

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from notifications.models import MessageContent
from system.models import UploadFile

pytestmark = pytest.mark.django_db

NOTICE_URL = "/api/notifications/notice-messages"


def _create_notice_via_api(auth_client, superuser, title="回收站测试公告"):
    resp = auth_client.post(
        NOTICE_URL,
        {"title": title, "message": "<p>hello</p>", "notice_type": 2, "level": "info",
         "publish": True, "notice_user": [superuser.pk], "files": []},
        format="json",
    )
    assert resp.status_code == 200, resp.data
    assert resp.data["code"] == 1000, resp.data
    return resp.data["data"]["pk"]


class TestNoticeRecycleBin:
    def test_delete_soft_recycle_restore(self, auth_client, superuser):
        pk = _create_notice_via_api(auth_client, superuser)

        # 删除 → 默认列表不可见，物理行仍在
        resp = auth_client.delete(f"{NOTICE_URL}/{pk}")
        assert resp.status_code == 200
        assert resp.data["code"] == 1000
        assert not MessageContent.objects.filter(pk=pk).exists()
        obj = MessageContent.all_objects.get(pk=pk)
        assert obj.deleted_at is not None

        # 回收站列表可见
        resp = auth_client.get(f"{NOTICE_URL}/recycle")
        assert resp.status_code == 200
        assert any(r["pk"] == pk for r in resp.data["data"]["results"])

        # 恢复 → 默认列表重新可见
        resp = auth_client.patch(f"{NOTICE_URL}/recycle/restore", {"pks": [pk]}, format="json")
        assert resp.status_code == 200, resp.data
        assert MessageContent.objects.filter(pk=pk).exists()

    def test_purge_physical_delete(self, auth_client, superuser):
        pk = _create_notice_via_api(auth_client, superuser)
        auth_client.delete(f"{NOTICE_URL}/{pk}")

        resp = auth_client.delete(f"{NOTICE_URL}/recycle/purge", {"pks": [pk]}, format="json")
        assert resp.status_code == 200, resp.data
        assert not MessageContent.all_objects.filter(pk=pk).exists()

    def test_purge_expired_only(self, auth_client, superuser):
        old_pk = _create_notice_via_api(auth_client, superuser, title="过期公告")
        recent_pk = _create_notice_via_api(auth_client, superuser, title="近期公告")
        for pk in (old_pk, recent_pk):
            auth_client.delete(f"{NOTICE_URL}/{pk}")

        MessageContent.all_objects.filter(pk=old_pk).update(deleted_at=timezone.now() - timedelta(days=31))

        # 不传 pks：只清除超过保留期（默认 30 天）的数据
        resp = auth_client.delete(f"{NOTICE_URL}/recycle/purge", format="json")
        assert resp.status_code == 200, resp.data
        assert not MessageContent.all_objects.filter(pk=old_pk).exists()
        assert MessageContent.all_objects.filter(pk=recent_pk).exists()


class TestSoftDeleteModel:
    def test_upload_soft_delete_keeps_file(self, auth_client):
        """UploadFile 软删除仅做标记，物理文件与行保留在回收站。"""
        upload = UploadFile.objects.create(
            filename="a.txt", filesize=1, mime_type="text/plain", md5sum="x" * 32,
            filepath=SimpleUploadedFile("a.txt", b"hello"), is_upload=True,
        )
        upload.delete()
        assert UploadFile.objects.filter(pk=upload.pk).count() == 0
        assert UploadFile.all_objects.filter(pk=upload.pk, deleted_at__isnull=False).count() == 1

    def test_hard_delete_removes_row(self, auth_client):
        upload = UploadFile.objects.create(
            filename="b.txt", filesize=1, mime_type="text/plain", md5sum="y" * 32,
            filepath=SimpleUploadedFile("b.txt", b"world"), is_upload=True,
        )
        upload.hard_delete()
        assert UploadFile.all_objects.filter(pk=upload.pk).count() == 0
