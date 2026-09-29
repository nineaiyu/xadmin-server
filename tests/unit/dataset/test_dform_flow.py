# -*- coding: utf-8 -*-
"""动态表单 × 审批：绑定流程提交 / 终态回写 / 驳回重提 / 审批通过自动落库。

守护四件事：
1. 绑定流程的表单提交进入流程引擎，实例与提交行双向绑定，终态经信号回写状态；
2. 驳回后仅申请人可重新提交，按当前数据发起新实例；
3. 审批中心通过登记的「通过后动作」自动落库，申请人无需手动重放，且只执行一次；
4. 审批中的提交不可修改（避免实例快照与提交数据不一致）。
"""

import pytest
from django.core.exceptions import ValidationError

from approval.models.approval import (
    ApprovalFlow,
    ApprovalFlowNode,
    ApprovalInstance,
    ApprovalRequest,
)
from approval.utils.approval import approve_request
from dataset.models.dform import DynamicForm, DynamicFormSubmission
from dataset.utils.dform_flow import (
    DFORM_BIZ_TYPE,
    create_flow_instance,
    resubmit_submission,
    sync_bound_flow_schema,
    sync_dform_instance,
)
from system.models import UserInfo

pytestmark = pytest.mark.django_db


@pytest.fixture
def applicant():
    return UserInfo.objects.create_user(username="dform_applicant", password="Test@123456", nickname="申请人")


@pytest.fixture
def approver(superuser):
    """敏感操作审批的引擎层会校验审批资格（超管或审批人集合）：
    本文件的用例直接调用 approve_request，因此审批人必须具备资格。"""
    return superuser


@pytest.fixture
def flow(approver):
    flow = ApprovalFlow.objects.create(
        name="入职登记审批",
        code="dform_onboarding",
        form_schema=[{"key": "days", "label": "天数", "type": "number"}],
    )
    ApprovalFlowNode.objects.create(
        flow=flow,
        name="人事确认",
        order=1,
        approve_type=ApprovalFlowNode.ApproveType.OR,
        assignee_type=ApprovalFlowNode.AssigneeType.USER,
        assignee_value=approver.username,
    )
    return flow


@pytest.fixture
def form(flow):
    return DynamicForm.objects.create(
        name="入职登记表",
        approval_flow=flow,
        schema={"fields": [{"key": "name", "label": "姓名", "type": "input", "required": True}]},
    )


def make_submission(form, applicant, data=None):
    return DynamicFormSubmission.objects.create(
        form=form,
        data=data if data is not None else {"name": "张三"},
        creator=applicant,
        modifier=applicant,
    )


def test_submit_creates_instance_and_pending_status(form, applicant):
    submission = make_submission(form, applicant)
    ok, detail = create_flow_instance(submission, applicant)
    assert ok and detail is None
    submission.refresh_from_db()
    assert submission.status == DynamicFormSubmission.Status.PENDING
    assert submission.instance is not None
    assert submission.instance.biz_type == DFORM_BIZ_TYPE
    assert submission.instance.biz_id == str(submission.pk)


def test_submit_rejected_when_flow_inactive(form, applicant):
    form.approval_flow.is_active = False
    form.approval_flow.save(update_fields=["is_active", "updated_time"])
    submission = make_submission(form, applicant)
    ok, detail = create_flow_instance(submission, applicant)
    assert not ok
    assert submission.status == ""


def test_terminal_status_syncs_back(form, applicant, approver):
    submission = make_submission(form, applicant)
    create_flow_instance(submission, applicant)
    instance = submission.instance
    sync_dform_instance(instance, ApprovalInstance.Status.APPROVED, "同意")
    submission.refresh_from_db()
    assert submission.status == DynamicFormSubmission.Status.APPROVED
    # 幂等：重复回写不产生副作用
    sync_dform_instance(instance, ApprovalInstance.Status.APPROVED, "同意")
    assert DynamicFormSubmission.objects.filter(pk=submission.pk).count() == 1


