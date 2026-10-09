# -*- coding: utf-8 -*-
"""周期任务配置者归属（PeriodicTaskOwner side 表）单元测试。

覆盖：创建入口的归属落行（页面创建 / 克隆 / 启动期系统注册 / 更新与重复落行
不覆盖 / 删除级联）、定时派发执行记录的 creator 回溯（publish 信号按 side 表
补归属，手动执行不覆盖触发者）、任务中心可见性与取消的归属人三态（归属人 /
管理员 / 无关用户）与序列化器 creator 字段对定时派发行产出值。
"""

import uuid

import pytest
from django_celery_beat.models import CrontabSchedule, PeriodicTask
from rest_framework.test import APIRequestFactory, force_authenticate

from common.celery.utils import create_or_update_celery_periodic_tasks
from identity.models import UserInfo
from task.models.task import PeriodicTaskOwner, TaskExecution
from task.serializers.task import TaskExecutionSerializer
from task.signal_task_execution import task_execution_on_publish
from task.utils.task_center import cancel_record, clear_cancel, is_cancel_requested, unified_rows
from task.views.task import PeriodicTaskViewSet
from task.views.task_center import SystemTaskCenterViewSet

pytestmark = pytest.mark.django_db

RUNNABLE_TASK = "system.tasks.auto_clean_operation_job"


@pytest.fixture(autouse=True)
def _no_broker(monkeypatch):
    """取消动作的 revoke 打桩（不依赖 broker），并记录调用。"""
    calls = []
    from server.celery import app

    monkeypatch.setattr(app.control, "revoke", lambda task_id, terminate=False: calls.append(task_id))
    return calls


def _make_user(username):
    return UserInfo.objects.create_superuser(username=username, password="x")


def _allow_runnable_tasks(monkeypatch, tasks):
    """放宽可手动执行白名单（默认拒绝）：页面创建入口校验的白名单。"""
    from common.core.config import SysConfig

    monkeypatch.setattr(type(SysConfig), "MANUAL_RUNNABLE_TASKS", property(lambda self: list(tasks)), raising=False)


def _make_periodic_task(name="归属测试任务"):
    crontab = CrontabSchedule.objects.create(minute="0", hour="4", day_of_week="*", day_of_month="*", month_of_year="*")
    # ORM 直建（无请求上下文）：post_save 信号落归属行，creator 为空即系统注册
    return PeriodicTask.objects.create(name=name, task=RUNNABLE_TASK, crontab=crontab, args="[]", kwargs="{}")


def _set_owner(task, user):
    """把既有（信号落行、creator 为空）的归属行指给指定用户，模拟页面配置入口的落行结果。"""
    PeriodicTaskOwner.objects.filter(periodic_task=task).update(creator=user)


def _dispatch_row(task, status=TaskExecution.Status.PENDING):
    """模拟 beat 派发：publish 信号按 side 表回溯 creator 建执行记录。"""
    task_id = str(uuid.uuid4())
    task_execution_on_publish(
        headers={"id": task_id, "task": task.task, "periodic_task_name": task.name},
        body=([], {}),
    )
    execution = TaskExecution.objects.get(pk=task_id)
    execution.status = status
    execution.save(update_fields=["status"])
    return execution


def _view_request(user, method, path, data=None):
    factory = APIRequestFactory()
    if data is None:
        request = getattr(factory, method)(path)
    else:
        request = getattr(factory, method)(path, data=data, format="json")
    force_authenticate(request, user=user)
    return request


# --------------------------------------------------------------- 归属落行


