# -*- coding: utf-8 -*-
"""大屏与定时报表集成测试。

覆盖：Screen/Report CRUD 与越权、报表调度命中（daily/weekly/monthly × 时刻）、
执行（创建者权限上下文 fail-closed、xlsx 产物进下载中心、邮件降级）、
run 动作派发契约（ExportRecord.pk == task_id）。
"""

from datetime import datetime

import pytest
from django.core import mail
from django.utils import timezone
from django.utils.translation import gettext as _

from dataset.models.dataset import Dashboard, Dataset, Report, Screen
from dataset.serializers.report import IM_NOTIFY_CHANNELS, REPORT_NOTIFY_CHANNELS
from identity.models import UserInfo
from system.models import ModelLabelField

pytestmark = pytest.mark.django_db

SCREEN_URL = "/api/dataset/screens"
REPORT_URL = "/api/dataset/reports"


@pytest.fixture
def model_registry(db):
    root, _ = ModelLabelField.objects.get_or_create(
        name="identity.userinfo", defaults={"field_type": ModelLabelField.FieldChoices.DATA, "label": "用户"}
    )
    for name in ("username", "nickname"):
        ModelLabelField.objects.get_or_create(
            name=name, parent=root, defaults={"field_type": ModelLabelField.FieldChoices.DATA, "label": name}
        )
    return root


@pytest.fixture
def dataset(model_registry, superuser):
    return Dataset.objects.create(
        name="用户清单",
        bound_model="identity.userinfo",
        columns=["username", "nickname"],
        visibility="shared",
        creator=superuser,
    )


@pytest.fixture
def screen_urls():
    from system.models import Menu, MenuMeta

    def _make(name, path, method):
        meta = MenuMeta.objects.create(title=name)
        return Menu.objects.create(
            name=name, path=path, method=method, menu_type=Menu.MenuChoices.PERMISSION, meta=meta
        )

    detail = "api/dataset/screens/(?P<pk>[^/.]+)"
    report_detail = "api/dataset/reports/(?P<pk>[^/.]+)"
    menus = [
        _make("list:Screen", "api/dataset/screens$", "GET"),
        _make("create:Screen", "api/dataset/screens$", "POST"),
        _make("partialUpdate:Screen", detail + "$", "PATCH"),
        _make("list:Report", "api/dataset/reports$", "GET"),
        _make("create:Report", "api/dataset/reports$", "POST"),
        _make("partialUpdate:Report", report_detail + "$", "PATCH"),
        _make("run:Report", report_detail + "/run$", "POST"),
    ]
    return menus


def grant(user, menus):
    from identity.models import UserRole

    role = UserRole.objects.create(name=f"role-{user.username}", code=user.username)
    user.roles.add(role)
    role.menu.set(menus)


class TestScreen:
    def test_anonymous_rejected(self, api_client):
        assert api_client.get(SCREEN_URL).status_code == 401

    def test_crud_and_visibility(self, auth_client, normal_user, dashboard_factory, screen_urls):
        dashboard_factory()
        payload = {
            "name": "值班大屏",
            "dashboards": [str(Dashboard.objects.first().pk)],
            "interval": 20,
            "refresh": 60,
            "visibility": "shared",
        }
        response = auth_client.post(SCREEN_URL, payload, format="json")
        assert response.status_code == 200, response.data

        # personal 仅创建者可见；other 无菜单 403，授菜单后可见性生效
        mine = Screen.objects.create(name="私人屏", creator=normal_user)
        other = _second_user(screen_urls)
        visible = {item["name"] for item in other["client"].get(SCREEN_URL).json()["data"]["results"]}
        assert "值班大屏" in visible and "私人屏" not in visible

        # 共享只读
        response = other["client"].patch(
            f"{SCREEN_URL}/{Screen.objects.get(name='值班大屏').pk}", {"name": "改"}, format="json"
        )
        assert response.json()["code"] == 1003
        assert mine.pk  # noqa: B015

    def test_interval_bounds(self, auth_client, dashboard_factory):
        dashboard_factory()
        response = auth_client.post(
            SCREEN_URL,
            {"name": "坏间隔", "dashboards": [str(Dashboard.objects.first().pk)], "interval": 1},
            format="json",
        )
        assert response.status_code == 400

    def test_unknown_dashboard_rejected(self, auth_client):
        response = auth_client.post(
            SCREEN_URL, {"name": "坏引用", "dashboards": ["00000000-0000-0000-0000-000000000000"]}, format="json"
        )
        assert response.status_code == 400

    def test_list_exposes_render_mode(self, auth_client, superuser, dashboard_factory):
        """列表派生 carousel / canvas-grid / canvas-free，与投屏端窗格判定同源。"""
        dashboard = dashboard_factory()
        pane = {"id": "p1", "dashboard": str(dashboard.pk)}
        Screen.objects.create(name="轮播屏", dashboards=[str(dashboard.pk)], creator=superuser)
        Screen.objects.create(name="栅格屏", dashboards=[str(dashboard.pk)], layout=[pane], creator=superuser)
        Screen.objects.create(
            name="自由屏",
            dashboards=[str(dashboard.pk)],
            layout_mode=Screen.LayoutMode.FREE,
            free_layout={"panes": [pane]},
            creator=superuser,
        )

        rows = {item["name"]: item["render_mode"] for item in auth_client.get(SCREEN_URL).json()["data"]["results"]}
        assert rows == {"轮播屏": "carousel", "栅格屏": "canvas-grid", "自由屏": "canvas-free"}


