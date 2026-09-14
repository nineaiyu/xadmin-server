# -*- coding: utf-8 -*-
"""common/tasks.py 周期/异步任务回归。

celery eager 模式下 `.delay()` 同步执行，任务体当普通函数驱动：
- 邮件任务用 mock send_mail 捕获参数 + locmem 真发各验一层；
- 后台批量视图任务（导入/批量删除聚合）以模块内假 ViewSet 驱动真实聚合逻辑，
  消息类 monkeypatch 成记录器（WSGIRequest 天然无 user，由假视图回填，
  与生产「认证后视图内已具备 user」口径一致）。
"""

import json
from datetime import timedelta
from types import SimpleNamespace

import pytest
from celery import Task
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.viewsets import ViewSet

import common.tasks as ct
from common.cache.redis import CacheList
from common.tasks import (
    background_task_view_set_job,
    check_server_performance_period,
    clean_celery_periodic_tasks,
    create_or_update_registered_periodic_tasks,
    purge_soft_deleted,
    send_mail_async,
    send_mail_attachment_async,
)

# --------------------------------------------------------------------------- 邮件


class TestSendMailAsync:
    def test_three_args_inserts_from_email(self, monkeypatch, settings):
        settings.EMAIL_SUBJECT_PREFIX = "[xadmin]"
        settings.EMAIL_FROM = "from@x.com"
        captured = {}
        monkeypatch.setattr(ct, "send_mail", lambda *a, **kw: captured.update(a=a, kw=kw) or 1)

        assert send_mail_async("标题", "正文", ["a@b.c"]) == 1
        assert captured["a"][0] == "[xadmin] 标题"
        assert captured["a"][1] == "正文"
        assert captured["a"][2] == "from@x.com"
        assert captured["a"][3] == ["a@b.c"]

    def test_four_args_keeps_from_email(self, monkeypatch, settings):
        captured = {}
        monkeypatch.setattr(ct, "send_mail", lambda *a, **kw: captured.update(a=a) or 1)
        send_mail_async("s", "m", "from@x.com", ["a@b.c"])
        assert captured["a"][2] == "from@x.com"

    def test_kwargs_form_and_real_locmem_send(self, settings):
        settings.EMAIL_SUBJECT_PREFIX = ""
        settings.EMAIL_FROM = "from@x.com"
        from django.core import mail

        # kwargs 形态不注入 from_email，必须自带（缺 from 会被吞异常静默失败）
        assert (
            send_mail_async(subject="kw 标题", message="kw 正文", from_email="from@x.com", recipient_list=["a@b.c"])
            == 1
        )
        assert len(mail.outbox) == 1
        assert mail.outbox[0].to == ["a@b.c"]

    def test_send_failure_swallowed(self, monkeypatch):
        def boom(*a, **kw):
            raise RuntimeError("smtp down")

        monkeypatch.setattr(ct, "send_mail", boom)
        assert send_mail_async("s", "m", ["a@b.c"]) is None

    def test_task_path_would_retry_with_backoff(self, monkeypatch):
        """任务路径（非直接调用）失败追加一次退避重试；同步直接调用不触发（见上一条）。"""

        class FakeTask(Task):
            # 类属性覆盖 Task.request property，模拟 worker 执行上下文
            request = SimpleNamespace(retries=0, called_directly=False)

            def __init__(self):
                self.retry_msgs = []

            def retry(self, exc=None, countdown=None):
                self.retry_msgs.append(countdown)
                raise RuntimeError(f"RETRY:{countdown}")

        fake = FakeTask()
        monkeypatch.setattr(ct, "send_mail", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("smtp down")))

        # 取原始未绑定函数体，手动以 fake 作 bind 首参，等价 worker 执行路径
        run = send_mail_async.run.__func__
        with pytest.raises(RuntimeError, match="RETRY:60"):
            run(fake, "s", "m", ["a@b.c"])
        assert fake.retry_msgs == [60]

        # 重试耗尽：记录错误后返回 None（不外抛）
        class ExhaustedTask(FakeTask):
            request = SimpleNamespace(retries=ct.MAIL_MAX_RETRIES, called_directly=False)

        exhausted = ExhaustedTask()
        assert run(exhausted, "s", "m", ["a@b.c"]) is None
        assert exhausted.retry_msgs == []

    def test_mail_tasks_declare_acks_late(self):
        """P3 守护：邮件任务开启 acks_late，worker 崩溃时任务不丢。"""
        assert send_mail_async.acks_late is True
        assert send_mail_attachment_async.acks_late is True
        assert ct.MAIL_MAX_RETRIES >= 1

    def test_strip_task_self_only_removes_task_instance(self):
        class FakeTask(Task):
            pass

        fake = FakeTask()
        task_self, rest = ct._strip_task_self((fake, "s", "m"))
        assert task_self is fake
        assert rest == ("s", "m")
        # 普通业务参数原样保留，不会误剥离
        task_self, rest = ct._strip_task_self(("s", "m"))
        assert task_self is None
        assert rest == ("s", "m")


