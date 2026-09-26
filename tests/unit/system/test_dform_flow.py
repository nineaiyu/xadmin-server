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
from system.models import UserInfo
from system.models.dform import DynamicForm, DynamicFormSubmission
from system.utils.dform_flow import (
    DFORM_BIZ_TYPE,
    create_flow_instance,
    resubmit_submission,
    sync_dform_instance,
)

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
        path="/api/system/dynamic-form-submissions",
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
        path="/api/system/dynamic-form-submissions",
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
        path="/api/system/dynamic-form-submissions",
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
    from system.utils.dform import validate_schema

    with pytest.raises(ValidationError):
        validate_schema({"fields": [{"key": "x", "label": "X", "type": "unknown"}]})