class TestScreenMetricDatasetVisibility:
    """大屏指标卡引用的数据集必须落在当前用户可见域（shared 或本人创建）。

    与仪表盘引用同口径：存在但不可见（他人的 personal 数据集）按未知引用拒绝，
    避免低权用户把他人数据集挂进自己的大屏。
    """

    @staticmethod
    def _payload(dataset, name="指标大屏"):
        return {
            "name": name,
            "dashboards": [],
            "layout": [
                {
                    "pk": "m1",
                    "type": "metric",
                    "x": 0,
                    "y": 0,
                    "w": 3,
                    "h": 2,
                    "dataset": str(dataset.pk),
                    "metric": "count",
                }
            ],
            "interval": 20,
            "refresh": 60,
            "visibility": "shared",
        }

    def test_other_personal_dataset_rejected(self, screen_urls, superuser):
        other = _second_user(screen_urls)
        personal = Dataset.objects.create(
            name="他人私有集",
            bound_model="identity.userinfo",
            columns=["username"],
            visibility=Dataset.Visibility.PERSONAL,
            creator=superuser,
        )

        response = other["client"].post(SCREEN_URL, self._payload(personal), format="json")

        assert response.status_code == 400, response.data
        assert not Screen.objects.filter(name="指标大屏").exists()

    def test_shared_dataset_allowed(self, screen_urls, dataset):
        other = _second_user(screen_urls)

        response = other["client"].post(SCREEN_URL, self._payload(dataset), format="json")

        assert response.status_code == 200, response.data
        assert Screen.objects.filter(name="指标大屏").exists()