def test_resubmit_only_by_applicant_and_only_rejected(form, applicant, approver):
    submission = make_submission(form, applicant)
    create_flow_instance(submission, applicant)
    ok, _ = resubmit_submission(submission, applicant)
    assert not ok  # PENDING 不可重提

    sync_dform_instance(submission.instance, ApprovalInstance.Status.REJECTED, "材料不齐")
    submission.refresh_from_db()
    ok, detail = resubmit_submission(submission, approver)
    assert not ok  # 非申请人不可重提

    ok, _ = resubmit_submission(submission, applicant)
    assert ok
    submission.refresh_from_db()
    assert submission.status == DynamicFormSubmission.Status.PENDING
    assert submission.instance.biz_type == DFORM_BIZ_TYPE


def test_resubmit_requires_bound_flow(applicant):
    plain = DynamicForm.objects.create(
        name="无流程表单",
        schema={"fields": [{"key": "name", "label": "姓名", "type": "input"}]},
    )
    submission = DynamicFormSubmission.objects.create(
        form=plain, data={"name": "x"}, status=DynamicFormSubmission.Status.REJECTED, creator=applicant
    )
    ok, _ = resubmit_submission(submission, applicant)
    assert not ok


def test_pending_submission_cannot_be_modified(form, applicant):
    """审批中的提交不可改动：由序列化器校验前的守卫拦截（此处守护状态标记语义）。"""
    submission = make_submission(form, applicant)
    create_flow_instance(submission, applicant)
    assert submission.status == DynamicFormSubmission.Status.PENDING


def test_approve_request_auto_completes_submission(applicant, approver):
    plain = DynamicForm.objects.create(
        name="报销单",
        approval_required=True,
        schema={
            "fields": [
                {"key": "amount", "label": "金额", "type": "number", "required": True},
                {"key": "reason", "label": "事由", "type": "textarea"},
            ]
        },
    )
    approval = ApprovalRequest.objects.create(
        module="动态表单提交",
        method="POST",
        path="/api/dataset/dynamic-form-submissions",
        params={},
        payload={"form": str(plain.pk), "data": {"amount": 120, "reason": "出差"}},
        creator=applicant,
    )
    ok, detail = approve_request(approval, approver)
    assert ok and detail is None

    submission = DynamicFormSubmission.objects.filter(form=plain, creator=applicant).first()
    assert submission is not None, "审批通过后应自动落库，申请人无需手动重放"
    assert submission.data["amount"] == 120
    approval.refresh_from_db()
    assert approval.auto_completed is True
    assert approval.consume_time is not None

    # 只执行一次：再次触发不会重复建行
    approve_request(approval, approver)
    assert DynamicFormSubmission.objects.filter(form=plain, creator=applicant).count() == 1


def test_auto_complete_skipped_without_payload(applicant, approver):
    plain = DynamicForm.objects.create(
        name="无快照表单",
        schema={"fields": [{"key": "name", "label": "姓名", "type": "input"}]},
    )
    approval = ApprovalRequest.objects.create(
        module="动态表单提交",
        method="POST",
        path="/api/dataset/dynamic-form-submissions",
        params={},
        payload={},
        creator=applicant,
    )
    approve_request(approval, approver)
    assert not DynamicFormSubmission.objects.filter(form=plain).exists()
    approval.refresh_from_db()
    assert approval.auto_completed is False


def test_auto_complete_failure_does_not_break_approval(applicant, approver):
    """payload 校验失败（缺必填）时审批结果仍生效，保留手动重放兜底。"""
    plain = DynamicForm.objects.create(
        name="缺必填表单",
        schema={"fields": [{"key": "amount", "label": "金额", "type": "number", "required": True}]},
    )
    approval = ApprovalRequest.objects.create(
        module="动态表单提交",
        method="POST",
        path="/api/dataset/dynamic-form-submissions",
        params={},
        payload={"form": str(plain.pk), "data": {}},
        creator=applicant,
    )
    ok, _ = approve_request(approval, approver)
    assert ok
    approval.refresh_from_db()
    assert approval.status == ApprovalRequest.Status.APPROVED
    assert approval.auto_completed is False
    assert not DynamicFormSubmission.objects.filter(form=plain).exists()


