# -*- coding: utf-8 -*-
"""动态表单集成测试。

覆盖：schema 校验矩阵（key 格式/重复/控件类型/options/字段数上限）、提交
校验矩阵（required/选项外/数值越界/超长/未知键）、creator 隔离（他人不可见
不可改）、停用表单拒提交、超管全量、越权。
"""

import pytest
from rest_framework.test import APIClient

from dataset.models.dform import DynamicForm, DynamicFormSubmission
from system.models import UserInfo

pytestmark = pytest.mark.django_db

FORM_URL = "/api/system/dynamic-forms"
SUBMISSION_URL = "/api/system/dynamic-form-submissions"

SCHEMA = {
    "fields": [
        {"key": "name", "label": "姓名", "type": "input", "required": True, "max_length": 20},
        {"key": "level", "label": "级别", "type": "select", "options": ["P4", "P5", "P6"]},
        {"key": "score", "label": "得分", "type": "number", "min": 0, "max": 100},
        {"key": "remark", "label": "备注", "type": "textarea"},
    ]
}


@pytest.fixture
def form(superuser):
    return DynamicForm.objects.create(name="设备登记", schema=SCHEMA, creator=superuser)


def grant_form_menus(user):
    from system.models import Menu, MenuMeta, UserRole

    def _make(name, path, method):
        menu = Menu.objects.filter(name=name).first()
        if menu:
            return menu
        meta = MenuMeta.objects.create(title=name)
        return Menu.objects.create(
            name=name, path=path, method=method, menu_type=Menu.MenuChoices.PERMISSION, meta=meta
        )

    detail = "api/system/dynamic-forms/(?P<pk>[^/.]+)"
    sub_detail = "api/system/dynamic-form-submissions/(?P<pk>[^/.]+)"
    menus = [
        _make("list:DynamicForm", "api/system/dynamic-forms$", "GET"),
        _make("create:DynamicForm", "api/system/dynamic-forms$", "POST"),
        _make("retrieve:DynamicForm", detail + "$", "GET"),
        _make("list:DynamicFormSubmission", "api/system/dynamic-form-submissions$", "GET"),
        _make("create:DynamicFormSubmission", "api/system/dynamic-form-submissions$", "POST"),
        _make("partialUpdate:DynamicFormSubmission", sub_detail + "$", "PATCH"),
        _make("destroy:DynamicFormSubmission", sub_detail + "$", "DELETE"),
    ]
    role = user.roles.first() or UserRole.objects.create(name=f"role-{user.username}", code=user.username)
    user.roles.add(role)
    role.menu.set(menus)


def client_for(user):
    client = APIClient(HTTP_USER_AGENT="pytest-agent")
    client.force_authenticate(user=user)
    return client


class TestFormSchema:
    def test_anonymous_rejected(self, api_client):
        assert api_client.get(FORM_URL).status_code == 401

    def test_create_valid_schema(self, auth_client):
        response = auth_client.post(FORM_URL, {"name": "设备登记", "schema": SCHEMA}, format="json")
        assert response.status_code == 200, response.data

    @pytest.mark.parametrize(
        "schema",
        [
            {"fields": [{"key": "Bad-Key", "label": "x", "type": "input"}]},
            {
                "fields": [
                    {"key": "name", "label": "x", "type": "input"},
                    {"key": "name", "label": "y", "type": "input"},
                ]
            },
            {"fields": [{"key": "name", "label": "x", "type": "richtext"}]},
            {"fields": [{"key": "level", "label": "x", "type": "select"}]},  # 缺 options
            {"fields": [{"key": "name", "label": "x", "type": "input", "options": ["a"]}]},  # input 不接受 options
            {"fields": [{"key": f"f{i}", "label": "x", "type": "input"} for i in range(51)]},  # 超 50 字段
            {"fields": []},
        ],
    )
    def test_invalid_schema_rejected(self, auth_client, schema):
        response = auth_client.post(FORM_URL, {"name": "坏定义", "schema": schema}, format="json")
        assert response.status_code == 400


