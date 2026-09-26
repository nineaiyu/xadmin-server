# -*- coding: utf-8 -*-
"""动态表单：草稿（DRAFT）/ 模板（is_template）/ 数据字典联动。

口径：
- 草稿：`as_draft=true` 创建/编辑走轻校验（允许缺必填），`{pk}/submit` 时按
  schema 严格校验后进入 流程引擎 / 操作审批 / 直接生效 三分支；
- 模板：与表单同表（is_template），列表默认不出模板（kind=templates 只出模板），
  available-forms 与提交外键均排除模板，模板创建后标记不可变、不可绑定流程；
- 字典：选项型字段可绑定 dict（与内联 options 互斥），提交校验读字典值（fail-closed）。
"""

import pytest

from approval.models.approval import ApprovalFlow, ApprovalFlowNode
from system.models import UserInfo
from system.models.dform import DynamicForm, DynamicFormSubmission
from system.models.dict import DataDict

pytestmark = pytest.mark.django_db

FORMS_URL = "/api/system/dynamic-forms"
SUBMISSIONS_URL = "/api/system/dynamic-form-submissions"


def _make_form(**overrides):
    defaults = {
        "name": "入职登记",
        "schema": {
            "fields": [
                {"key": "name", "label": "姓名", "type": "input", "required": True},
            ]
        },
    }
    defaults.update(overrides)
    return DynamicForm.objects.create(**defaults)


def _make_flow(approver, code="dform_draft_flow"):
    flow = ApprovalFlow.objects.create(name=f"流程-{code}", code=code, form_schema=[], is_active=True)
    ApprovalFlowNode.objects.create(
        flow=flow,
        name="人事确认",
        order=1,
        approve_type=ApprovalFlowNode.ApproveType.OR,
        assignee_type=ApprovalFlowNode.AssigneeType.USER,
        assignee_value=approver.username,
    )
    return flow


class TestDraftLifecycle:
    def test_draft_create_skips_required(self, auth_client, superuser):
        form = _make_form()
        resp = auth_client.post(f"{SUBMISSIONS_URL}", {"form": form.pk, "data": {}, "as_draft": True}, format="json")
        assert resp.data["code"] == 1000, resp.data
        submission = DynamicFormSubmission.objects.get(pk=resp.data["data"]["pk"])
        assert submission.status == DynamicFormSubmission.Status.DRAFT

    def test_draft_submit_strict_validation_then_effective(self, auth_client):
        form = _make_form()
        created = auth_client.post(f"{SUBMISSIONS_URL}", {"form": form.pk, "data": {}, "as_draft": True}, format="json")
        pk = created.data["data"]["pk"]

        missing = auth_client.post(f"{SUBMISSIONS_URL}/{pk}/submit", {}, format="json")
        assert missing.data["code"] == 1001, missing.data

        ok = auth_client.post(f"{SUBMISSIONS_URL}/{pk}/submit", {"data": {"name": "张三"}}, format="json")
        assert ok.data["code"] == 1000, ok.data
        submission = DynamicFormSubmission.objects.get(pk=pk)
        assert submission.status == ""
        assert submission.data == {"name": "张三"}

    def test_draft_update_allows_missing_required(self, auth_client):
        form = _make_form()
        created = auth_client.post(f"{SUBMISSIONS_URL}", {"form": form.pk, "data": {}, "as_draft": True}, format="json")
        pk = created.data["data"]["pk"]
        updated = auth_client.patch(f"{SUBMISSIONS_URL}/{pk}", {"data": {"name": ""}}, format="json")
        assert updated.data["code"] == 1000, updated.data

    def test_submit_only_for_draft(self, auth_client):
        form = _make_form()
        created = auth_client.post(f"{SUBMISSIONS_URL}", {"form": form.pk, "data": {"name": "李四"}}, format="json")
        pk = created.data["data"]["pk"]
        resp = auth_client.post(f"{SUBMISSIONS_URL}/{pk}/submit", {}, format="json")
        assert resp.data["code"] == 1001, resp.data

    def test_draft_submit_with_flow_enters_engine(self, auth_client, superuser):
        approver = UserInfo.objects.create_user(username="draft_approver", password="Test@123456")
        flow = _make_flow(approver)
        form = _make_form(name="流程登记表", approval_flow=flow)
        created = auth_client.post(f"{SUBMISSIONS_URL}", {"form": form.pk, "data": {}, "as_draft": True}, format="json")
        pk = created.data["data"]["pk"]

        resp = auth_client.post(f"{SUBMISSIONS_URL}/{pk}/submit", {"data": {"name": "王五"}}, format="json")
        assert resp.data["code"] == 1000, resp.data
        submission = DynamicFormSubmission.objects.get(pk=pk)
        assert submission.status == DynamicFormSubmission.Status.PENDING
        assert submission.instance_id is not None
        assert submission.instance.biz_type == "dform_submission"

    def test_other_user_cannot_submit_draft(self, api_client, normal_user, menu_factory):
        """creator 隔离：他人草稿对普通用户不可见（取值域过滤 → 404），提交无从下手。"""
        form = _make_form()
        owner = UserInfo.objects.create_user(username="draft_owner", password="Test@123456")
        submission = DynamicFormSubmission.objects.create(
            form=form, data={}, status=DynamicFormSubmission.Status.DRAFT, creator=owner, modifier=owner
        )
        list_menu = menu_factory("list:FormMySubmission", path="api/system/dynamic-form-submissions$", method="GET")
        submit_menu = menu_factory(
            "submit:FormMySubmission",
            path="api/system/dynamic-form-submissions/(?P<pk>[^/.]+)/submit$",
            method="POST",
        )
        normal_user.roles.first().menu.add(list_menu, submit_menu)
        api_client.force_authenticate(user=normal_user)
        resp = api_client.post(f"{SUBMISSIONS_URL}/{submission.pk}/submit", {}, format="json")
        # 取值域过滤后对象不存在（全局异常处理把 404 归一为 400 业务码）
        assert resp.status_code == 400, resp.data
        submission.refresh_from_db()
        assert submission.status == DynamicFormSubmission.Status.DRAFT