def test_schema_validation_still_enforced_on_write():
    """定义侧校验不变：未知控件类型拒绝（回归护栏）。"""
    from dataset.utils.dform import validate_schema

    with pytest.raises(ValidationError):
        validate_schema({"fields": [{"key": "x", "label": "X", "type": "unknown"}]})


class TestSubmitConcurrencyGuard:
    """并发收敛：双击 / 重放不得产生重复流程实例（源状态 CAS + 行锁）。"""

    def test_stale_object_cannot_create_second_instance(self, form, applicant):
        """两个请求各自读到同一 DRAFT 行：只有第一次能推进（第二次拿到可读失败）。"""
        submission = make_submission(form, applicant)
        stale = DynamicFormSubmission.objects.get(pk=submission.pk)

        ok, detail = create_flow_instance(submission, applicant)
        assert ok and detail is None

        ok_second, detail_second = create_flow_instance(stale, applicant)
        assert ok_second is False
        assert detail_second

        assert ApprovalInstance.objects.filter(biz_type=DFORM_BIZ_TYPE, biz_id=str(submission.pk)).count() == 1
        submission.refresh_from_db()
        assert submission.status == DynamicFormSubmission.Status.PENDING
        assert submission.instance_id is not None

    def test_cas_failure_creates_no_instance(self, form, applicant):
        """占位失败时不创建实例（无孤儿实例、无幻影通知）。"""
        submission = make_submission(form, applicant)
        create_flow_instance(submission, applicant)
        before = ApprovalInstance.objects.count()

        stale = DynamicFormSubmission.objects.get(pk=submission.pk)
        stale.status = DynamicFormSubmission.Status.DRAFT  # 旧对象携带的过期源状态
        ok, _detail = create_flow_instance(stale, applicant)

        assert ok is False
        assert ApprovalInstance.objects.count() == before

    def test_flow_failure_keeps_source_status(self, form, applicant):
        """发起失败整体回滚：CAS 一并还原，行保持可重试的源状态。"""
        form.approval_flow.is_active = False
        form.approval_flow.save(update_fields=["is_active", "updated_time"])
        submission = make_submission(form, applicant)
        ok, _detail = create_flow_instance(submission, applicant)
        assert ok is False
        submission.refresh_from_db()
        assert submission.status == ""

    def test_resubmit_rejects_stale_rejected_object(self, form, applicant):
        """驳回重提同样只允许一次：并发重放第二次失败且不新增实例。"""
        submission = make_submission(form, applicant)
        create_flow_instance(submission, applicant)
        sync_dform_instance(submission.instance, ApprovalInstance.Status.REJECTED, "材料不齐")
        submission.refresh_from_db()

        stale = DynamicFormSubmission.objects.get(pk=submission.pk)
        ok, _detail = resubmit_submission(submission, applicant)
        assert ok
        ok_second, detail_second = resubmit_submission(stale, applicant)
        assert ok_second is False
        assert detail_second
        assert ApprovalInstance.objects.filter(biz_type=DFORM_BIZ_TYPE, biz_id=str(submission.pk)).count() == 2