class TestOwnerRecording:
    def test_create_via_view_records_configurator(self, monkeypatch):
        """页面创建入口：归属记录 creator 为操作者（经审计信号回填请求用户）。"""
        _allow_runnable_tasks(monkeypatch, [RUNNABLE_TASK])
        user = _make_user("creator-a")
        crontab = CrontabSchedule.objects.create(
            minute="0", hour="4", day_of_week="*", day_of_month="*", month_of_year="*"
        )
        request = _view_request(
            user,
            "post",
            "/api/task/periodic",
            data={"name": "页面新建任务", "task": RUNNABLE_TASK, "crontab": crontab.pk, "args": "[]", "kwargs": "{}"},
        )
        view = PeriodicTaskViewSet.as_view({"post": "create"})
        response = view(request)
        assert response.data["code"] == 1000, response.data
        owner = PeriodicTaskOwner.objects.get(periodic_task_id=response.data["data"]["pk"])
        assert owner.creator == user

    def test_clone_records_cloner_as_owner(self):
        """克隆视为新建：克隆任务归属克隆操作者，源任务归属不变。"""
        cloner = _make_user("cloner-b")
        source = _make_periodic_task("克隆归属源任务")
        request = _view_request(cloner, "post", f"/api/task/periodic/{source.pk}/clone")
        view = PeriodicTaskViewSet.as_view({"post": "clone"})
        response = view(request, pk=str(source.pk))
        assert response.data["code"] == 1000, response.data
        clone = PeriodicTask.objects.get(pk=response.data["data"]["pk"])
        assert PeriodicTaskOwner.objects.get(periodic_task=clone).creator == cloner
        assert PeriodicTaskOwner.objects.get(periodic_task=source).creator is None

    def test_system_registration_records_system_owner(self):
        """启动期系统注册无请求上下文：归属 creator 为空即系统注册；重放（更新路径）不新增行。"""
        spec = {"owner-sys-task": {"task": RUNNABLE_TASK, "interval": 60}}
        create_or_update_celery_periodic_tasks(spec)
        owner = PeriodicTaskOwner.objects.get(periodic_task__name="owner-sys-task")
        assert owner.creator is None
        create_or_update_celery_periodic_tasks(spec)
        assert PeriodicTaskOwner.objects.filter(periodic_task__name="owner-sys-task").count() == 1

    def test_record_for_keeps_first_owner(self):
        """幂等口径：重复落行保留首个归属，不随后续操作者漂移。"""
        user_a = _make_user("first-owner")
        user_b = _make_user("later-owner")
        task = _make_periodic_task("幂等归属任务")
        _set_owner(task, user_a)
        PeriodicTaskOwner.record_for(task, creator=user_b)
        assert PeriodicTaskOwner.objects.get(periodic_task=task).creator == user_a

    def test_update_keeps_owner(self, superuser):
        """更新配置不改归属（含他人编辑）：post_save 仅在新建时落行。"""
        creator = _make_user("update-owner")
        task = _make_periodic_task("更新归属任务")
        _set_owner(task, creator)
        request = _view_request(superuser, "patch", f"/api/task/periodic/{task.pk}", data={"description": "改描述"})
        view = PeriodicTaskViewSet.as_view({"patch": "partial_update"})
        response = view(request, pk=str(task.pk))
        assert response.data["code"] == 1000, response.data
        assert PeriodicTaskOwner.objects.get(periodic_task=task).creator == creator

    def test_delete_task_cascades_owner(self):
        """任务删除（含孤儿清理路径的同款 delete）时归属随 OneToOne CASCADE 清除。"""
        task = _make_periodic_task("级联归属任务")
        assert PeriodicTaskOwner.objects.filter(periodic_task=task).exists()
        task.delete()
        assert PeriodicTaskOwner.objects.count() == 0


# --------------------------------------------------------------- 派发回溯


