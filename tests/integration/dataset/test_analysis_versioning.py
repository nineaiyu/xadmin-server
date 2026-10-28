# -*- coding: utf-8 -*-
"""报表 / 大屏设计版本化与乐观锁。

口径：
1. 创建即记第一版（``design_version`` / ``layout_version`` 与版本表同步）；
2. 只有设计载荷（报表 design / 大屏 layout+mode+free_layout）实质变更才追加版本，
   改名 / 调度 / 收件人 / 主题调整不挤占版本历史；
3. ``rollback`` 还原目标版本并**再记一版**（回滚可逆，历史不删）；
4. ``base_version`` 与当前版本不一致时拒绝写入（并发保存不能静默覆盖）。
"""

import pytest

from dataset.models.dataset import Dataset
from dataset.models.report import Report

pytestmark = pytest.mark.django_db

REPORTS_URL = "/api/dataset/reports"
SCREENS_URL = "/api/dataset/screens"


@pytest.fixture
def dataset(superuser):
    return Dataset.objects.create(
        name="版本化数据集",
        bound_model="identity.userinfo",
        columns=["username", "gender"],
        visibility="shared",
        creator=superuser,
    )


def _report_body(dataset, design, name="版本化报表"):
    return {
        "name": name,
        "dataset": str(dataset.pk),
        "recipients": ["version@example.com"],
        "design": design,
    }


def _screen_body(layout, name="版本化大屏"):
    return {"name": name, "dashboards": [], "layout": layout}


def _text_pane(pk, x=0):
    return {"pk": pk, "type": "text", "x": x, "y": 0, "w": 6, "h": 4, "text": f"文案-{pk}"}


class TestReportVersioning:
    def test_create_and_design_change_append_versions(self, auth_client, dataset):
        created = auth_client.post(
            REPORTS_URL,
            _report_body(dataset, {"columns": ["username"], "table_limit": 20}),
            format="json",
        ).json()
        assert created["code"] == 1000, created
        report_pk = created["data"]["pk"]
        assert created["data"]["design_version"] == 1

        updated = auth_client.patch(
            f"{REPORTS_URL}/{report_pk}",
            {"design": {"columns": ["gender"], "table_limit": 50}},
            format="json",
        ).json()
        assert updated["code"] == 1000, updated
        assert updated["data"]["design_version"] == 2

        versions = auth_client.get(f"{REPORTS_URL}/{report_pk}/versions").json()
        assert versions["code"] == 1000, versions
        assert [row["version"] for row in versions["data"]] == [2, 1]

    def test_non_design_update_keeps_version(self, auth_client, dataset):
        created = auth_client.post(
            REPORTS_URL,
            _report_body(dataset, {"columns": ["username"]}),
            format="json",
        ).json()
        report_pk = created["data"]["pk"]

        renamed = auth_client.patch(f"{REPORTS_URL}/{report_pk}", {"name": "改名不记版本"}, format="json").json()
        assert renamed["code"] == 1000, renamed
        assert renamed["data"]["design_version"] == 1
        assert Report.objects.get(pk=report_pk).versions.count() == 1

    def test_rollback_restores_design_and_appends_version(self, auth_client, dataset):
        created = auth_client.post(
            REPORTS_URL,
            _report_body(dataset, {"columns": ["username"], "table_limit": 20}),
            format="json",
        ).json()
        report_pk = created["data"]["pk"]
        auth_client.patch(
            f"{REPORTS_URL}/{report_pk}",
            {"design": {"columns": ["gender"], "table_limit": 50}},
            format="json",
        )

        rolled = auth_client.post(f"{REPORTS_URL}/{report_pk}/rollback", {"version": 1}, format="json").json()
        assert rolled["code"] == 1000, rolled
        assert rolled["data"]["design"]["columns"] == ["username"]
        assert rolled["data"]["design_version"] == 3

        versions = auth_client.get(f"{REPORTS_URL}/{report_pk}/versions").json()
        assert [row["version"] for row in versions["data"]] == [3, 2, 1]

    def test_missing_version_rejected(self, auth_client, dataset):
        created = auth_client.post(
            REPORTS_URL,
            _report_body(dataset, {"columns": ["username"]}),
            format="json",
        ).json()
        report_pk = created["data"]["pk"]

        body = auth_client.post(f"{REPORTS_URL}/{report_pk}/rollback", {"version": 99}, format="json").json()
        assert body["code"] == 1001, body

    def test_stale_base_version_rejected(self, auth_client, dataset):
        created = auth_client.post(
            REPORTS_URL,
            _report_body(dataset, {"columns": ["username"]}),
            format="json",
        ).json()
        report_pk = created["data"]["pk"]

        # 客户端拿到的基线是 1，但服务端已前进到 2：并发保存必须被拒绝
        auth_client.patch(f"{REPORTS_URL}/{report_pk}", {"design": {"columns": ["gender"]}}, format="json")
        resp = auth_client.patch(
            f"{REPORTS_URL}/{report_pk}",
            {"design": {"columns": ["username"]}, "base_version": 1},
            format="json",
        )
        assert resp.status_code == 400, resp.data
        assert "base_version" in resp.data

        # 带上当前版本则放行
        ok = auth_client.patch(
            f"{REPORTS_URL}/{report_pk}",
            {"design": {"columns": ["username"]}, "base_version": 2},
            format="json",
        ).json()
        assert ok["code"] == 1000, ok