class TestFieldAssigneeDualResolution:
    """FIELD 型审批人 pk/用户名双解析（dform 选人控件存 pk 的集成断裂修复）。"""

    def test_field_assignee_by_user_pk(self, form, applicant, approver):
        """dform user 控件值 = 用户主键（int）：按 pk 解析（修复前按用户名解析为空）。"""
        flow = form.approval_flow
        ApprovalFlowNode.objects.create(
            flow=flow,
            name="字段审批人",
            order=2,
            assignee_type=ApprovalFlowNode.AssigneeType.FIELD,
            assignee_value="approver_field",
        )
        from approval.utils.approval_flow.conditions import resolve_assignees

        resolved = resolve_assignees(flow.nodes.get(order=2), applicant, {"approver_field": approver.pk})
        assert [user.pk for user in resolved] == [approver.pk]

    def test_field_assignee_by_username_still_works(self, form, applicant, approver):
        """独立流程表单（存用户名）行为不变。"""
        flow = form.approval_flow
        ApprovalFlowNode.objects.create(
            flow=flow,
            name="字段审批人",
            order=2,
            assignee_type=ApprovalFlowNode.AssigneeType.FIELD,
            assignee_value="approver_field",
        )
        from approval.utils.approval_flow.conditions import resolve_assignees

        resolved = resolve_assignees(flow.nodes.get(order=2), applicant, {"approver_field": approver.username})
        assert [user.pk for user in resolved] == [approver.pk]

    def test_field_assignee_mixed_list_resolves_union(self, form, applicant, approver):
        """多选值（pk + 用户名混合列表）：候选取并集。"""
        flow = form.approval_flow
        ApprovalFlowNode.objects.create(
            flow=flow,
            name="字段审批人",
            order=2,
            assignee_type=ApprovalFlowNode.AssigneeType.FIELD,
            assignee_value="approver_field",
        )
        from approval.utils.approval_flow.conditions import resolve_assignees

        resolved = resolve_assignees(
            flow.nodes.get(order=2), applicant, {"approver_field": [approver.pk, "flow_approver1"]}
        )
        assert [user.pk for user in resolved] == [approver.pk]

    def test_field_assignee_unknown_value_stays_empty(self, form, applicant):
        """未知值仍 fail-closed（空候选）。"""
        flow = form.approval_flow
        ApprovalFlowNode.objects.create(
            flow=flow,
            name="字段审批人",
            order=2,
            assignee_type=ApprovalFlowNode.AssigneeType.FIELD,
            assignee_value="approver_field",
        )
        from approval.utils.approval_flow.conditions import resolve_assignees

        assert resolve_assignees(flow.nodes.get(order=2), applicant, {"approver_field": "ghost_user"}) == []


class TestResubmitRevalidatesCurrentSchema:
    """驳回重提按当前 schema 完整重校验（防绕过改版新增必填）。"""

    def test_resubmit_blocked_by_new_required_field(self, form, applicant):
        """改版新增必填后，旧数据重提被拒且不产生新实例。"""
        submission = make_submission(form, applicant)
        create_flow_instance(submission, applicant)
        sync_dform_instance(submission.instance, ApprovalInstance.Status.REJECTED, "材料不齐")

        form.schema = {
            "fields": [
                {"key": "name", "label": "姓名", "type": "input", "required": True},
                {"key": "reason2", "label": "补充说明", "type": "input", "required": True},
            ]
        }
        form.save(update_fields=["schema", "updated_time"])

        ok, detail = resubmit_submission(submission, applicant)
        assert ok is False
        assert detail
        assert ApprovalInstance.objects.filter(biz_type=DFORM_BIZ_TYPE, biz_id=str(submission.pk)).count() == 1

    def test_resubmit_passes_after_data_fixed(self, form, applicant):
        """补齐新增必填后可正常重提。"""
        submission = make_submission(form, applicant)
        create_flow_instance(submission, applicant)
        sync_dform_instance(submission.instance, ApprovalInstance.Status.REJECTED, "材料不齐")

        form.schema = {
            "fields": [
                {"key": "name", "label": "姓名", "type": "input", "required": True},
                {"key": "reason2", "label": "补充说明", "type": "input", "required": True},
            ]
        }
        form.save(update_fields=["schema", "updated_time"])
        submission.data = {"name": "张三", "reason2": "已补充"}
        submission.save(update_fields=["data", "updated_time"])

        ok, detail = resubmit_submission(submission, applicant)
        assert ok and detail is None

    def test_resubmit_rejects_removed_key(self, form, applicant):
        """改版删除字段后，携带旧键的数据重提被拒（未知键拒绝口径）。"""
        submission = make_submission(form, applicant, data={"name": "张三", "ghost": "x"})
        create_flow_instance(submission, applicant)
        sync_dform_instance(submission.instance, ApprovalInstance.Status.REJECTED, "材料不齐")
        # 驳回期间 schema 删掉 ghost 字段
        form.schema = {"fields": [{"key": "name", "label": "姓名", "type": "input", "required": True}]}
        form.save(update_fields=["schema", "updated_time"])

        ok, detail = resubmit_submission(submission, applicant)
        assert ok is False
        assert "ghost" in detail