class TestSendMailAttachmentAsync:
    def test_send_with_attachment_removed_after(self, settings, tmp_path):
        settings.EMAIL_SUBJECT_PREFIX = "[x]"
        settings.EMAIL_FROM = "from@x.com"
        from django.core import mail

        attachment = tmp_path / "report.csv"
        attachment.write_text("a,b\n1,2", encoding="utf-8")

        assert send_mail_attachment_async("带附件", "正文", ["a@b.c"], [str(attachment)]) == 1
        assert len(mail.outbox) == 1
        name, content, _mimetype = mail.outbox[0].attachments[0]
        assert name == "report.csv"
        text = content if isinstance(content, str) else content.decode()
        assert "a,b" in text
        assert not attachment.exists()  # 发送后清理临时文件

    def test_send_failure_swallowed(self, settings, monkeypatch):
        settings.EMAIL_FROM = "from@x.com"

        class BadEmail:
            def __init__(self, **kw):
                pass

            def attach_file(self, path):
                pass

            def send(self):
                raise RuntimeError("smtp down")

        monkeypatch.setattr(ct, "EmailMultiAlternatives", BadEmail)
        assert send_mail_attachment_async("s", "m", ["a@b.c"]) is None

    def test_failed_send_keeps_attachment_for_retry(self, settings, monkeypatch, tmp_path):
        """修复守护：附件仅在发送成功后删除，失败时保留（重试/人工重发仍可用）。"""
        settings.EMAIL_FROM = "from@x.com"
        attachment = tmp_path / "keep.csv"
        attachment.write_text("a,b\n1,2", encoding="utf-8")

        class BadEmail:
            def __init__(self, **kw):
                pass

            def attach_file(self, path):
                pass

            def send(self):
                raise RuntimeError("smtp down")

        monkeypatch.setattr(ct, "EmailMultiAlternatives", BadEmail)
        assert send_mail_attachment_async("s", "m", ["a@b.c"], [str(attachment)]) is None
        assert attachment.exists()


# --------------------------------------------------------------------------- 周期任务


@pytest.mark.django_db
class TestPeriodicMaintenance:
    def test_clean_celery_periodic_tasks(self, db):
        from django_celery_beat.models import IntervalSchedule, PeriodicTask

        interval = IntervalSchedule.objects.create(every=1, period=IntervalSchedule.SECONDS)
        ghost = PeriodicTask.objects.create(name="ghost", task="no.such.task", interval=interval)
        alive = PeriodicTask.objects.create(
            name="alive", task="common.tasks.auto_clean_monitor_logs", interval=interval
        )

        clean_celery_periodic_tasks()

        assert not PeriodicTask.objects.filter(pk=ghost.pk).exists()
        assert PeriodicTask.objects.filter(pk=alive.pk).exists()

    def test_create_or_update_registered_periodic_tasks(self, db):
        from django_celery_beat.models import PeriodicTask

        from common.celery.decorator import get_register_period_tasks

        # 注册表元素为 {任务名: {task/interval/...}} 形态
        registered = {name for entry in get_register_period_tasks() for name in entry}
        assert registered

        create_or_update_registered_periodic_tasks()

        existing = set(PeriodicTask.objects.values_list("task", flat=True))
        assert registered <= existing

    def test_check_server_performance_period(self, monkeypatch):
        calls = []

        class FakeUtil:
            def check_and_publish(self):
                calls.append(1)

        monkeypatch.setattr(ct, "ServerPerformanceCheckUtil", FakeUtil)
        check_server_performance_period()
        assert calls == [1]


# --------------------------------------------------------------------------- 批量视图任务


def _build_meta(task_id="job_0", index=0, count=1, action="noop"):
    return {
        "task_id": task_id,
        "task_index": index,
        "task_count": count,
        "action": action,
        "REQUEST_METHOD": "POST",
        "PATH_INFO": "/demo/",
    }


class EchoViewSet(ViewSet):
    """批量任务的目标视图替身：回填 user（生产链路由认证层完成）并返回协议响应。"""

    # 单测里没有认证层：项目全局默认要求登录态，替身视图显式置空
    # （throttle 首请求会读 SysConfig 配置，置空后本任务链路不依赖 DB）
    authentication_classes: list = []
    permission_classes: list = []
    throttle_classes: list = []

    def create(self, request, *args, task=False, **kwargs):
        request._request.user = SimpleNamespace(pk=1, username="operator")
        return Response({"code": 1000, "detail": "操作成功"})


EchoViewSet.__doc__ = "批量操作演示视图\n第二行"