class TestReportSchedule:
    """到期判定：以「最近一次应当执行的时刻」为基准（含漏跑补偿）。"""

    def test_daily_match(self, dataset, superuser):
        report = _make_report(dataset, superuser, frequency="daily", send_time="08:00")
        due = timezone.localtime().replace(hour=8, minute=0)
        from dataset.analysis_tasks import report_due
        from dataset.models.dataset import Report

        # 建单先于命中时刻 → 命中后到期（可在命中后的任意扫描时刻补跑）
        Report.objects.filter(pk=report.pk).update(created_time=due - timezone.timedelta(days=1))
        report.refresh_from_db()
        assert report_due(report, due) is True
        assert report_due(report, due.replace(hour=9)) is True

        # 已经跑过本次命中 → 不再重复派发
        Report.objects.filter(pk=report.pk).update(last_run_at=due)
        report.refresh_from_db()
        assert report_due(report, due.replace(hour=9)) is False
        # 次日同一时刻再次到期
        assert report_due(report, due + timezone.timedelta(days=1)) is True

    def test_weekday_match(self, dataset, superuser):
        report = _make_report(dataset, superuser, frequency="weekly", send_time="08:00", weekday=0)
        from dataset.analysis_tasks import report_due
        from dataset.models.dataset import Report

        monday = timezone.localtime().replace(hour=8, minute=0)
        while monday.weekday() != 0:
            monday = monday + timezone.timedelta(days=1)
        Report.objects.filter(pk=report.pk).update(created_time=monday - timezone.timedelta(days=7))
        report.refresh_from_db()

        assert report_due(report, monday) is True
        Report.objects.filter(pk=report.pk).update(last_run_at=monday)
        report.refresh_from_db()
        tuesday = monday + timezone.timedelta(days=1)
        assert report_due(report, tuesday) is False

    def test_monthly_first_day(self, dataset, superuser):
        report = _make_report(dataset, superuser, frequency="monthly", send_time="08:00")
        from dataset.analysis_tasks import report_due
        from dataset.models.dataset import Report

        first = timezone.localtime().replace(day=1, hour=8, minute=0)
        Report.objects.filter(pk=report.pk).update(created_time=first - timezone.timedelta(days=31))
        report.refresh_from_db()

        assert report_due(report, first) is True
        Report.objects.filter(pk=report.pk).update(last_run_at=first)
        report.refresh_from_db()
        assert report_due(report, first.replace(day=2)) is False

    def test_recipients_email_validated(self, auth_client, dataset):
        payload = {"name": "坏邮箱", "dataset": str(dataset.pk), "recipients": ["not-an-email"]}
        response = auth_client.post(REPORT_URL, payload, format="json")
        assert response.status_code == 400

    def test_binding_shared_dataset_does_not_require_dataset_data_permission(
        self, api_client, normal_user, dataset, screen_urls
    ):
        """能看到 shared 数据集即可绑定；行级数据权限在执行期 fail-closed，不在保存期拦截。"""
        grant(normal_user, screen_urls)
        api_client.force_authenticate(user=normal_user)
        payload = {
            "name": "无数据集授权也能建报表",
            "dataset": str(dataset.pk),
            "recipients": ["a@corp.com"],
        }
        response = api_client.post(REPORT_URL, payload, format="json")
        assert response.status_code == 200, response.data
        assert Report.objects.filter(name=payload["name"]).exists()

    def test_aggregate_requires_group_by(self, auth_client, dataset):
        payload = {"name": "缺分组", "dataset": str(dataset.pk), "mode": "aggregate", "recipients": ["a@corp.com"]}
        response = auth_client.post(REPORT_URL, payload, format="json")
        assert response.status_code == 400


class TestReportRun:
    def test_run_creates_record_and_email(self, auth_client, dataset, superuser, settings):
        """run 动作：预创建 ExportRecord（下载中心）→ 任务执行 → 邮件附件。"""
        settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
        report = _make_report(dataset, superuser, recipients=["boss@corp.com"])
        response = auth_client.post(f"{REPORT_URL}/{report.pk}/run", {}, format="json")
        assert response.status_code == 200, response.data
        task_id = response.json()["data"]["task_id"]

        from task.models.export import ExportRecord

        record = ExportRecord.objects.get(pk=task_id)
        assert record.status == ExportRecord.Status.SUCCESS
        assert record.file and record.file.filesize > 0
        assert len(mail.outbox) == 1
        assert mail.outbox[0].to == ["boss@corp.com"]
        assert mail.outbox[0].attachments
        report.refresh_from_db()
        assert report.last_status == "SUCCESS"

    def test_run_fail_closed_for_creator_without_grant(self, normal_user, dataset, model_registry, screen_urls):
        """核心验收：创建者无数据授权 → 产物为空且 SUCCESS（fail-closed 不报错）。"""
        from openpyxl import load_workbook

        grant(normal_user, screen_urls)
        report = _make_report(dataset, normal_user, recipients=["boss@corp.com"])
        from dataset.analysis_tasks import schedule_report_run

        task_id = schedule_report_run(str(report.pk))
        from task.models.export import ExportRecord

        record = ExportRecord.objects.get(pk=task_id)
        assert record.status == ExportRecord.Status.SUCCESS
        sheet = load_workbook(record.file.filepath).active
        assert sheet.max_row == 1  # 只有表头：无授权 → 空结果（fail-closed）

    def test_run_renders_fk_uuid_column(self, auth_client, dataset, superuser, model_registry, settings):
        """FK 列（UUID pk）进 xlsx：单元格转字符串，不再抛 Cannot convert UUID（回归守护）。"""
        from openpyxl import load_workbook

        from identity.models import DeptInfo
        from task.models.export import ExportRecord

        settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
        root = ModelLabelField.objects.get(name="identity.userinfo")
        ModelLabelField.objects.get_or_create(
            name="dept", parent=root, defaults={"field_type": ModelLabelField.FieldChoices.DATA, "label": "部门"}
        )
        dept = DeptInfo.objects.create(name="研发部", code="dev")
        UserInfo.objects.create_user(username="fkuser", password="Test@123456", dept=dept)
        ModelLabelField.objects.get_or_create(
            name="date_joined",
            parent=root,
            defaults={"field_type": ModelLabelField.FieldChoices.DATA, "label": "加入时间"},
        )
        dataset.columns = ["username", "dept", "date_joined"]
        dataset.save()

        report = _make_report(dataset, superuser, recipients=["boss@corp.com"])
        response = auth_client.post(f"{REPORT_URL}/{report.pk}/run", {}, format="json")
        assert response.status_code == 200, response.data
        task_id = response.json()["data"]["task_id"]

        record = ExportRecord.objects.get(pk=task_id)
        assert record.status == ExportRecord.Status.SUCCESS
        sheet = load_workbook(record.file.filepath).active
        assert sheet.cell(row=2, column=1).value == "fkuser"
        assert sheet.cell(row=2, column=2).value == str(dept.pk)
        joined = sheet.cell(row=2, column=3).value
        assert isinstance(joined, datetime) and joined.tzinfo is None

    def test_email_failure_degrades(self, auth_client, dataset, superuser, settings):
        """邮件失败：产物 SUCCESS 保留，last_status 标记邮件错误。"""
        settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
        report = _make_report(dataset, superuser, recipients=["boss@corp.com"])
        from unittest import mock

        # 邮件投递实现位于 dataset.report_render（analysis_tasks 仅再导出），patch 目标须与实现同源
        with mock.patch("dataset.report_render.EmailMessage.send", side_effect=Exception("smtp down")):
            auth_client.post(f"{REPORT_URL}/{report.pk}/run", {}, format="json")
        report.refresh_from_db()
        assert report.last_status == "SUCCESS_WITH_DELIVERY_ERROR"


