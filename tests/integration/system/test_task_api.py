# -*- coding: utf-8 -*-
"""定时任务管理接口集成测试（django_celery_beat）。"""
import pytest
from django_celery_beat.models import CrontabSchedule, PeriodicTask

pytestmark = pytest.mark.django_db

TASK_URL = "/api/system/tasks/periodic"
CRONTAB_URL = "/api/system/tasks/crontab"


@pytest.fixture
def crontab_pk(auth_client):
    resp = auth_client.post(
        CRONTAB_URL, {"minute": "0", "hour": "3", "day_of_week": "*", "day_of_month": "*", "month_of_year": "*"},
        format="json",
    )
    assert resp.status_code == 200, resp.data
    assert resp.data["code"] == 1000, resp.data
    return resp.data["data"]["pk"]


def _create_task(auth_client, crontab_pk, name="清理临时文件"):
    payload = {
        "name": name,
        "task": "common.tasks.expire_caches",
        "crontab": crontab_pk,
        "enabled": True,
        "description": "测试任务",
    }
    resp = auth_client.post(TASK_URL, payload, format="json")
    assert resp.status_code == 200, resp.data
    assert resp.data["code"] == 1000, resp.data
    return resp.data["data"]["pk"]


class TestPeriodicTaskCrud:
    def test_create_list_retrieve_patch_delete(self, auth_client, crontab_pk):
        pk = _create_task(auth_client, crontab_pk)

        resp = auth_client.get(TASK_URL, {"name": "清理"})
        assert resp.status_code == 200
        assert resp.data["data"]["total"] >= 1
        assert any(r["pk"] == pk for r in resp.data["data"]["results"])

        resp = auth_client.get(f"{TASK_URL}/{pk}")
        assert resp.status_code == 200
        assert resp.data["data"]["enabled"] is True

        resp = auth_client.patch(f"{TASK_URL}/{pk}", {"description": "新描述"}, format="json")
        assert resp.status_code == 200, resp.data
        assert resp.data["data"]["description"] == "新描述"

        resp = auth_client.delete(f"{TASK_URL}/{pk}")
        assert resp.status_code == 200
        assert resp.data["code"] == 1000
        assert not PeriodicTask.objects.filter(pk=pk).exists()

    def test_enable_toggle(self, auth_client, crontab_pk):
        pk = _create_task(auth_client, crontab_pk)
        resp = auth_client.patch(f"{TASK_URL}/{pk}/enable", {"enabled": False}, format="json")
        assert resp.status_code == 200, resp.data
        assert resp.data["data"]["enabled"] is False
        assert PeriodicTask.objects.get(pk=pk).enabled is False

        # 不传 enabled 时按取反切换
        resp = auth_client.patch(f"{TASK_URL}/{pk}/enable", format="json")
        assert resp.data["data"]["enabled"] is True

    def test_filter_by_enabled(self, auth_client, crontab_pk):
        _create_task(auth_client, crontab_pk, name="任务A")
        resp = auth_client.get(TASK_URL, {"enabled": "true"})
        assert resp.data["data"]["total"] >= 1


class TestCrontabCrud:
    def test_create_list_delete(self, auth_client):
        resp = auth_client.post(
            CRONTAB_URL,
            {"minute": "*/5", "hour": "*", "day_of_week": "*", "day_of_month": "*", "month_of_year": "*"},
            format="json",
        )
        assert resp.data["code"] == 1000, resp.data
        pk = resp.data["data"]["pk"]
        assert CrontabSchedule.objects.filter(pk=pk, minute="*/5").exists()

        resp = auth_client.get(CRONTAB_URL, {"minute": "*/5"})
        assert resp.data["data"]["total"] >= 1

        resp = auth_client.delete(f"{CRONTAB_URL}/{pk}")
        assert resp.status_code == 200
        assert not CrontabSchedule.objects.filter(pk=pk).exists()