class FailViewSet(ViewSet):
    authentication_classes: list = []
    permission_classes: list = []
    throttle_classes: list = []

    def create(self, request, *args, task=False, **kwargs):
        request._request.user = SimpleNamespace(pk=1, username="operator")
        return Response({"code": 2000, "detail": "部分行失败"})


class BoomViewSet(ViewSet):
    authentication_classes: list = []
    permission_classes: list = []
    throttle_classes: list = []

    def create(self, request, *args, task=False, **kwargs):
        request._request.user = SimpleNamespace(pk=1, username="operator")
        raise RuntimeError("boom")


@pytest.fixture()
def clean_view_task_cache():
    yield
    CacheList("view_task_job", timeout=3600 * 24).delete()


class TestBackgroundTaskViewSetJob:
    def test_single_task_aggregates_and_publishes(self, monkeypatch, clean_view_task_cache):
        published = []

        class FakeMessage:
            def __init__(self, user, task_info):
                published.append((user, task_info))

            def publish(self):
                published.append("publish")

        monkeypatch.setattr(ct, "ImportDataMessage", FakeMessage)

        info = background_task_view_set_job(
            view=f"{__name__}.EchoViewSet",
            meta=_build_meta(count=1, action="import_data"),
            data=json.dumps({"rows": []}),
            action_map={"post": "create"},
        )

        assert info["state"] is True
        assert "成功" in str(info["status"])  # 聚合后 status 为文案（state 才是布尔）
        assert info["task_name"].endswith("EchoViewSet")
        assert info["view_doc"].startswith("批量操作演示视图")
        assert len(info["tasks"]) == 1
        # 消息以视图内回填的操作者身份发布
        assert published[0][0].username == "operator"
        assert published[-1] == "publish"

    def test_batch_destroy_arm(self, monkeypatch, clean_view_task_cache):
        published = []

        class FakeMessage:
            def __init__(self, user, task_info):
                published.append(user)

            def publish(self):
                pass

        monkeypatch.setattr(ct, "BatchDeleteDataMessage", FakeMessage)
        info = background_task_view_set_job(
            view=f"{__name__}.EchoViewSet",
            meta=_build_meta(count=1, action="batch_destroy"),
            data="[]",
            action_map={"post": "create"},
        )
        assert info["state"] is True
        assert published and published[0].username == "operator"

    def test_failed_view_marks_state_false(self, clean_view_task_cache):
        info = background_task_view_set_job(
            view=f"{__name__}.FailViewSet",
            meta=_build_meta(task_id="jobf_0", index=0, count=1, action="noop"),
            data="[]",
            action_map={"post": "create"},
        )
        assert info["state"] is False
        assert "失败" in str(info["status"])
        assert info["tasks"][0]["result"] == "部分行失败"
        CacheList("view_task_jobf", timeout=3600 * 24).delete()

    @pytest.mark.django_db
    def test_lazy_detail_does_not_break_push(self, clean_view_task_cache):
        """视图内部 500 时 detail 为 gettext 惰性代理：物化后才能进 cache.push。

        回归位：未物化时 json.dumps 抛 TypeError，整批分片结果丢失。
        """
        info = background_task_view_set_job(
            view=f"{__name__}.BoomViewSet",
            meta=_build_meta(task_id="jobb_0", index=0, count=1, action="noop"),
            data="[]",
            action_map={"post": "create"},
        )
        assert info["state"] is False
        # 500 兜底文案已物化为 str（未物化时上面 cache.push 就会崩，聚合根本走不到）
        assert info["tasks"][0]["result"]
        CacheList("view_task_jobb", timeout=3600 * 24).delete()

    def test_partial_tasks_wait_for_rest(self, clean_view_task_cache):
        info = background_task_view_set_job(
            view=f"{__name__}.EchoViewSet",
            meta=_build_meta(index=0, count=2, action="noop"),
            data="{}",
            action_map={"post": "create"},
        )
        # 未凑齐分片：只登记本片结果，不聚合、不发消息
        assert info["status"] is True
        assert "state" not in info
        assert CacheList("view_task_job", timeout=3600 * 24).len() == 1


# --------------------------------------------------------------------------- 回收站清理


@pytest.mark.django_db
class TestPurgeSoftDeleted:
    def test_purge_expired_user_only(self, db, django_user_model):
        keep = django_user_model.objects.create_user(username="purge_keep")
        gone = django_user_model.objects.create_user(username="purge_gone")
        gone.deleted_at = timezone.now() - timedelta(days=31)
        gone.save(update_fields=["deleted_at"])

        total = purge_soft_deleted()

        assert total >= 1
        assert django_user_model.objects.filter(pk=keep.pk).exists()
        assert not django_user_model.all_objects.filter(pk=gone.pk).exists()