class TestReportDatasetVisibility:
    """报表绑定/执行他人 personal 数据集：保存侧 400、执行侧 fail-closed。

    数据集可见域与 DatasetViewSet 读侧同口径（shared 全员 / personal 仅创建者）；
    报表执行以 report.creator 身份查数，因此「绑定」与「运行」两侧都要判定。
    """

    @staticmethod
    def _allow_dataset_rows(user):
        """给用户一条 dataset.dataset 的「全部数据」行权限。

        报表的 ``dataset`` 是关联字段，取值域先经行级数据权限过滤（fail-closed）；
        本用例要验证的是「可见性」这一层，故先把行权限补齐，避免把两种拒绝混为一谈。
        """
        from system.models import DataPermission

        rule = [
            {
                "table": "dataset.dataset",
                "field": "id",
                "type": "value.all",
                "match": "all",
                "value": "",
                "exclude": False,
            }
        ]
        user.rules.add(DataPermission.objects.create(name=f"数据集行权限-{user.username}", rules=rule))

    @staticmethod
    def _payload(dataset, name="越权报表"):
        return {
            "name": name,
            "dataset": str(dataset.pk),
            "mode": "rows",
            "frequency": "daily",
            "send_time": "08:00",
            "recipients": ["a@corp.com"],
        }

    def test_bind_other_personal_dataset_rejected(self, screen_urls, superuser):
        other = _second_user(screen_urls)
        self._allow_dataset_rows(other["user"])
        personal = Dataset.objects.create(
            name="他人私有集",
            bound_model="identity.userinfo",
            columns=["username"],
            visibility=Dataset.Visibility.PERSONAL,
            creator=superuser,
        )

        response = other["client"].post(REPORT_URL, self._payload(personal), format="json")

        assert response.status_code == 400, response.data
        assert str(_("The dataset is not visible to you (personal dataset of another user)")) in str(
            response.data["dataset"]
        )
        assert not Report.objects.filter(name="越权报表").exists()

    def test_bind_shared_dataset_allowed(self, screen_urls, dataset):
        other = _second_user(screen_urls)
        self._allow_dataset_rows(other["user"])
        response = other["client"].post(REPORT_URL, self._payload(dataset, "共享集报表"), format="json")
        assert response.status_code == 200, response.data
        assert Report.objects.filter(name="共享集报表").exists()

    def test_run_rejected_when_dataset_becomes_invisible(self, screen_urls, dataset, superuser):
        """建单后被改为他人 personal：run 动作立刻拒绝，不派发任务、不产生下载条目。"""
        from task.models.export import ExportRecord

        other = _second_user(screen_urls)
        report = _make_report(dataset, other["user"])
        dataset.visibility = Dataset.Visibility.PERSONAL
        dataset.creator = superuser
        dataset.save()

        response = other["client"].post(f"{REPORT_URL}/{report.pk}/run", {}, format="json")

        assert response.json()["code"] == 1001
        assert str(response.json()["detail"]) == str(
            _("The dataset is not visible to you (personal dataset of another user)")
        )
        assert not ExportRecord.objects.filter(params__report_id=str(report.pk)).exists()

    def test_scheduled_run_fails_closed(self, screen_urls, dataset, superuser):
        """调度链路不经过 run 动作：任务侧复核后记 FAILURE，不渲染任何数据。"""
        from dataset.analysis_tasks import schedule_report_run
        from task.models.export import ExportRecord

        other = _second_user(screen_urls)
        report = _make_report(dataset, other["user"])
        dataset.visibility = Dataset.Visibility.PERSONAL
        dataset.creator = superuser
        dataset.save()

        task_id = schedule_report_run(str(report.pk))

        record = ExportRecord.objects.get(pk=task_id)
        assert record.status == ExportRecord.Status.FAILURE
        assert not record.file
        assert str(_("The dataset is not visible to the report creator, run cancelled")) == record.error
        report.refresh_from_db()
        assert report.last_status == "FAILURE"


