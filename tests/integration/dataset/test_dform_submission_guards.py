# -*- coding: utf-8 -*-
"""「我的填报」写守卫：终态只读（含免审批已生效单）与批量删除逐行守卫。

口径：
- 可编辑白名单 = 草稿（DRAFT）与驳回（REJECTED）；审批中（PENDING）、已通过
  （APPROVED）、已撤回（CANCELLED）与免审批直接生效（status=""）一律只读——
  已生效单是留痕数据，可改写/硬删等于绕过审批留痕（历史缺陷：守卫只枚举
  已知终态、放行空状态）；
- 批量删除必须逐行走与单删相同的 _creator_guard：越权/终态项进 failures 明细，
  不静默跳过、不整批 SQL 直删；
- 管理面口径：改/删动作对超管放行终态限制（清理坏数据），提交动作不放行；
  审批中（PENDING）任何角色都不可改删（流程快照推进中）。
"""

import pytest

from dataset.models.dform import DynamicForm, DynamicFormSubmission

pytestmark = pytest.mark.django_db

SUBMISSIONS_URL = "/api/dataset/dynamic-form-submissions"

SCHEMA = {"fields": [{"key": "name", "label": "姓名", "type": "input", "required": True}]}


@pytest.fixture
def form(superuser):
    return DynamicForm.objects.create(name="守卫表单", schema=SCHEMA, creator=superuser)


def _make_submission(form, creator, status="", **overrides):
    params = {"form": form, "data": {"name": "张三"}, "creator": creator, "status": status}
    params.update(overrides)
    return DynamicFormSubmission.objects.create(**params)


def _outsider_client(user):
    """第三方非超管客户端：授予提交面权限码但不持有任何归属豁免。"""
    from django.core.cache import cache as django_cache
    from rest_framework.test import APIClient

    from identity.models import UserRole
    from system.models import Menu, MenuMeta

    role = UserRole.objects.create(name=f"outsider-{user.username}", code=f"outsider-{user.username}")
    user.roles.add(role)
    menus = []
    for name, path, method in (
        ("list:FormMySubmission", "api/dataset/dynamic-form-submissions$", "GET"),
        ("partialUpdate:FormMySubmission", "api/dataset/dynamic-form-submissions/(?P<pk>[^/.]+)$", "PATCH"),
        ("destroy:FormMySubmission", "api/dataset/dynamic-form-submissions/(?P<pk>[^/.]+)$", "DELETE"),
        ("batchDestroy:FormMySubmission", "api/dataset/dynamic-form-submissions/batch-destroy$", "POST"),
    ):
        meta = MenuMeta.objects.create(title=name)
        menus.append(
            Menu.objects.create(name=name, path=path, method=method, menu_type=Menu.MenuChoices.PERMISSION, meta=meta)
        )
    role.menu.set(menus)
    django_cache.clear()
    client = APIClient(HTTP_USER_AGENT="pytest-agent")
    client.force_authenticate(user=user)
    return client