class TestDraftOperationApproval:
    """草稿提交 × 操作审批（表单 approval_required）：412 待审批 → 通过后自动落库。"""

    def _grant(self, normal_user, menu_factory):
        menus = [
            menu_factory("list:FormMySubmission", path="api/system/dynamic-form-submissions$", method="GET"),
            menu_factory("create:FormMySubmission", path="api/system/dynamic-form-submissions$", method="POST"),
            menu_factory(
                "submit:FormMySubmission",
                path="api/system/dynamic-form-submissions/(?P<pk>[^/.]+)/submit$",
                method="POST",
            ),
        ]
        normal_user.roles.first().menu.add(*menus)

    def test_draft_submit_then_auto_complete_and_replay(self, api_client, normal_user, menu_factory):
        from approval.models.approval import ApprovalRequest
        from approval.utils.approval import approve_request

        approver = UserInfo.objects.create_superuser(
            username="draft_op_approver", email="draft_op@example.com", password="Test@123456"
        )
        form = _make_form(name="操作审批草稿表", approval_required=True)
        self._grant(normal_user, menu_factory)
        api_client.force_authenticate(user=normal_user)

        created = api_client.post(f"{SUBMISSIONS_URL}", {"form": form.pk, "data": {}, "as_draft": True}, format="json")
        pk = created.data["data"]["pk"]
        payload = {"data": {"name": "张三"}}

        pending = api_client.post(f"{SUBMISSIONS_URL}/{pk}/submit", payload, format="json")
        assert pending.status_code == 412, pending.data
        submission = DynamicFormSubmission.objects.get(pk=pk)
        assert submission.status == DynamicFormSubmission.Status.DRAFT

        approval = ApprovalRequest.objects.get(creator=normal_user, status=ApprovalRequest.Status.PENDING)
        ok, detail = approve_request(approval, approver)
        assert ok, detail
        submission.refresh_from_db()
        assert submission.status == ""
        assert submission.data == {"name": "张三"}

        # 审批通过后的重放（携令牌）返回成功语义，不被「仅草稿可提交」拦截
        replay = api_client.post(
            f"{SUBMISSIONS_URL}/{pk}/submit",
            payload,
            format="json",
            HTTP_X_APPROVAL_ID=str(approval.pk),
        )
        assert replay.data.get("code") == 1000, replay.data