class TestReportImDelivery:
    """IM 投递渠道：逐渠道独立、未选邮件则不发邮件、未配置渠道记交付错误。"""

    @staticmethod
    def _fake_backend(monkeypatch, sent, enable=True):
        class FakeClient:
            @staticmethod
            def send_msg(users, message, subject="", **kwargs):
                sent["users"] = [user.pk for user in users]
                sent["subject"] = subject
                sent["message"] = message

        class FakeBackendMember:
            """对齐 BACKEND 枚举成员契约：is_enable 属性 + client 客户端。"""

            is_enable = enable
            client = FakeClient()

        class FakeBackendEnum:
            def __call__(self, value):
                sent["channel"] = value
                return FakeBackendMember()

        monkeypatch.setattr("notifications.backends.BACKEND", FakeBackendEnum())

    def test_im_channel_delivered_without_email(self, auth_client, dataset, superuser, settings, monkeypatch):
        """渠道 = 钉钉：消息发给 IM 收件人（用户主键），邮件通道不执行。"""
        settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
        sent = {}
        self._fake_backend(monkeypatch, sent)
        report = _make_report(dataset, superuser)
        report.notify_channels = ["dingtalk"]
        report.im_recipients = [superuser.pk]
        report.save(update_fields=["notify_channels", "im_recipients"])

        auth_client.post(f"{REPORT_URL}/{report.pk}/run", {}, format="json")

        report.refresh_from_db()
        assert report.last_status == "SUCCESS"
        assert sent["channel"] == "dingtalk"
        assert sent["users"] == [superuser.pk]
        assert report.name in sent["subject"]
        assert "定时报表" in sent["message"] or "Scheduled report" in sent["message"]
        assert mail.outbox == []  # 未选邮件渠道 → 不发邮件

    def test_im_not_configured_marks_delivery_error(self, auth_client, dataset, superuser, settings, monkeypatch):
        """渠道未配置（is_enable=False）：交付状态记错误明细，产物仍 SUCCESS。"""
        settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
        sent = {}
        self._fake_backend(monkeypatch, sent, enable=False)
        report = _make_report(dataset, superuser)
        report.notify_channels = ["feishu"]
        report.im_recipients = [superuser.pk]
        report.save(update_fields=["notify_channels", "im_recipients"])

        auth_client.post(f"{REPORT_URL}/{report.pk}/run", {}, format="json")

        from task.models.export import ExportRecord

        report.refresh_from_db()
        assert report.last_status == "SUCCESS_WITH_DELIVERY_ERROR"
        record = ExportRecord.objects.filter(module="Report").order_by("-created_time").first()
        assert "feishu: not configured" in (record.error or "")

    def test_notify_channels_validated(self, auth_client, dataset):
        payload = {
            "name": "坏渠道",
            "dataset": str(dataset.pk),
            "recipients": ["a@corp.com"],
            "notify_channels": ["telegram"],
        }
        response = auth_client.post(REPORT_URL, payload, format="json")
        assert response.status_code == 400

    def test_choices_matches_write_side_constants(self, auth_client):
        """渠道枚举单源守护：choices 下发值集 == 写入校验常量（前端不手抄选项表）。"""
        response = auth_client.get(f"{REPORT_URL}/choices")
        assert response.status_code == 200, response.data
        assert response.data["code"] == 1000
        choices = response.data["choices_dict"]
        assert choices["notify_channels"] == list(REPORT_NOTIFY_CHANNELS)
        assert choices["im_notify_channels"] == list(IM_NOTIFY_CHANNELS)

    def test_im_channel_requires_recipients(self, auth_client, dataset):
        payload = {
            "name": "缺IM收件人",
            "dataset": str(dataset.pk),
            "notify_channels": ["feishu"],
        }
        response = auth_client.post(REPORT_URL, payload, format="json")
        assert response.status_code == 400


