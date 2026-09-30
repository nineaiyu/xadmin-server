# -*- coding: utf-8 -*-
"""可筛选字段物化列（提交时写入 + 列表按 JSON 包含筛选）集成测试。

覆盖：提交/草稿/编辑三条写入路径的物化、列表筛选命中与 fail-closed（未勾选字段）、
设计器 schema 对 `filterable` 标记的校验。
"""

import json

import pytest
from rest_framework.test import APIClient

from dataset.models.dform import DynamicForm, DynamicFormSubmission
from system.models import Menu, MenuMeta, UserRole

pytestmark = pytest.mark.django_db

FORM_URL = "/api/dataset/dynamic-forms"
SUBMISSION_URL = "/api/dataset/dynamic-form-submissions"
FORM_DATA_URL = "/api/dataset/form-data"

SCHEMA = {
    "fields": [
        {"key": "name", "label": "姓名", "type": "input", "required": True, "filterable": True},
        {"key": "level", "label": "级别", "type": "select", "options": ["P4", "P5"], "filterable": True},
        {"key": "score", "label": "得分", "type": "number", "filterable": True},
        {"key": "remark", "label": "备注", "type": "textarea"},
    ]
}


@pytest.fixture
def form(superuser):
    return DynamicForm.objects.create(name="可筛选登记", schema=SCHEMA, creator=superuser)


def grant_form_menus(user):
    """与 test_dynamic_form 同口径：授予填报链路所需权限点。"""

    def _make(name, path, method):
        menu = Menu.objects.filter(name=name).first()
        if menu:
            return menu
        meta = MenuMeta.objects.create(title=name)
        return Menu.objects.create(
            name=name, path=path, method=method, menu_type=Menu.MenuChoices.PERMISSION, meta=meta
        )

    sub_detail = "api/dataset/dynamic-form-submissions/(?P<pk>[^/.]+)"
    menus = [
        _make("list:DynamicForm", "api/dataset/dynamic-forms$", "GET"),
        _make("list:DynamicFormSubmission", "api/dataset/dynamic-form-submissions$", "GET"),
        _make("create:DynamicFormSubmission", "api/dataset/dynamic-form-submissions$", "POST"),
        _make("partialUpdate:DynamicFormSubmission", sub_detail + "$", "PATCH"),
        _make("submit:FormMySubmission", sub_detail + "/submit$", "POST"),
        _make("list:FormData", "api/dataset/form-data$", "GET"),
    ]
    role = user.roles.first() or UserRole.objects.create(name=f"role-{user.username}", code=user.username)
    user.roles.add(role)
    role.menu.set(menus)


def client_for(user):
    client = APIClient(HTTP_USER_AGENT="pytest-agent")
    client.force_authenticate(user=user)
    return client


def _submit(client, form, data, **extra):
    return client.post(SUBMISSION_URL, {"form": str(form.pk), "data": data, **extra}, format="json")


def test_submit_materializes_filterable_fields(form, normal_user):
    """提交即物化：只写勾选「可筛选」的字段，空值不写入。"""
    grant_form_menus(normal_user)
    response = _submit(
        client_for(normal_user), form, {"name": "张三", "level": "P5", "remark": "不应物化"}, as_draft=True
    )
    assert response.json()["code"] == 1000, response.data
    submission = DynamicFormSubmission.objects.get()
    assert submission.filter_data == {"name": "张三", "level": "P5"}

    # 草稿提交（submit 端点）后物化随数据同步（score 补入）
    draft_client = client_for(normal_user)
    resp = draft_client.post(
        f"{SUBMISSION_URL}/{submission.pk}/submit",
        {"data": {"name": "张三", "level": "P5", "remark": "备注", "score": 88}},
        format="json",
    )
    assert resp.json()["code"] == 1000, resp.data
    submission.refresh_from_db()
    assert submission.filter_data == {"name": "张三", "level": "P5", "score": 88}