class TestSubmission:
    def test_submit_valid(self, form, normal_user):
        grant_form_menus(normal_user)
        client = client_for(normal_user)
        response = client.post(
            SUBMISSION_URL,
            {"form": str(form.pk), "data": {"name": "张三", "level": "P5", "score": 88}},
            format="json",
        )
        assert response.status_code == 200, response.data
        submission = DynamicFormSubmission.objects.get()
        assert submission.creator_id == normal_user.pk
        assert submission.data["level"] == "P5"

    def test_required_missing_rejected(self, form, normal_user):
        grant_form_menus(normal_user)
        client = client_for(normal_user)
        response = client.post(SUBMISSION_URL, {"form": str(form.pk), "data": {"level": "P5"}}, format="json")
        assert response.status_code == 400

    def test_option_outside_rejected(self, form, normal_user):
        grant_form_menus(normal_user)
        client = client_for(normal_user)
        response = client.post(
            SUBMISSION_URL,
            {"form": str(form.pk), "data": {"name": "张三", "level": "P99"}},
            format="json",
        )
        assert response.status_code == 400

    def test_number_bounds_rejected(self, form, normal_user):
        grant_form_menus(normal_user)
        client = client_for(normal_user)
        response = client.post(
            SUBMISSION_URL,
            {"form": str(form.pk), "data": {"name": "张三", "score": 101}},
            format="json",
        )
        assert response.status_code == 400

    def test_unknown_key_rejected(self, form, normal_user):
        grant_form_menus(normal_user)
        client = client_for(normal_user)
        response = client.post(
            SUBMISSION_URL,
            {"form": str(form.pk), "data": {"name": "张三", "hacker": "x"}},
            format="json",
        )
        assert response.status_code == 400

    def test_max_length_rejected(self, form, normal_user):
        grant_form_menus(normal_user)
        client = client_for(normal_user)
        response = client.post(
            SUBMISSION_URL,
            {"form": str(form.pk), "data": {"name": "x" * 21}},
            format="json",
        )
        assert response.status_code == 400

    def test_inactive_form_rejected(self, form, normal_user):
        form.is_active = False
        form.save()
        grant_form_menus(normal_user)
        client = client_for(normal_user)
        response = client.post(SUBMISSION_URL, {"form": str(form.pk), "data": {"name": "张三"}}, format="json")
        # 停用表单拒绝提交（序列化器校验 → HTTP 400 + 可读文案；活动语言随环境变化，双语兼容）
        assert response.status_code == 400
        detail = response.json()["detail"]
        assert "no longer accepting submissions" in detail or "不再接受提交" in detail, detail


class TestCreatorIsolation:
    def test_creator_sees_own_only(self, form, normal_user, superuser):
        grant_form_menus(normal_user)
        client = client_for(normal_user)
        client.post(SUBMISSION_URL, {"form": str(form.pk), "data": {"name": "张三"}}, format="json")
        # 本人可见
        body = client.get(SUBMISSION_URL).json()
        assert body["data"]["total"] == 1
        # 他人（第二个普通用户）不可见
        other = UserInfo.objects.create_user(username="lisi", password="Test@123456")
        grant_form_menus(other)
        other_body = client_for(other).get(SUBMISSION_URL).json()
        assert other_body["data"]["total"] == 0
        # 超管全量
        admin_body = auth_client_list(superuser)
        assert admin_body["data"]["total"] == 1

    def test_other_cannot_modify(self, form, normal_user, superuser):
        grant_form_menus(normal_user)
        client = client_for(normal_user)
        client.post(SUBMISSION_URL, {"form": str(form.pk), "data": {"name": "张三"}}, format="json")
        submission = DynamicFormSubmission.objects.get()
        other = UserInfo.objects.create_user(username="lisi", password="Test@123456")
        grant_form_menus(other)
        # creator 隔离：他人视角对象不可见（404→业务 400），自然不可改
        response = client_for(other).patch(f"{SUBMISSION_URL}/{submission.pk}", {"data": {"name": "改"}}, format="json")
        assert response.status_code == 400
        response = client_for(other).delete(f"{SUBMISSION_URL}/{submission.pk}")
        assert response.status_code == 400
        assert DynamicFormSubmission.objects.filter(pk=submission.pk).exists()

    def test_creator_can_update_own(self, form, normal_user):
        grant_form_menus(normal_user)
        client = client_for(normal_user)
        client.post(SUBMISSION_URL, {"form": str(form.pk), "data": {"name": "张三"}}, format="json")
        submission = DynamicFormSubmission.objects.get()
        response = client.patch(f"{SUBMISSION_URL}/{submission.pk}", {"data": {"name": "张三改"}}, format="json")
        assert response.json()["code"] == 1000
        submission.refresh_from_db()
        assert submission.data["name"] == "张三改"


def auth_client_list(superuser):
    from rest_framework.test import APIClient

    client = APIClient(HTTP_USER_AGENT="pytest-agent")
    client.force_authenticate(user=superuser)
    return client.get(SUBMISSION_URL).json()


# ---------------------------------------------------------------- 审批挂接