# ---------------------------------------------------------------- helpers


def _make_report(dataset, creator, frequency="daily", send_time="08:00", weekday=0, recipients=None, mode="rows"):
    return Report.objects.create(
        name=f"报表-{dataset.name}-{frequency}-{creator.username}",
        dataset=dataset,
        mode=mode,
        frequency=frequency,
        send_time=send_time,
        weekday=weekday,
        recipients=recipients if recipients is not None else ["a@corp.com"],
        creator=creator,
    )


def _second_user(screen_urls):
    from rest_framework.test import APIClient

    user = UserInfo.objects.create_user(username="lisi", password="Test@123456")
    grant(user, screen_urls)
    client = APIClient(HTTP_USER_AGENT="pytest-agent")
    client.force_authenticate(user=user)
    return {"user": user, "client": client}


@pytest.fixture
def dashboard_factory(superuser):
    def _make(name="大屏用看板"):
        return Dashboard.objects.create(name=name, creator=superuser, visibility="shared")

    return _make


class TestReportListWithoutVisibilityDimension:
    """报表无 ``visibility`` 字段：非超管列表不得按 visibility 过滤（FieldError 500 回归）。

    可见性收敛是数据集 / 看板 / 大屏的共享维度；报表是调度配置（模型无该字段）。
    超管请求绕过收敛路径、既有 E2E 也以超管执行——该缺陷只在「非超管打开报表页」
    路径暴露。写保护仍由 _creator_guard 承担（非创建者只读）。
    """

    def test_non_superuser_list_returns_rows(self, normal_user, screen_urls, dataset, superuser):
        from rest_framework.test import APIClient

        report = _make_report(dataset, superuser)
        grant(normal_user, screen_urls)

        client = APIClient(HTTP_USER_AGENT="pytest-agent")
        client.force_authenticate(user=normal_user)
        response = client.get(REPORT_URL)

        assert response.status_code == 200
        assert report.name in {item["name"] for item in response.json()["data"]["results"]}

    def test_non_creator_recipients_masked_and_last_run_exposed(
        self, auth_client, normal_user, screen_urls, dataset, superuser
    ):
        """列表全员可见不变，但非创建者拿不到收件人原文；last_run_at 随列表下发。"""
        from rest_framework.test import APIClient

        from common.core.auth.credentials import MASK

        report = _make_report(dataset, superuser, recipients=["boss@corp.com"])
        Report.objects.filter(pk=report.pk).update(last_run_at=timezone.now())
        grant(normal_user, screen_urls)

        client = APIClient(HTTP_USER_AGENT="pytest-agent")
        client.force_authenticate(user=normal_user)
        row = next(item for item in client.get(REPORT_URL).json()["data"]["results"] if item["name"] == report.name)
        assert row["recipients"] == [MASK]
        assert row["last_run_at"] is not None

        owner_row = next(
            item for item in auth_client.get(REPORT_URL).json()["data"]["results"] if item["name"] == report.name
        )
        assert owner_row["recipients"] == ["boss@corp.com"]
