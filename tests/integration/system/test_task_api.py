# -*- coding: utf-8 -*-
"""定时任务管理接口集成测试（django_celery_beat）。"""

import uuid

import pytest
from django_celery_beat.models import CrontabSchedule, IntervalSchedule, PeriodicTask

pytestmark = pytest.mark.django_db

TASK_URL = "/api/system/tasks/periodic"
CRONTAB_URL = "/api/system/tasks/crontab"
INTERVAL_URL = "/api/system/tasks/interval"


@pytest.fixture
def crontab_pk(auth_client):
    resp = auth_client.post(
        CRONTAB_URL,
        {"minute": "0", "hour": "3", "day_of_week": "*", "day_of_month": "*", "month_of_year": "*"},
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


class TestBatchEnable:
    def test_batch_enable_with_explicit_flag(self, auth_client, crontab_pk):
        pk1 = _create_task(auth_client, crontab_pk, name="批量启停-任务A")
        pk2 = _create_task(auth_client, crontab_pk, name="批量启停-任务B")
        PeriodicTask.objects.filter(pk__in=[pk1, pk2]).update(enabled=False)

        resp = auth_client.post(f"{TASK_URL}/batch-enable", {"pks": [pk1, pk2], "enabled": True}, format="json")
        assert resp.status_code == 200, resp.data
        assert resp.data["data"]["success"] == 2
        assert PeriodicTask.objects.get(pk=pk1).enabled is True
        assert PeriodicTask.objects.get(pk=pk2).enabled is True

    def test_batch_disable(self, auth_client, crontab_pk):
        pk1 = _create_task(auth_client, crontab_pk, name="批量停用-任务A")
        pk2 = _create_task(auth_client, crontab_pk, name="批量停用-任务B")

        resp = auth_client.post(f"{TASK_URL}/batch-enable", {"pks": [pk1, pk2], "enabled": False}, format="json")
        assert resp.status_code == 200, resp.data
        assert PeriodicTask.objects.get(pk=pk1).enabled is False
        assert PeriodicTask.objects.get(pk=pk2).enabled is False

    def test_batch_enable_toggle_without_flag(self, auth_client, crontab_pk):
        pk1 = _create_task(auth_client, crontab_pk, name="批量取反-任务A")
        PeriodicTask.objects.filter(pk=pk1).update(enabled=False)

        resp = auth_client.post(f"{TASK_URL}/batch-enable", {"pks": [pk1]}, format="json")
        assert resp.status_code == 200, resp.data
        assert PeriodicTask.objects.get(pk=pk1).enabled is True

    def test_batch_enable_ignores_unknown_pk(self, auth_client, crontab_pk):
        pk = _create_task(auth_client, crontab_pk, name="批量未知主键")
        bogus = str(uuid.uuid4())

        resp = auth_client.post(f"{TASK_URL}/batch-enable", {"pks": [pk, bogus], "enabled": False}, format="json")
        assert resp.status_code == 200, resp.data
        assert resp.data["data"]["success"] == 1
        assert PeriodicTask.objects.get(pk=pk).enabled is False


class TestClone:
    def test_clone_copies_schedule_and_disables(self, auth_client, crontab_pk):
        pk = _create_task(auth_client, crontab_pk, name="克隆源任务")
        original = PeriodicTask.objects.get(pk=pk)

        resp = auth_client.post(f"{TASK_URL}/{pk}/clone", format="json")
        assert resp.status_code == 200, resp.data
        assert resp.data["code"] == 1000, resp.data

        clone = PeriodicTask.objects.get(pk=resp.data["data"]["pk"])
        assert clone.pk != original.pk
        assert clone.name.startswith(f"{original.name}-copy")
        assert clone.enabled is False  # 克隆默认停用，避免克隆即执行
        assert clone.task == original.task
        assert clone.crontab_id == original.crontab_id  # 调度复用不复制
        assert clone.description == original.description
        assert clone.total_run_count == 0
        assert clone.last_run_at is None
        # 源任务保持原状
        original.refresh_from_db()
        assert original.enabled is True

    def test_clone_generates_unique_name(self, auth_client, crontab_pk):
        pk = _create_task(auth_client, crontab_pk, name="克隆重名任务")
        names = set()
        for _ in range(2):
            resp = auth_client.post(f"{TASK_URL}/{pk}/clone", format="json")
            assert resp.data["code"] == 1000, resp.data
            names.add(resp.data["data"]["name"])
        assert len(names) == 2
        assert PeriodicTask.objects.filter(name__in=names).count() == 2


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


class TestIntervalCrud:
    def test_create_list_patch_delete(self, auth_client):
        resp = auth_client.post(INTERVAL_URL, {"every": 15, "period": "minutes"}, format="json")
        assert resp.status_code == 200, resp.data
        assert resp.data["code"] == 1000, resp.data
        pk = resp.data["data"]["pk"]
        assert IntervalSchedule.objects.filter(pk=pk, every=15, period="minutes").exists()
        # period 为 labeled_choice：响应下发 {value,label}，列表/表单直出可读标签
        assert resp.data["data"]["period"]["value"] == "minutes"
        assert resp.data["data"]["period"]["label"]

        resp = auth_client.get(INTERVAL_URL, {"period": "minutes"})
        assert resp.data["data"]["total"] >= 1

        resp = auth_client.patch(f"{INTERVAL_URL}/{pk}", {"every": 30}, format="json")
        assert resp.data["code"] == 1000, resp.data
        assert IntervalSchedule.objects.filter(pk=pk, every=30).exists()

        resp = auth_client.delete(f"{INTERVAL_URL}/{pk}")
        assert resp.status_code == 200
        assert not IntervalSchedule.objects.filter(pk=pk).exists()

    def test_duplicate_every_period_rejected(self, auth_client):
        """业务侧唯一性校验（库 2.9 已移除 unique_together）：重复组合返回 400，避免下拉出现同名项。"""
        payload = {"every": 17, "period": "hours"}
        resp = auth_client.post(INTERVAL_URL, payload, format="json")
        assert resp.data["code"] == 1000, resp.data
        pk = resp.data["data"]["pk"]

        resp = auth_client.post(INTERVAL_URL, payload, format="json")
        assert resp.status_code == 400, resp.data

        # 编辑自身（未改动 every/period）不受唯一性校验影响
        resp = auth_client.patch(f"{INTERVAL_URL}/{pk}", {"period": "hours"}, format="json")
        assert resp.data["code"] == 1000, resp.data
        # 改成已存在的组合仍被拒
        other = auth_client.post(INTERVAL_URL, {"every": 23, "period": "hours"}, format="json")
        assert other.data["code"] == 1000, other.data
        resp = auth_client.patch(f"{INTERVAL_URL}/{pk}", {"every": 23}, format="json")
        assert resp.status_code == 400, resp.data

    def test_batch_destroy(self, auth_client):
        """批量删除入参为主键列表（与 crontab/periodic 同款端点）。"""
        pks = []
        for every in (11, 13):
            resp = auth_client.post(INTERVAL_URL, {"every": every, "period": "days"}, format="json")
            assert resp.data["code"] == 1000, resp.data
            pks.append(resp.data["data"]["pk"])

        resp = auth_client.post(f"{INTERVAL_URL}/batch-destroy", pks, format="json")
        assert resp.data["code"] == 1000, resp.data
        assert not IntervalSchedule.objects.filter(pk__in=pks).exists()


class TestPeriodicTaskIntervalSchedule:
    def test_create_task_with_interval_and_readable_label(self, auth_client):
        """间隔调度可挂到周期任务上，且关联字段带可读 label（间隔页 → 任务表单闭环）。"""
        resp = auth_client.post(INTERVAL_URL, {"every": 19, "period": "minutes"}, format="json")
        assert resp.data["code"] == 1000, resp.data
        interval_pk = resp.data["data"]["pk"]

        payload = {
            "name": f"间隔任务-{uuid.uuid4().hex[:6]}",
            "task": "common.tasks.expire_caches",
            "interval": interval_pk,
            "enabled": False,
        }
        resp = auth_client.post(TASK_URL, payload, format="json")
        assert resp.data["code"] == 1000, resp.data
        task = PeriodicTask.objects.get(pk=resp.data["data"]["pk"])
        assert task.interval_id == interval_pk
        assert task.crontab_id is None
        assert "19" in resp.data["data"]["interval"]["label"]