class TestFormApproval:
    """approval_required 表单走审批流（提交 412 → 通过 → 令牌重放落库）。"""

    @pytest.fixture
    def gated_form(self, superuser):
        return DynamicForm.objects.create(name="需审批登记", schema=SCHEMA, creator=superuser, approval_required=True)

    def test_api_exposes_approval_required(self, auth_client):
        """approval_required 经定义接口读写（前端设计器开关的契约面）。"""
        created = auth_client.post(
            FORM_URL, {"name": "审批表单", "schema": SCHEMA, "approval_required": True}, format="json"
        )
        assert created.status_code == 200, created.data
        payload = created.json()["data"]
        assert payload["approval_required"] is True
        # 详情回读
        detail = auth_client.get(f"{FORM_URL}/{payload['pk']}").json()["data"]
        assert detail["approval_required"] is True
        # 关闭回落为直提
        patched = auth_client.patch(f"{FORM_URL}/{payload['pk']}", {"approval_required": False}, format="json").json()[
            "data"
        ]
        assert patched["approval_required"] is False

    def test_submit_returns_412_and_creates_approval(self, gated_form, normal_user):
        grant_form_menus(normal_user)
        client = client_for(normal_user)
        response = client.post(
            SUBMISSION_URL,
            {"form": str(gated_form.pk), "data": {"name": "张三"}},
            format="json",
        )
        # 412 协议：type=approval_required + approval_id
        assert response.status_code == 412
        assert response.data["type"] == "approval_required"
        approval_id = response.data["data"]["approval_id"]
        from approval.models.approval import ApprovalRequest

        approval = ApprovalRequest.objects.get(pk=approval_id)
        assert approval.status == ApprovalRequest.Status.PENDING
        assert approval.params["data"] == {"name": "张三"}
        assert not DynamicFormSubmission.objects.exists()

    def test_superuser_bypasses_approval(self, gated_form, superuser):
        client = client_for(superuser)
        response = client.post(
            SUBMISSION_URL,
            {"form": str(gated_form.pk), "data": {"name": "管理员直提"}},
            format="json",
        )
        assert response.status_code == 200
        assert DynamicFormSubmission.objects.filter(data__name="管理员直提").exists()

    def test_replay_without_token_still_412(self, gated_form, normal_user, superuser):
        grant_form_menus(normal_user)
        client = client_for(normal_user)
        client.post(SUBMISSION_URL, {"form": str(gated_form.pk), "data": {"name": "张三"}}, format="json")
        # 未携令牌重发：find_active_pending 命中同一单，仍 412（不重复建单）
        response = client.post(SUBMISSION_URL, {"form": str(gated_form.pk), "data": {"name": "张三"}}, format="json")
        assert response.status_code == 412
        from approval.models.approval import ApprovalRequest

        assert ApprovalRequest.objects.count() == 1

    def test_replay_with_token_creates_submission(self, gated_form, normal_user, superuser):
        """审批通过 → 携令牌重放 → 令牌一次性消费 → 提交落库。"""
        grant_form_menus(normal_user)
        client = client_for(normal_user)
        response = client.post(SUBMISSION_URL, {"form": str(gated_form.pk), "data": {"name": "张三"}}, format="json")
        approval_id = response.data["data"]["approval_id"]
        from approval.models.approval import ApprovalRequest
        from approval.utils.approval import approve_request

        approval = ApprovalRequest.objects.get(pk=approval_id)
        assert approve_request(approval, superuser)[0] is True

        # 重放令牌走请求头 X-Approval-Id（query param 会进请求指纹导致不一致）；
        # Django 测试客户端需用 HTTP_X_APPROVAL_ID 才能映射出该头名
        client.credentials(HTTP_USER_AGENT="pytest-agent", HTTP_X_APPROVAL_ID=approval_id)
        response = client.post(SUBMISSION_URL, {"form": str(gated_form.pk), "data": {"name": "张三"}}, format="json")
        assert response.status_code == 200, response.data
        assert DynamicFormSubmission.objects.filter(data__name="张三").exists()

        # 令牌一次性：审批通过后系统已自动落库，再次重放返回幂等成功且不重复建行
        response = client.post(SUBMISSION_URL, {"form": str(gated_form.pk), "data": {"name": "张三"}}, format="json")
        assert response.status_code == 200
        assert DynamicFormSubmission.objects.count() == 1

    def test_replay_with_tampered_data_rejected(self, gated_form, normal_user, superuser):
        """指纹校验：批准 A、重放 B → 403 + 审批单 FAILED。"""
        grant_form_menus(normal_user)
        client = client_for(normal_user)
        response = client.post(SUBMISSION_URL, {"form": str(gated_form.pk), "data": {"name": "张三"}}, format="json")
        approval_id = response.data["data"]["approval_id"]
        from approval.models.approval import ApprovalRequest
        from approval.utils.approval import approve_request

        approval = ApprovalRequest.objects.get(pk=approval_id)
        assert approve_request(approval, superuser)[0] is True

        client.credentials(HTTP_USER_AGENT="pytest-agent", HTTP_X_APPROVAL_ID=approval_id)
        response = client.post(SUBMISSION_URL, {"form": str(gated_form.pk), "data": {"name": "李四"}}, format="json")
        assert response.status_code == 403
        approval.refresh_from_db()
        assert approval.status == ApprovalRequest.Status.FAILED
        # 审批通过后按原快照自动落库；篡改重放仍被拒，不会按篡改数据建行
        assert DynamicFormSubmission.objects.filter(data__name="李四").count() == 0
        assert DynamicFormSubmission.objects.filter(data__name="张三").count() == 1