def test_update_rematerializes(form, normal_user):
    """编辑（PATCH）后物化随数据更新（改为草稿编辑保留轻校验语义）。"""
    grant_form_menus(normal_user)
    client = client_for(normal_user)
    _submit(client, form, {"name": "张三", "level": "P4"}, as_draft=True)
    submission = DynamicFormSubmission.objects.get()
    resp = client.patch(
        f"{SUBMISSION_URL}/{submission.pk}",
        {"data": {"name": "张三", "level": "P5", "score": 60}},
        format="json",
    )
    assert resp.json()["code"] == 1000, resp.data
    submission.refresh_from_db()
    assert submission.filter_data == {"name": "张三", "level": "P5", "score": 60}


def test_list_filter_by_materialized_fields(form, normal_user, superuser):
    """列表筛选：可筛选字段多条件 AND；未勾选字段 fail-closed；通用模式按形态编译。"""
    from dataset.models.dform import DynamicFormSubmission as Submission

    for index, payload in enumerate(
        (
            {"name": "张三", "level": "P5", "score": 88},
            {"name": "张三", "level": "P4", "score": 60},
            {"name": "李四", "level": "P5", "score": 88},
        )
    ):
        Submission.objects.create(
            form=form,
            data={**payload, "remark": None},
            filter_data={key: value for key, value in payload.items() if key != "remark"},
            creator=normal_user if index != 2 else superuser,
        )

    # 管理端（超管，数据权限全量）：按可筛选字段筛选（多条件 AND，命中 GIN 的包含语义）
    admin = APIClient(HTTP_USER_AGENT="pytest-agent")
    admin.force_authenticate(user=superuser)
    resp = admin.get(
        FORM_DATA_URL,
        {"form": str(form.pk), "filter_data": json.dumps({"level": "P5", "score": 88})},
    )
    assert resp.json()["code"] == 1000, resp.data
    assert {row["data"]["name"] for row in resp.json()["data"]["results"]} == {"张三", "李四"}

    # 未勾选「可筛选」的字段：fail-closed（可读报错，不回退扫原 data 列）
    resp = admin.get(FORM_DATA_URL, {"form": str(form.pk), "filter_data": json.dumps({"remark": "x"})})
    assert resp.json()["code"] != 1000

    # 未选表单时按通用形态编译（不限表单的列表口径）：正常筛选
    resp = admin.get(FORM_DATA_URL, {"filter_data": json.dumps({"level": "P5"})})
    assert resp.json()["code"] == 1000, resp.data
    assert resp.json()["data"]["total"] == 2

    # 「我的填报」：creator 隔离 + 同参数筛选（无 form 上下文的通用编译）
    grant_form_menus(normal_user)
    mine = client_for(normal_user).get(SUBMISSION_URL, {"filter_data": json.dumps({"level": "P4"})})
    assert mine.json()["code"] == 1000, mine.data
    rows = mine.json()["data"]["results"]
    assert [row["data"]["name"] for row in rows] == ["张三"]


def test_schema_filterable_flag_validation(auth_client):
    """设计器标记校验：可筛选仅对等值型控件开放，且必须是布尔。"""
    bad_type = {
        "fields": [{"key": "files", "label": "附件", "type": "upload", "filterable": True}],
    }
    resp = auth_client.post(FORM_URL, {"name": "坏标记-类型", "schema": bad_type}, format="json")
    assert resp.status_code == 400

    bad_value = {
        "fields": [{"key": "name", "label": "姓名", "type": "input", "filterable": "yes"}],
    }
    resp = auth_client.post(FORM_URL, {"name": "坏标记-取值", "schema": bad_value}, format="json")
    assert resp.status_code == 400

    ok = {
        "fields": [
            {"key": "name", "label": "姓名", "type": "input", "filterable": True},
            {"key": "remark", "label": "备注", "type": "textarea"},
        ],
    }
    resp = auth_client.post(FORM_URL, {"name": "正常标记", "schema": ok}, format="json")
    assert resp.json()["code"] == 1000, resp.data
