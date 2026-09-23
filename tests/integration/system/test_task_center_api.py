# -*- coding: utf-8 -*-
"""任务中心API 集成测试：统一列表 / 取消 / 重跑。

列表跨三类记录聚合（数据域与下载中心一致）；取消 PENDING 立即终态；
重跑走白名单（导出 / 导入 / 报表），派发用 stub 记录（不真跑任务）。
"""

import pytest
from rest_framework.test import APIClient

from system.models.export import ExportRecord
from system.models.task import TaskExecution

pytestmark = pytest.mark.django_db

UNIFIED_URL = "/api/system/tasks/unified"


@pytest.fixture(autouse=True)
def _clean_records():
    ExportRecord.objects.all().delete()
    TaskExecution.objects.all().delete()
    yield
    ExportRecord.objects.all().delete()
    TaskExecution.objects.all().delete()


@pytest.fixture(autouse=True)
def _no_broker(monkeypatch):
    from server.celery import app

    monkeypatch.setattr(app.control, "revoke", lambda task_id, terminate=False: None)


def _export(user, **kwargs):
    defaults = {
        "name": "用户导出",
        "module": "用户",
        "path": "/api/system/user/export-async",
        "params": {"type": "xlsx"},
    }
    return ExportRecord.objects.create(creator=user, **{**defaults, **kwargs})


class TestUnifiedEndpoint:
    def test_anonymous_rejected(self, api_client):
        assert api_client.get(UNIFIED_URL).status_code == 401

    def test_list_and_filters(self, auth_client, superuser):
        _export(superuser, name="导出甲")
        _export(superuser, name="导出乙", status=ExportRecord.Status.FAILURE)
        TaskExecution.objects.create(pk="a" * 32, name="system.tasks.demo", creator=superuser)
        body = auth_client.get(UNIFIED_URL).json()["data"]
        assert body["total"] == 3 and len(body["results"]) == 3
        filtered = auth_client.get(f"{UNIFIED_URL}?type=export&status=FAILURE").json()["data"]
        assert filtered["total"] == 1 and filtered["results"][0]["name"] == "导出乙"
        assert auth_client.get(f"{UNIFIED_URL}?keyword=导出甲").json()["data"]["total"] == 1
        assert auth_client.get(f"{UNIFIED_URL}?type=task").json()["data"]["total"] == 1

    def test_owner_scope(self, normal_user):
        """无任务中心权限点的普通用户被拒；有权限者（超管口径）只看自己的记录。

        数据域本身的过滤逻辑由 tests/unit/system/test_task_center.py 覆盖，
        这里只守护「接口不向无权限用户开放」。
        """
        from system.models import UserInfo

        other = UserInfo.objects.create(username="scope-other", nickname="他人")
        _export(other)
        client = APIClient(HTTP_USER_AGENT="pytest-agent")
        client.force_authenticate(user=normal_user)
        response = client.get(UNIFIED_URL)
        assert response.status_code == 403 or response.json()["data"]["total"] == 0


class TestCancelEndpoint:
    def test_cancel_pending_export(self, auth_client, superuser):
        record = _export(superuser, status=ExportRecord.Status.PENDING)
        response = auth_client.post(f"{UNIFIED_URL}/cancel", {"type": "export", "pk": str(record.pk)}, format="json")
        assert response.json()["code"] == 1000, response.data
        record.refresh_from_db()
        assert record.status == ExportRecord.Status.REVOKED

    def test_cancel_unknown_record(self, auth_client):
        response = auth_client.post(f"{UNIFIED_URL}/cancel", {"type": "export", "pk": "x"}, format="json")
        assert response.json()["code"] == 1001


class TestRerunEndpoint:
    def test_rerun_export_creates_clone(self, auth_client, superuser, monkeypatch):
        from system.utils import task_center

        dispatched = []
        monkeypatch.setattr(
            task_center, "_dispatch", lambda task, args=None, kwargs=None, task_id=None: dispatched.append(args)
        )
        record = _export(superuser, status=ExportRecord.Status.SUCCESS)
        response = auth_client.post(f"{UNIFIED_URL}/rerun", {"type": "export", "pk": str(record.pk)}, format="json")
        assert response.json()["code"] == 1000, response.data
        clone_id = response.json()["data"]["record_id"]
        clone = ExportRecord.objects.get(pk=clone_id)
        assert clone.status == ExportRecord.Status.PENDING and clone.name.endswith("-rerun")
        assert dispatched and dispatched[0][0] == clone_id

    def test_rerun_running_record_rejected(self, auth_client, superuser):
        record = _export(superuser, status=ExportRecord.Status.RUNNING)
        response = auth_client.post(f"{UNIFIED_URL}/rerun", {"type": "export", "pk": str(record.pk)}, format="json")
        assert response.json()["code"] == 1001

    def test_rerun_task_execution_unsupported(self, auth_client, superuser):
        TaskExecution.objects.create(pk="b" * 32, name="system.tasks.demo", creator=superuser)
        response = auth_client.post(f"{UNIFIED_URL}/rerun", {"type": "task", "pk": "b" * 32}, format="json")
        assert response.json()["code"] == 1001
