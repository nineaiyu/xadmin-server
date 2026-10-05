# -*- coding: utf-8 -*-
"""报表设计 API 集成测试（批次二）。

口径：
1. `design` 与既有调度字段并存：写入设计即进入「设计报表」，清空 design 回到存量口径；
2. 非法设计（未知列 / 未知组件类型 / 图表缺分组 / sum 取非数值列 / 行数越界）一律 400；
3. 读取返回归一化后的 design（未声明键被丢弃，列保序去重）。
"""

import pytest

from dataset.models.dataset import Dataset, Report

pytestmark = pytest.mark.django_db

REPORTS_URL = "/api/dataset/reports"


@pytest.fixture
def dataset(superuser):
    return Dataset.objects.create(
        name="设计报表数据集",
        bound_model="identity.userinfo",
        columns=["username", "gender", "is_active", "date_joined"],
        visibility="shared",
        creator=superuser,
    )


def _payload(dataset, design, name="设计报表"):
    return {
        "name": name,
        "dataset": str(dataset.pk),
        "recipients": ["design@example.com"],
        "design": design,
    }


class TestDesignWrite:
    def test_create_with_design(self, auth_client, dataset):
        body = auth_client.post(
            REPORTS_URL,
            _payload(
                dataset,
                {
                    "columns": ["gender", "username", "gender"],
                    "table_limit": 50,
                    "components": [
                        {"id": "c1", "type": "bar", "title": "性别分布", "group_by": "gender", "span": 6},
                        {"id": "n1", "type": "number", "title": "性别合计", "metric": "sum", "value_field": "gender"},
                    ],
                },
            ),
            format="json",
        ).json()
        assert body["code"] == 1000, body
        design = body["data"]["design"]
        assert design["columns"] == ["gender", "username"]
        assert design["table_limit"] == 50
        assert [item["type"] for item in design["components"]] == ["bar", "number"]
        assert design["components"][0]["span"] == 6

    def test_partial_update_design_only(self, auth_client, dataset):
        created = auth_client.post(REPORTS_URL, _payload(dataset, {}, name="设计报表-改"), format="json").json()
        pk = created["data"]["pk"]
        assert created["data"]["design"] == {}

        resp = auth_client.patch(
            f"{REPORTS_URL}/{pk}",
            {"design": {"columns": ["username"], "components": [{"id": "c1", "type": "pie", "group_by": "is_active"}]}},
            format="json",
        ).json()
        assert resp["code"] == 1000, resp
        assert resp["data"]["design"]["columns"] == ["username"]
        assert Report.objects.get(pk=pk).design["components"][0]["type"] == "pie"

    def test_clear_design_back_to_legacy(self, auth_client, dataset):
        created = auth_client.post(
            REPORTS_URL,
            _payload(dataset, {"columns": ["username"]}, name="设计报表-清空"),
            format="json",
        ).json()
        pk = created["data"]["pk"]
        resp = auth_client.patch(f"{REPORTS_URL}/{pk}", {"design": {}}, format="json").json()
        assert resp["code"] == 1000
        assert resp["data"]["design"] == {}
        # 调度字段不受影响
        assert resp["data"]["recipients"] == ["design@example.com"]

    def test_unknown_keys_are_dropped(self, auth_client, dataset):
        created = auth_client.post(
            REPORTS_URL,
            _payload(
                dataset,
                {"columns": ["username"], "components": [{"id": "c1", "type": "number", "color": "red"}]},
                name="设计报表-净",
            ),
            format="json",
        ).json()
        component = created["data"]["design"]["components"][0]
        assert "color" not in component


class TestDesignRejections:
    def _create(self, auth_client, dataset, design, name="设计报表-校验"):
        return auth_client.post(REPORTS_URL, _payload(dataset, design, name=name), format="json")

    def test_unknown_column(self, auth_client, dataset):
        resp = self._create(auth_client, dataset, {"columns": ["password"]})
        assert resp.status_code == 400

    def test_unknown_component_type(self, auth_client, dataset):
        resp = self._create(auth_client, dataset, {"components": [{"id": "c1", "type": "table", "group_by": "gender"}]})
        assert resp.status_code == 400

    def test_chart_without_group_by(self, auth_client, dataset):
        resp = self._create(auth_client, dataset, {"components": [{"id": "c1", "type": "line"}]})
        assert resp.status_code == 400

    def test_sum_on_text_column(self, auth_client, dataset):
        resp = self._create(
            auth_client,
            dataset,
            {
                "components": [
                    {"id": "c1", "type": "bar", "group_by": "gender", "metric": "sum", "value_field": "username"}
                ]
            },
        )
        assert resp.status_code == 400

    def test_table_limit_out_of_range(self, auth_client, dataset):
        assert self._create(auth_client, dataset, {"table_limit": 5}).status_code == 400
        assert self._create(auth_client, dataset, {"table_limit": 9999}).status_code == 400

    def test_rejected_payload_does_not_persist(self, auth_client, dataset):
        self._create(auth_client, dataset, {"columns": ["password"]})
        assert not Report.objects.filter(name="设计报表-校验").exists()

    def test_invalid_design_on_update_keeps_old_value(self, auth_client, dataset):
        created = auth_client.post(
            REPORTS_URL,
            _payload(dataset, {"columns": ["username"]}, name="设计报表-回滚"),
            format="json",
        ).json()
        pk = created["data"]["pk"]
        resp = auth_client.patch(
            f"{REPORTS_URL}/{pk}",
            {"design": {"components": [{"id": "c1", "type": "bar"}]}},
            format="json",
        )
        assert resp.status_code == 400
        assert Report.objects.get(pk=pk).design["columns"] == ["username"]


class TestDesignRead:
    def test_list_and_detail_return_design(self, auth_client, dataset):
        created = auth_client.post(
            REPORTS_URL,
            _payload(dataset, {"columns": ["gender"], "table_limit": 20}, name="设计报表-读"),
            format="json",
        ).json()
        pk = created["data"]["pk"]

        detail = auth_client.get(f"{REPORTS_URL}/{pk}").json()
        assert detail["data"]["design"] == {"columns": ["gender"], "table_limit": 20, "components": []}

        listed = auth_client.get(REPORTS_URL, {"name": "设计报表-读"}).json()["data"]["results"]
        assert listed[0]["design"]["table_limit"] == 20