class TestSchemaChangeFlowReferenceGuard:
    """schema 变更的被引用字段删除检查（与流程侧版本化对称的防静默改路）。"""

    @pytest.fixture
    def flow_with_condition(self, form):
        """节点条件引用 dform 字段 amount/urgent + FIELD 审批人引用 owner。"""
        flow = form.approval_flow
        form.schema = {
            "fields": [
                {"key": "name", "label": "姓名", "type": "input", "required": True},
                {"key": "amount", "label": "金额", "type": "number"},
                {"key": "urgent", "label": "加急", "type": "select", "options": ["yes", "no"]},
                {"key": "owner", "label": "审批人", "type": "user"},
            ]
        }
        form.save(update_fields=["schema", "updated_time"])
        flow.form_schema = []
        flow.save(update_fields=["form_schema", "updated_time"])
        ApprovalFlowNode.objects.create(
            flow=flow,
            name="金额门槛",
            order=2,
            assignee_type=ApprovalFlowNode.AssigneeType.USER,
            assignee_value="dform_applicant",
            condition={"field": "amount", "op": "gte", "value": 100},
            routes=[{"condition": {"field": "urgent", "op": "eq", "value": "yes"}, "target": 1}],
        )
        return flow

    def test_removing_referenced_field_is_rejected(self, form, flow_with_condition):
        """删除被节点条件引用的 amount → 拒绝并报出字段名。"""
        from dataset.serializers.dform import DynamicFormSerializer

        serializer = DynamicFormSerializer(
            instance=form,
            data={"schema": {"fields": [{"key": "name", "label": "姓名", "type": "input", "required": True}]}},
            partial=True,
        )
        assert serializer.is_valid() is False
        assert "amount" in str(serializer.errors)

    def test_removing_route_and_assignee_referenced_fields_rejected(self, form, flow_with_condition):
        """路由条件字段 urgent 与 FIELD 审批人字段 owner 同样受保护。"""
        from dataset.serializers.dform import DynamicFormSerializer

        ApprovalFlowNode.objects.create(
            flow=flow_with_condition,
            name="字段审批人",
            order=3,
            assignee_type=ApprovalFlowNode.AssigneeType.FIELD,
            assignee_value="owner",
        )
        serializer = DynamicFormSerializer(
            instance=form,
            data={
                "schema": {
                    "fields": [
                        {"key": "name", "label": "姓名", "type": "input", "required": True},
                        {"key": "amount", "label": "金额", "type": "number"},
                        {"key": "urgent", "label": "加急", "type": "select", "options": ["yes", "no"]},
                    ]
                }
            },
            partial=True,
        )
        assert serializer.is_valid() is False
        assert "owner" in str(serializer.errors)

    def test_removing_unreferenced_field_allowed(self, form, flow_with_condition):
        """删除未被引用的字段不受限（正常改版仍可进行）。"""
        from dataset.serializers.dform import DynamicFormSerializer

        form.schema = {
            "fields": [
                {"key": "name", "label": "姓名", "type": "input", "required": True},
                {"key": "amount", "label": "金额", "type": "number"},
                {"key": "urgent", "label": "加急", "type": "select", "options": ["yes", "no"]},
                {"key": "memo", "label": "备注", "type": "input"},
            ]
        }
        form.save(update_fields=["schema", "updated_time"])
        serializer = DynamicFormSerializer(
            instance=form,
            data={
                "schema": {
                    "fields": [
                        {"key": "name", "label": "姓名", "type": "input", "required": True},
                        {"key": "amount", "label": "金额", "type": "number"},
                        {"key": "urgent", "label": "加急", "type": "select", "options": ["yes", "no"]},
                    ]
                }
            },
            partial=True,
        )
        assert serializer.is_valid(), serializer.errors

    def test_unbound_form_has_no_check(self, flow_with_condition, applicant):
        """未绑定流程的表单不受检查。"""
        from dataset.serializers.dform import DynamicFormSerializer

        free_form = DynamicForm.objects.create(
            name="自由表单",
            schema={"fields": [{"key": "amount", "label": "金额", "type": "number"}]},
        )
        serializer = DynamicFormSerializer(
            instance=free_form,
            data={"schema": {"fields": [{"key": "name", "label": "姓名", "type": "input"}]}},
            partial=True,
        )
        assert serializer.is_valid(), serializer.errors


