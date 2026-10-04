# -*- coding: utf-8 -*-
"""「我的填报」列表轻量序列化。

列表逐行序列化 ``form_schema`` + ``approval_trail``（每行触发
``instance.tasks.all()`` 且无 prefetch）是列表页主开销，而列表页不需要这两个
详情口径字段。修复后列表走 ``MySubmissionListSerializer``（固定列 + data 摘要），
详情（retrieve）仍走读写序列化器取全量。
"""

import pytest

from dataset.models.dform import DynamicForm, DynamicFormSubmission

pytestmark = pytest.mark.django_db

SUBMISSIONS_URL = "/api/dataset/dynamic-form-submissions"

SCHEMA = {
    "fields": [
        {"key": "name", "label": "姓名", "type": "input", "required": True},
    ]
}

LIST_KEYS = {"pk", "form", "form_name", "schema_version", "data", "status", "creator", "created_time", "updated_time"}


@pytest.fixture
def form(superuser):
    return DynamicForm.objects.create(name="轻列表表单", schema=SCHEMA, creator=superuser)


@pytest.fixture
def submission(form, superuser):
    return DynamicFormSubmission.objects.create(form=form, data={"name": "张三"}, creator=superuser)


def test_list_omits_schema_and_trail(auth_client, submission):
    """列表行不含 form_schema / approval_trail，且保留列表语义列。"""
    resp = auth_client.get(SUBMISSIONS_URL)
    assert resp.data["code"] == 1000, resp.data
    rows = resp.data["data"]["results"]
    assert rows, "列表至少应返回一条"
    row = next(item for item in rows if item["pk"] == str(submission.pk))
    assert set(row.keys()) == LIST_KEYS
    assert "form_schema" not in row
    assert "approval_trail" not in row
    assert row["form_name"] == submission.form.name
    assert row["data"] == {"name": "张三"}


def test_detail_keeps_full_serializer(auth_client, submission):
    """详情（抽屉）仍走读写序列化器：form_schema / approval_trail 不缺席。"""
    resp = auth_client.get(f"{SUBMISSIONS_URL}/{submission.pk}")
    assert resp.data["code"] == 1000, resp.data
    detail = resp.data["data"]
    assert "form_schema" in detail
    assert detail["form_schema"]
    assert "approval_trail" in detail


def test_list_rows_is_light_for_bulk(auth_client, form, superuser, django_assert_max_num_queries):
    """性能口径回归：批量行的列表不做逐行 schema / 流程任务展开。

    以查询数上限兜底序列化器切换未回退（列表 N 行不再逐行触发
    instance.tasks.all()）。
    """
    DynamicFormSubmission.objects.bulk_create(
        [DynamicFormSubmission(form=form, data={"name": f"用户{i}"}, creator=superuser) for i in range(30)]
    )
    with django_assert_max_num_queries(40):
        resp = auth_client.get(f"{SUBMISSIONS_URL}?size=100")
        assert resp.data["code"] == 1000
        assert len(resp.data["data"]["results"]) >= 30