class TestDispatchCreatorAttribution:
    def test_publish_attributes_dispatch_to_owner(self):
        """定时派发（beat 投递，无请求上下文）：执行记录 creator 回溯到配置者。"""
        owner_user = _make_user("dispatch-owner")
        task = _make_periodic_task("派发归属任务")
        _set_owner(task, owner_user)
        task_execution_on_publish(
            headers={"id": str(uuid.uuid4()), "task": task.task, "periodic_task_name": task.name},
            body=([], {}),
        )
        execution = TaskExecution.objects.get(periodic_task=task)
        assert execution.creator == owner_user
        assert execution.status == TaskExecution.Status.PENDING

    def test_publish_without_owner_stays_system(self):
        """未登记归属（存量任务/系统注册）：creator 保持为空，即系统调度。"""
        task = _make_periodic_task("无归属派发任务")
        PeriodicTaskOwner.objects.filter(periodic_task=task).delete()
        task_execution_on_publish(
            headers={"id": str(uuid.uuid4()), "task": task.task, "periodic_task_name": task.name},
            body=([], {}),
        )
        execution = TaskExecution.objects.get(periodic_task=task)
        assert execution.creator is None

    def test_publish_keeps_manual_runner_over_owner(self):
        """手动立即执行：记录投递前已创建（creator 为触发者），publish 不覆盖为配置者。"""
        runner = _make_user("run-runner")
        task = _make_periodic_task("手动执行归属任务")
        execution = TaskExecution.objects.create(name=task.task, periodic_task=task, creator=runner)
        task_execution_on_publish(
            headers={"id": str(execution.pk), "task": task.task, "periodic_task_name": task.name},
            body=([], {}),
        )
        execution.refresh_from_db()
        assert execution.creator == runner

    def test_execution_serializer_outputs_owner_for_dispatch_row(self):
        """既有 serializer 的 creator 字段对定时派发行产出配置者（契约零变化）。"""
        owner_user = _make_user("serializer-owner")
        task = _make_periodic_task("序列化归属任务")
        _set_owner(task, owner_user)
        task_execution_on_publish(
            headers={"id": str(uuid.uuid4()), "task": task.task, "periodic_task_name": task.name},
            body=([], {}),
        )
        execution = TaskExecution.objects.get(periodic_task=task)
        data = TaskExecutionSerializer(execution).data
        assert data["creator"]["pk"] == owner_user.pk
        assert owner_user.username in data["creator"]["label"]


# --------------------------------------------------------------- 可见与取消


class TestOwnerScopeAndCancel:
    def test_owner_visible_and_can_cancel(self, normal_user):
        """归属人：任务中心可见定时派发行，且可取消（PENDING 立即终态）。"""
        task = _make_periodic_task("归属人取消任务")
        _set_owner(task, normal_user)
        execution = _dispatch_row(task)
        rows, total = unified_rows(normal_user, types=["task"])
        assert total == 1
        assert rows[0]["pk"] == str(execution.pk)
        assert rows[0]["creator"] == normal_user.username
        result = cancel_record(normal_user, "task", str(execution.pk))
        execution.refresh_from_db()
        assert result["ok"] is True
        assert execution.status == TaskExecution.Status.REVOKED

    def test_superuser_can_cancel_dispatch_row(self, superuser):
        """管理员：无归属（系统调度）的派发行管理员可取消（数据域超管口径）。"""
        task = _make_periodic_task("管理员取消任务")
        execution = _dispatch_row(task)
        result = cancel_record(superuser, "task", str(execution.pk))
        assert result["ok"] is True
        execution.refresh_from_db()
        assert execution.status == TaskExecution.Status.REVOKED

    def test_unrelated_user_cannot_cancel(self, normal_user):
        """无关用户：非本人归属的行不可达，取消被拒（与交互式执行记录同口径）。"""
        owner_user = _make_user("row-owner")
        task = _make_periodic_task("无关用户取消任务")
        _set_owner(task, owner_user)
        execution = _dispatch_row(task)
        assert execution.creator == owner_user
        other = UserInfo.objects.create_user(username="cancel-other", password="x")
        result = cancel_record(other, "task", str(execution.pk))
        assert result["ok"] is False
        execution.refresh_from_db()
        assert execution.status == TaskExecution.Status.PENDING
        assert unified_rows(other, types=["task"])[1] == 0

    def test_running_dispatch_row_cooperative_stop_by_owner(self, normal_user):
        """归属人取消运行中派发行：走协作式停止（revoke + 安全点收敛）。"""
        task = _make_periodic_task("运行中取消任务")
        _set_owner(task, normal_user)
        execution = _dispatch_row(task, status=TaskExecution.Status.RUNNING)
        result = cancel_record(normal_user, "task", str(execution.pk))
        assert result["ok"] is True
        assert is_cancel_requested(execution.pk) is True
        execution.refresh_from_db()
        assert execution.status == TaskExecution.Status.RUNNING
        clear_cancel(execution.pk)


