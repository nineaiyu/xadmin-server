#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""动态表单 × 审批流程引擎联动。

表单绑定审批流程后，提交进入流程引擎（多级审批），实例终态经
``approval_instance_finished`` 信号回写提交状态；被驳回的提交允许申请人
修改数据后重新提交（重新发起流程实例）。未绑定流程的表单不受影响。
"""

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from common.utils import get_logger
from system.models.dform import DynamicForm, DynamicFormSubmission

logger = get_logger(__name__)

# 业务标识：ApprovalInstance.biz_type / approval_instance_finished 分发键
DFORM_BIZ_TYPE = "dform_submission"


def build_instance_title(form, applicant) -> str:
    """流程实例标题：表单名 + 提交人，便于审批列表一眼区分。"""
    username = getattr(applicant, "nickname", "") or getattr(applicant, "username", "")
    return f"{form.name}（{username}）" if username else str(form.name)


def create_flow_instance(submission, applicant):
    """为绑定流程的表单提交创建流程实例并置 PENDING。返回 (ok, detail)。

    失败时调用方回滚事务：宁可拒绝提交，也不留下状态与实例不一致的提交行。
    """
    from system.utils.approval_flow import create_instance

    form = submission.form
    if form.approval_flow_id is None:
        return False, str(_("The form is not bound to an approval flow"))
    flow = form.approval_flow
    if flow is None:
        return False, str(_("The approval flow does not exist"))
    if not flow.is_active:
        return False, str(_("The approval flow is not active"))

    instance, error = create_instance(
        flow=flow,
        applicant=applicant,
        title=build_instance_title(form, applicant),
        form_data=submission.data or {},
        biz_type=DFORM_BIZ_TYPE,
        biz_id=str(submission.pk),
    )
    if error:
        return False, error

    submission.instance = instance
    submission.status = DynamicFormSubmission.Status.PENDING
    submission.save(update_fields=["instance", "status", "updated_time"])
    return True, None


def resubmit_submission(submission, user):
    """被驳回后重新提交：仅申请人、仅 REJECTED；按当前数据发起新流程实例。返回 (ok, detail)。"""
    if submission.creator_id != user.pk and not getattr(user, "is_superuser", False):
        return False, str(_("Only the applicant can resubmit the submission"))
    if submission.status != DynamicFormSubmission.Status.REJECTED:
        return False, str(_("Only rejected submissions can be resubmitted"))
    if not submission.form.is_active:
        return False, str(_("This form is no longer accepting submissions"))
    return create_flow_instance(submission, user)


def submit_from_approval(approval, user):
    """审批通过后自动落库：按审批单快照重建表单提交。返回 (ok, detail)。

    申请人取审批单 creator（不是审批人）；此处只覆盖「操作审批」链路——绑定流程的
    表单提交走流程引擎，不会生成这类审批单。
    """
    from system.utils.dform import validate_submission_data

    payload = approval.payload or {}
    form = DynamicForm.objects.filter(pk=payload.get("form") or "", is_active=True).first()
    if form is None:
        return False, str(_("The form does not exist or is no longer accepting submissions"))
    try:
        data = validate_submission_data(form.schema, payload.get("data") or {})
    except ValidationError as exc:
        messages = getattr(exc, "messages", None) or [str(exc)]
        return False, str(messages[0])

    submission = DynamicFormSubmission.objects.create(
        form=form,
        data=data,
        creator=approval.creator,
        modifier=approval.creator,
    )
    logger.info(
        "dform submission auto created by approval. approval:%s form:%s submission:%s",
        approval.pk,
        form.pk,
        submission.pk,
    )
    return True, None


def register_approval_handlers():
    """注册「审批通过后自动完成」的动作（app ready 时调用，可重复执行）。"""
    from system.utils.approval import register_on_approved

    register_on_approved(r"^/api/system/dynamic-form-submissions/?$", submit_from_approval)


def sync_dform_instance(instance, status, reason: str = "") -> None:
    """流程实例终态回写表单提交状态：由信号接收器调用（幂等）。"""
    from system.models.approval import ApprovalInstance

    if getattr(instance, "biz_type", "") != DFORM_BIZ_TYPE or not instance.biz_id:
        return
    status = str(status)
    if status not in dict(ApprovalInstance.Status.choices):
        return

    submission = DynamicFormSubmission.objects.filter(pk=instance.biz_id).first()
    if submission is None:
        logger.warning(
            "dform submission sync skipped, business row missing. instance:%s biz_id:%s",
            instance.pk,
            instance.biz_id,
        )
        return
    if submission.status == status and submission.instance_id == instance.pk:
        return
    submission.status = status
    submission.instance = instance
    submission.save(update_fields=["status", "instance", "updated_time"])
    logger.info(
        "dform submission status synced by approval instance. submission:%s status:%s reason:%s",
        submission.pk,
        status,
        reason,
    )