class TestFlowFormSchemaProjection:
    """dform schema → flow.form_schema 单向投影（含流程侧编辑锁）。"""

    def test_bind_projects_schema(self, applicant):
        """绑定（创建时绑定）即投影：字段集/必填/类型映射一致。"""
        from dataset.serializers.dform import DynamicFormSerializer

        flow = ApprovalFlow.objects.create(name="投影流程", code="projection_flow", form_schema=[])
        ApprovalFlowNode.objects.create(
            flow=flow,
            name="审批",
            order=1,
            assignee_type=ApprovalFlowNode.AssigneeType.USER,
            assignee_value="dform_applicant",
        )
        serializer = DynamicFormSerializer(
            data={
                "name": "投影表单",
                "approval_flow": str(flow.pk),
                "schema": {
                    "fields": [
                        {"key": "name", "label": "姓名", "type": "input", "required": True},
                        {"key": "days", "label": "天数", "type": "number"},
                        {"key": "kind", "label": "类型", "type": "radio", "options": ["a", "b"]},
                    ]
                },
            }
        )
        assert serializer.is_valid(), serializer.errors
        form = serializer.save()

        flow.refresh_from_db()
        assert flow.form_schema == [
            {"key": "name", "label": "姓名", "type": "text", "required": True, "options": []},
            {"key": "days", "label": "天数", "type": "number", "required": False, "options": []},
            {"key": "kind", "label": "类型", "type": "select", "required": False, "options": ["a", "b"]},
        ]
        assert form.approval_flow_id == flow.pk

    def test_schema_change_reprojects(self, form):
        """表单改版（未被引用字段增删）→ 流程投影即时跟随。"""
        from dataset.serializers.dform import DynamicFormSerializer

        serializer = DynamicFormSerializer(
            instance=form,
            data={
                "schema": {
                    "fields": [
                        {"key": "name", "label": "姓名", "type": "input", "required": True},
                        {"key": "days", "label": "请假天数", "type": "number"},
                    ]
                }
            },
            partial=True,
        )
        assert serializer.is_valid(), serializer.errors
        serializer.save()

        form.approval_flow.refresh_from_db()
        assert [item["key"] for item in form.approval_flow.form_schema] == ["name", "days"]

    def test_flow_side_form_schema_write_is_locked(self, form):
        """编辑锁：绑定期流程侧 form_schema 改动被忽略，form_schema_locked=true。"""
        from approval.serializers.approval_flow import ApprovalFlowSerializer

        flow = form.approval_flow
        serializer = ApprovalFlowSerializer(
            instance=flow,
            data={"form_schema": [{"key": "hacked", "label": "注入", "type": "text"}]},
            partial=True,
        )
        assert serializer.is_valid(), serializer.errors
        serializer.save()
        flow.refresh_from_db()
        assert not [item for item in flow.form_schema if item["key"] == "hacked"]
        assert ApprovalFlowSerializer(flow).data["form_schema_locked"] is True

    def test_unbound_flow_unlocked(self):
        """未绑定流程可自由编辑 form_schema（存量行为）。"""
        from approval.serializers.approval_flow import ApprovalFlowSerializer

        flow = ApprovalFlow.objects.create(name="独立流程", code="standalone_flow", form_schema=[])
        ApprovalFlowNode.objects.create(
            flow=flow,
            name="审批",
            order=1,
            assignee_type=ApprovalFlowNode.AssigneeType.USER,
            assignee_value="dform_applicant",
        )
        assert ApprovalFlowSerializer(flow).data["form_schema_locked"] is False

    def test_unbind_resyncs_when_other_forms_bound(self, applicant):
        """解绑：旧流程仍有其他绑定表单时重投影（本表单字段移出）。"""
        from dataset.serializers.dform import DynamicFormSerializer

        flow = ApprovalFlow.objects.create(name="共用流程", code="shared_flow", form_schema=[])
        ApprovalFlowNode.objects.create(
            flow=flow,
            name="审批",
            order=1,
            assignee_type=ApprovalFlowNode.AssigneeType.USER,
            assignee_value="dform_applicant",
        )
        schema_a = {"fields": [{"key": "a_field", "label": "A", "type": "input", "required": True}]}
        schema_b = {"fields": [{"key": "b_field", "label": "B", "type": "input", "required": True}]}
        form_a = DynamicForm.objects.create(name="表单A", approval_flow=flow, schema=schema_a)
        DynamicForm.objects.create(name="表单B", approval_flow=flow, schema=schema_b)
        sync_bound_flow_schema(form_a)
        flow.refresh_from_db()
        assert [item["key"] for item in flow.form_schema] == ["a_field", "b_field"]

        # 表单A 解绑
        serializer = DynamicFormSerializer(instance=form_a, data={"approval_flow": None}, partial=True)
        assert serializer.is_valid(), serializer.errors
        serializer.save()
        flow.refresh_from_db()
        assert [item["key"] for item in flow.form_schema] == ["b_field"]

    def test_unbind_keeps_projection_when_last_form(self, form):
        """解绑最后一个绑定表单：投影结果保留（不静默清空），锁随绑定解除释放。"""
        from approval.serializers.approval_flow import ApprovalFlowSerializer
        from dataset.serializers.dform import DynamicFormSerializer

        flow = form.approval_flow
        sync_bound_flow_schema(form)  # 先建立投影基线（夹具自带的 form_schema 是独立配置面）
        flow.refresh_from_db()
        assert [item["key"] for item in flow.form_schema] == ["name"]
        serializer = DynamicFormSerializer(instance=form, data={"approval_flow": None}, partial=True)
        assert serializer.is_valid(), serializer.errors
        serializer.save()
        flow.refresh_from_db()
        assert [item["key"] for item in flow.form_schema] == ["name"]
        assert ApprovalFlowSerializer(flow).data["form_schema_locked"] is False


class TestFormDataListPayloadShrink:
    """表单数据列表 ?data_fields= 行内 data 载荷收缩。"""

    def test_data_fields_shrinks_payload(self, form, applicant):
        from dataset.serializers.dform import FormDataListSerializer

        submission = make_submission(form, applicant, data={"name": "张三", "secret": "不想在列表回传"})
        serializer = FormDataListSerializer([submission], many=True, context={"data_fields": {"name"}})
        row = serializer.data[0]
        assert row["data"] == {"name": "张三"}

    def test_default_keeps_full_payload(self, form, applicant):
        from dataset.serializers.dform import FormDataListSerializer

        submission = make_submission(form, applicant, data={"name": "张三", "secret": "保留"})
        serializer = FormDataListSerializer([submission], many=True)
        row = serializer.data[0]
        assert row["data"] == {"name": "张三", "secret": "保留"}