class TestFormTemplate:
    def test_template_created_and_listed_by_kind(self, auth_client):
        _make_form(name="普通表单")
        created = auth_client.post(
            FORMS_URL,
            {
                "name": "模板-入职",
                "is_template": True,
                "is_active": False,
                "schema": {"fields": [{"key": "name", "label": "姓名", "type": "input"}]},
            },
            format="json",
        )
        assert created.data["code"] == 1000, created.data

        listed = auth_client.get(FORMS_URL)
        names = [row["name"] for row in listed.data["data"]["results"]]
        assert "普通表单" in names and "模板-入职" not in names

        templates = auth_client.get(f"{FORMS_URL}?kind=templates")
        names = [row["name"] for row in templates.data["data"]["results"]]
        assert names == ["模板-入职"]

    def test_template_excluded_from_available_forms_and_submit(self, auth_client):
        template = _make_form(name="模板-A", is_template=True, is_active=True)
        available = auth_client.get(f"{SUBMISSIONS_URL}/available-forms")
        assert template.pk not in [row["pk"] for row in available.data["data"]]

        resp = auth_client.post(f"{SUBMISSIONS_URL}", {"form": template.pk, "data": {}}, format="json")
        assert resp.status_code == 400, resp.data

    def test_template_flag_immutable(self, auth_client):
        form = _make_form()
        resp = auth_client.patch(f"{FORMS_URL}/{form.pk}", {"is_template": True}, format="json")
        assert resp.status_code == 400, resp.data

    def test_template_cannot_bind_flow(self, auth_client, superuser):
        approver = UserInfo.objects.create_user(username="tmpl_approver", password="Test@123456")
        flow = _make_flow(approver, code="tmpl_flow")
        resp = auth_client.post(
            FORMS_URL,
            {
                "name": "模板-流程",
                "is_template": True,
                "is_active": False,
                "approval_flow": str(flow.pk),
                "schema": {"fields": [{"key": "name", "label": "姓名", "type": "input"}]},
            },
            format="json",
        )
        assert resp.status_code == 400, resp.data


class TestDictDrivenFields:
    @pytest.fixture
    def priority_dict(self, db):
        parent = DataDict.objects.create(code="dform_priority", label="优先级", sort=1)
        DataDict.objects.create(parent=parent, code="high", label="高", value="high", sort=1)
        DataDict.objects.create(parent=parent, code="low", label="低", value="low", sort=2)
        return parent

    def _dict_form(self):
        return _make_form(
            name="字典表单",
            schema={
                "fields": [
                    {"key": "priority", "label": "优先级", "type": "select", "dict": "dform_priority"},
                ]
            },
        )

    def test_schema_rejects_invalid_dict_code(self, auth_client):
        resp = auth_client.post(
            FORMS_URL,
            {
                "name": "非法字典",
                "schema": {"fields": [{"key": "p", "label": "P", "type": "select", "dict": "Invalid-Code"}]},
            },
            format="json",
        )
        assert resp.status_code == 400, resp.data

    def test_schema_rejects_options_with_dict(self, auth_client):
        resp = auth_client.post(
            FORMS_URL,
            {
                "name": "双配置",
                "schema": {
                    "fields": [{"key": "p", "label": "P", "type": "select", "dict": "dform_priority", "options": ["a"]}]
                },
            },
            format="json",
        )
        assert resp.status_code == 400, resp.data

    def test_schema_rejects_dict_on_plain_type(self, auth_client):
        resp = auth_client.post(
            FORMS_URL,
            {
                "name": "错位字典",
                "schema": {"fields": [{"key": "p", "label": "P", "type": "input", "dict": "dform_priority"}]},
            },
            format="json",
        )
        assert resp.status_code == 400, resp.data

    def test_submit_validates_against_dict_values(self, auth_client, priority_dict):
        form = self._dict_form()
        bad = auth_client.post(f"{SUBMISSIONS_URL}", {"form": form.pk, "data": {"priority": "unknown"}}, format="json")
        assert bad.status_code == 400, bad.data

        ok = auth_client.post(f"{SUBMISSIONS_URL}", {"form": form.pk, "data": {"priority": "high"}}, format="json")
        assert ok.data["code"] == 1000, ok.data

    def test_empty_dict_fails_closed(self, auth_client):
        DataDict.objects.create(code="dform_empty", label="空字典", sort=1)
        form = _make_form(
            name="空字典表单",
            schema={"fields": [{"key": "p", "label": "P", "type": "radio", "dict": "dform_empty"}]},
        )
        resp = auth_client.post(f"{SUBMISSIONS_URL}", {"form": form.pk, "data": {"p": "any"}}, format="json")
        assert resp.status_code == 400, resp.data