class TestOwnerScopeViaView:
    """视图级端到端：记账信号回溯归属后，任务中心 HTTP 接口零改动即按归属人圈域。

    链路 = publish 信号建行（side 表回溯 creator）→ SystemTaskCenterViewSet
    list / cancel——视图层不做任何归属特判，依赖 _owner_filter 的 creator 过滤。
    普通用户需先持有任务中心两个权限点（菜单权限口径），数据域差异只来自归属。
    """

    @staticmethod
    def _grant_task_center_menus(role, menu_factory):
        role.menu.add(menu_factory("task-center-list", path="api/task/unified$", method="GET"))
        role.menu.add(menu_factory("task-center-cancel", path="api/task/unified/cancel$", method="POST"))

    def _list_view(self, user, query=""):
        request = _view_request(user, "get", f"/api/task/unified{query}")
        return SystemTaskCenterViewSet.as_view({"get": "list"})(request)

    def _cancel_view(self, user, execution):
        request = _view_request(
            user, "post", "/api/task/unified/cancel", data={"type": "task", "pk": str(execution.pk)}
        )
        return SystemTaskCenterViewSet.as_view({"post": "cancel"})(request)

    def test_owner_sees_and_cancels_dispatch_row_through_view(self, normal_user, role, menu_factory):
        """归属人经任务中心接口可见定时派发行并取消（PENDING → REVOKED）。"""
        self._grant_task_center_menus(role, menu_factory)
        task = _make_periodic_task("视图归属人任务")
        _set_owner(task, normal_user)
        execution = _dispatch_row(task)
        response = self._list_view(normal_user, "?type=task")
        assert response.data["code"] == 1000
        assert response.data["data"]["total"] == 1
        row = response.data["data"]["results"][0]
        assert row["pk"] == str(execution.pk)
        assert row["creator"] == normal_user.username
        response = self._cancel_view(normal_user, execution)
        assert response.data["code"] == 1000, response.data
        execution.refresh_from_db()
        assert execution.status == TaskExecution.Status.REVOKED

    def test_menu_authorized_user_cannot_see_or_cancel_others_row(self, normal_user, role, menu_factory):
        """同持任务中心权限点的无关用户：列表为空（数据域只差在归属），取消动作拒绝。"""
        self._grant_task_center_menus(role, menu_factory)
        owner_user = _make_user("view-row-owner")
        task = _make_periodic_task("视图无关用户任务")
        _set_owner(task, owner_user)
        execution = _dispatch_row(task)
        other = UserInfo.objects.create_user(username="view-other", password="x")
        other.roles.add(role)
        response = self._list_view(other, "?type=task")
        assert response.data["code"] == 1000
        assert response.data["data"]["total"] == 0
        response = self._cancel_view(other, execution)
        assert response.data["code"] != 1000
        execution.refresh_from_db()
        assert execution.status == TaskExecution.Status.PENDING

    def test_superuser_cancels_system_dispatch_row_through_view(self, superuser):
        """管理员经接口取消无归属（系统调度）的派发行。"""
        task = _make_periodic_task("视图管理员任务")
        execution = _dispatch_row(task)
        response = self._cancel_view(superuser, execution)
        assert response.data["code"] == 1000, response.data
        execution.refresh_from_db()
        assert execution.status == TaskExecution.Status.REVOKED