class TestScreenVersioning:
    def test_create_and_layout_change_append_versions(self, auth_client):
        created = auth_client.post(SCREENS_URL, _screen_body([_text_pane("p1")]), format="json").json()
        assert created["code"] == 1000, created
        screen_pk = created["data"]["pk"]
        assert created["data"]["layout_version"] == 1

        updated = auth_client.patch(
            f"{SCREENS_URL}/{screen_pk}",
            {"layout": [_text_pane("p1"), _text_pane("p2", x=6)]},
            format="json",
        ).json()
        assert updated["code"] == 1000, updated
        assert updated["data"]["layout_version"] == 2

        versions = auth_client.get(f"{SCREENS_URL}/{screen_pk}/versions").json()
        assert [row["version"] for row in versions["data"]] == [2, 1]

    def test_theme_update_keeps_version(self, auth_client):
        created = auth_client.post(SCREENS_URL, _screen_body([_text_pane("p1")]), format="json").json()
        screen_pk = created["data"]["pk"]

        themed = auth_client.patch(f"{SCREENS_URL}/{screen_pk}", {"theme": "light"}, format="json").json()
        assert themed["code"] == 1000, themed
        assert themed["data"]["layout_version"] == 1

    def test_rollback_restores_layout_and_appends_version(self, auth_client):
        created = auth_client.post(SCREENS_URL, _screen_body([_text_pane("p1")]), format="json").json()
        screen_pk = created["data"]["pk"]
        auth_client.patch(
            f"{SCREENS_URL}/{screen_pk}",
            {"layout": [_text_pane("p1"), _text_pane("p2", x=6)]},
            format="json",
        )

        rolled = auth_client.post(f"{SCREENS_URL}/{screen_pk}/rollback", {"version": 1}, format="json").json()
        assert rolled["code"] == 1000, rolled
        assert [pane["pk"] for pane in rolled["data"]["layout"]] == ["p1"]
        assert rolled["data"]["layout_version"] == 3

    def test_stale_base_version_rejected(self, auth_client):
        created = auth_client.post(SCREENS_URL, _screen_body([_text_pane("p1")]), format="json").json()
        screen_pk = created["data"]["pk"]
        auth_client.patch(
            f"{SCREENS_URL}/{screen_pk}",
            {"layout": [_text_pane("p1"), _text_pane("p2", x=6)]},
            format="json",
        )

        resp = auth_client.patch(
            f"{SCREENS_URL}/{screen_pk}",
            {"layout": [_text_pane("p1")], "base_version": 1},
            format="json",
        )
        assert resp.status_code == 400, resp.data
        assert "base_version" in resp.data