class TestEffectiveSubmissionReadOnly:
    """免审批已生效单（status=""）终态只读：创建者本人直调 API 不可改写/删除。

    终态约束的对象是申请人（留痕不可自改）；超管不受终态限制（管理面清理）。
    """

    def test_effective_submission_update_rejected(self, form, normal_user):
        row = _make_submission(form, normal_user)
        outsider = _outsider_client(normal_user)
        resp = outsider.patch(f"{SUBMISSIONS_URL}/{row.pk}", {"data": {"name": "李四"}}, format="json")
        assert resp.data["code"] == 1003, resp.data
        row.refresh_from_db()
        assert row.data == {"name": "张三"}

    def test_effective_submission_destroy_rejected(self, form, normal_user):
        row = _make_submission(form, normal_user)
        outsider = _outsider_client(normal_user)
        resp = outsider.delete(f"{SUBMISSIONS_URL}/{row.pk}")
        assert resp.data["code"] == 1003, resp.data
        assert DynamicFormSubmission.objects.filter(pk=row.pk).exists()

    def test_superuser_still_manages_terminal_rows(self, auth_client, form, superuser):
        """超管不受终态限制（管理面清理保留）：可改可删 status="" 行。"""
        row = _make_submission(form, superuser)
        resp = auth_client.delete(f"{SUBMISSIONS_URL}/{row.pk}")
        assert resp.data["code"] == 1000, resp.data
        assert not DynamicFormSubmission.objects.filter(pk=row.pk).exists()

    @pytest.mark.parametrize(
        "status",
        [
            DynamicFormSubmission.Status.PENDING,
            DynamicFormSubmission.Status.APPROVED,
            DynamicFormSubmission.Status.CANCELLED,
        ],
    )
    def test_terminal_states_stay_readonly(self, form, normal_user, status):
        """审批中/已通过/已撤回的既有终态守卫不回退（创建者视角）。"""
        row = _make_submission(form, normal_user, status=status)
        outsider = _outsider_client(normal_user)
        resp = outsider.patch(f"{SUBMISSIONS_URL}/{row.pk}", {"data": {"name": "改"}}, format="json")
        assert resp.data["code"] == 1003, resp.data

    def test_draft_and_rejected_remain_editable(self, form, normal_user):
        """可编辑白名单：草稿与驳回态继续可改（重提/继续填写的正常链路）。"""
        draft = _make_submission(form, normal_user, status=DynamicFormSubmission.Status.DRAFT)
        rejected = _make_submission(form, normal_user, status=DynamicFormSubmission.Status.REJECTED)
        outsider = _outsider_client(normal_user)
        ok_draft = outsider.patch(f"{SUBMISSIONS_URL}/{draft.pk}", {"data": {"name": "甲"}}, format="json")
        ok_rejected = outsider.patch(f"{SUBMISSIONS_URL}/{rejected.pk}", {"data": {"name": "乙"}}, format="json")
        assert ok_draft.data["code"] == 1000, ok_draft.data
        assert ok_rejected.data["code"] == 1000, ok_rejected.data

    def test_non_creator_still_denied_first(self, form, normal_user, superuser):
        """归属守卫仍在：第三方（非创建者、非超管）改他人草稿同样 1003。

        超管豁免归属守卫是既有设计，不能用超管客户端验证本条。
        """
        row = _make_submission(form, superuser, status=DynamicFormSubmission.Status.DRAFT)
        outsider = _outsider_client(normal_user)
        resp = outsider.patch(f"{SUBMISSIONS_URL}/{row.pk}", {"data": {"name": "越权"}}, format="json")
        # 两层守卫先到先拦：行级数据权限（get_object 404→400）或归属守卫（1003）
        assert resp.data["code"] in (400, 1003), resp.data
        row.refresh_from_db()
        assert row.data == {"name": "张三"}


class TestBatchDestroyRowwiseGuard:
    """批量删除逐行守卫：终态/越权项进 failures 明细，不得整批 SQL 直删。"""

    def test_batch_destroy_mixed_statuses_rowwise(self, auth_client, form, superuser):
        draft = _make_submission(form, superuser, status=DynamicFormSubmission.Status.DRAFT)
        pending = _make_submission(form, superuser, status=DynamicFormSubmission.Status.PENDING)
        effective = _make_submission(form, superuser, status="")
        approved = _make_submission(form, superuser, status=DynamicFormSubmission.Status.APPROVED)

        resp = auth_client.post(
            f"{SUBMISSIONS_URL}/batch-destroy",
            [str(draft.pk), str(pending.pk), str(effective.pk), str(approved.pk)],
            format="json",
        )
        assert resp.status_code == 200, resp.data
        body = resp.data
        assert body["code"] == 1000, body
        # 超管管理面：草稿与终态行可删；审批中项因流程快照推进中被拒
        assert set(body["data"]["success"]) == {str(draft.pk), str(effective.pk), str(approved.pk)}
        failed_pks = {item["pk"] for item in body["data"]["failures"]}
        # 审批中项留库（流程快照推进中不可删）；终态项按超管管理面放行
        assert failed_pks == {str(pending.pk)}
        assert DynamicFormSubmission.objects.filter(pk=pending.pk).exists()
        assert not DynamicFormSubmission.objects.filter(pk__in=[draft.pk, effective.pk, approved.pk]).exists()

    def test_batch_destroy_others_rows_rejected(self, form, normal_user, superuser):
        """归属守卫在批删逐行分支同样生效：他人提交进 failures（第三方非超管视角）。"""
        others = _make_submission(form, superuser, status=DynamicFormSubmission.Status.DRAFT)
        outsider = _outsider_client(normal_user)
        resp = outsider.post(f"{SUBMISSIONS_URL}/batch-destroy", [str(others.pk)], format="json")
        assert resp.data["code"] == 1000, resp.data
        # 他人行不可删除：数据权限预过滤（不在 success 也不在候选池）或归属守卫（failures）
        assert resp.data["data"]["success"] == []
        assert DynamicFormSubmission.objects.filter(pk=others.pk).exists()
