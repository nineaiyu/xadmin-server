#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""动态表单 × 审批流程引擎联动。

表单绑定审批流程后，提交进入流程引擎（多级审批），实例终态经
``approval_instance_finished`` 信号回写提交状态；被驳回的提交允许申请人
修改数据后重新提交（重新发起流程实例）。未绑定流程的表单不受影响。

并发收敛：发起前按「源状态」做条件更新占位（CAS）并整体事务化——双击 / 重放
只有一次能推进到 PENDING，失败路径不留「状态与实例不一致」的行。
"""

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from common.utils import get_logger
from dataset.models.dform import DynamicForm, DynamicFormSubmission

logger = get_logger(__name__)

# 业务标识：ApprovalInstance.biz_type / approval_instance_finished 分发键
DFORM_BIZ_TYPE = "dform_submission"

# dform 控件类型 → 流程表单字段类型（流程侧 FORM_FIELD_TYPES 收敛集：
# text/textarea/number/date/select；投影仅供发起校验与独立发起页渲染）
_FLOW_FIELD_TYPES = {
    "input": "text",
    "textarea": "textarea",
    "number": "number",
    "amount": "number",
    "date": "date",
    "daterange": "date",
    "select": "select",
    "radio": "select",
    "checkbox": "select",
}


class _FlowRollback(Exception):
    """内部信号：本次发起流程实例的尝试需要整体回滚（失败原因即 detail）。"""


def flow_referenced_keys(flow) -> set:
    """流程**当前生效定义**引用的表单字段 key 集合（三类引用）：

    节点条件（condition.field）、分支路由条件（routes[].condition.field）、
    FIELD 型审批人（assignee_value 即字段 key）。默认管理器只暴露当前生效行
    ，历史版本行不参与——在途单按钉住版本的行推进，不受影响。
    """
    keys: set = set()
    for node in flow.nodes.all():
        condition = node.condition if isinstance(node.condition, dict) else {}
        if condition.get("field"):
            keys.add(str(condition["field"]))
        for route in node.routes or []:
            if isinstance(route, dict) and isinstance(route.get("condition"), dict) and route["condition"].get("field"):
                keys.add(str(route["condition"]["field"]))
        if node.assignee_type == node.AssigneeType.FIELD and str(node.assignee_value or "").strip():
            keys.add(str(node.assignee_value).strip())
    return keys


def assert_schema_safe_for_flow(form, new_schema) -> None:
    """schema 实质变更的流程引用检查：被绑定流程引用的字段不可删除。

    在途单已按版本钉住定义，本检查保护的是**改版后新建/重提**的实例：
    引用字段被删后条件恒 False、节点被静默跳过（FIELD 审批人解析为空时节点甚至
    自动通过），审批路径无声改变。先调整流程（或保留字段）再改表单。
    """
    if form.approval_flow_id is None:
        return
    flow = form.approval_flow
    if flow is None:
        return
    old_keys = {
        str(item.get("key"))
        for item in (form.schema or {}).get("fields") or []
        if isinstance(item, dict) and item.get("key")
    }
    new_keys = {
        str(item.get("key"))
        for item in (new_schema or {}).get("fields") or []
        if isinstance(item, dict) and item.get("key")
    }
    blocked = sorted(flow_referenced_keys(flow) & (old_keys - new_keys))
    if blocked:
        raise ValidationError(
            str(_("Fields referenced by the approval flow cannot be removed: {}").format(", ".join(blocked)))
        )


def project_flow_form_schema(flow) -> list:
    """绑定表单 → flow.form_schema 单向投影（dform 是唯一事实源）。

    字段集合 = 全部**未删除**绑定表单（按创建序）的 schema 字段并集：同 key 以
    最早表单为准，控件类型映射到流程侧收敛集。投影后同步刷新最新版本快照里的
    form_schema（快照仅审计/回滚用，节点推进不读它——在途单不受影响；刷新是为
    避免后续纯节点改版被误判「定义变更」多落版本）。
    """
    fields: list = []
    seen: set = set()
    for form in flow.bound_forms.filter(is_template=False).order_by("created_time"):
        for item in (form.schema or {}).get("fields") or []:
            if not isinstance(item, dict):
                continue
            key = str(item.get("key") or "")
            if not key or key in seen:
                continue
            seen.add(key)
            ftype = _FLOW_FIELD_TYPES.get(str(item.get("type") or ""), "text")
            options: list = []
            if ftype == "select":
                from dataset.utils.dform import field_option_values

                options = [str(value) for value in field_option_values(item)]
            fields.append(
                {
                    "key": key,
                    "label": str(item.get("label") or key),
                    "type": ftype,
                    "required": bool(item.get("required")),
                    "options": options,
                }
            )
    flow.form_schema = fields
    flow.save(update_fields=["form_schema", "updated_time"])
    _refresh_latest_snapshot_form_schema(flow)
    return fields


def _refresh_latest_snapshot_form_schema(flow) -> None:
    """最新版本快照的 form_schema 对齐当前投影（无快照/无变化跳过）。"""
    import json

    from approval.models.approval import ApprovalFlowVersion

    row = ApprovalFlowVersion.objects.filter(flow=flow).order_by("-version").first()
    if row is None:
        return
    snapshot = json.loads(json.dumps(row.snapshot or {}))
    if snapshot.get("form_schema") == flow.form_schema:
        return
    snapshot["form_schema"] = flow.form_schema
    row.snapshot = snapshot
    row.save(update_fields=["snapshot", "updated_time"])


def sync_bound_flow_schema(form) -> None:
    """表单保存（新建/改版/绑定变化）后同步绑定流程的 form_schema 投影。"""
    if form.approval_flow_id is None:
        return
    flow = form.approval_flow
    if flow is not None:
        project_flow_form_schema(flow)


def resync_flow_after_unbind(previous_flow_id) -> None:
    """表单解绑/删除后的流程侧再同步：仍有其他绑定表单才重投影（保留投影结果）。

    最后一个绑定表单移除后流程 form_schema 保持原样（不静默清空）：编辑锁随
    绑定解除而释放，管理员可手动改写或直接停用该流程。
    """
    if not previous_flow_id:
        return
    from approval.models.approval import ApprovalFlow

    flow = ApprovalFlow.objects.filter(pk=previous_flow_id).first()
    if flow is None or not flow.bound_forms.filter(is_template=False).exists():
        return
    project_flow_form_schema(flow)


def build_instance_title(form, applicant) -> str:
    """流程实例标题：表单名 + 提交人，便于审批列表一眼区分。"""
    username = getattr(applicant, "nickname", "") or getattr(applicant, "username", "")
    return f"{form.name}（{username}）" if username else str(form.name)


def create_flow_instance(submission, applicant):
    """为绑定流程的表单提交创建流程实例并置 PENDING。返回 (ok, detail)。

    并发安全（双击 / 重放）：以「读取时的源状态」做条件更新（CAS）把提交行推进到
    PENDING——只有一次尝试能占到（Postgres 下调用方另有行锁），失败路径整体回滚
    本次尝试的写入（含刚创建的实例），不留下状态与实例不一致的提交行。
    """
    from approval.utils.approval_flow import create_instance

    form = submission.form
    if form.approval_flow_id is None:
        return False, str(_("The form is not bound to an approval flow"))
    flow = form.approval_flow
    if flow is None:
        return False, str(_("The approval flow does not exist"))
    if not flow.is_active:
        return False, str(_("The approval flow is not active"))

    source_status = submission.status
    try:
        with transaction.atomic():
            # 先 CAS 占位再发起：占不到说明并发请求已推进，不创建实例（无孤儿实例/无幻影通知）；
            # 发起失败则整体回滚（CAS 一并还原，行保持可重试的源状态）
            advanced = DynamicFormSubmission.objects.filter(pk=submission.pk, status=source_status).update(
                status=DynamicFormSubmission.Status.PENDING,
                updated_time=timezone.now(),
            )
            if not advanced:
                raise _FlowRollback(str(_("The submission has already been submitted")))
            instance, error = create_instance(
                flow=flow,
                applicant=applicant,
                title=build_instance_title(form, applicant),
                form_data=submission.data or {},
                biz_type=DFORM_BIZ_TYPE,
                biz_id=str(submission.pk),
            )
            if error:
                raise _FlowRollback(error)
            DynamicFormSubmission.objects.filter(pk=submission.pk).update(
                instance=instance, updated_time=timezone.now()
            )
            submission.instance = instance
            submission.status = DynamicFormSubmission.Status.PENDING
    except _FlowRollback as exc:
        return False, str(exc)
    return True, None


def resubmit_submission(submission, user):
    """被驳回后重新提交：仅申请人、仅 REJECTED；按当前数据发起新流程实例。返回 (ok, detail)。

    行锁内复核状态：并发重放也只有一次能推进（配合 create_flow_instance 的源状态 CAS）。
    重校验：驳回后表单可能已改版（新增必填/删除字段），重提按**当前 schema** 完整校验——
    否则旧数据可绕过新增必填直达流程引擎（引擎 validate_form 只看流程侧 form_schema）。
    """
    from dataset.utils.dform import validate_submission_data
    from dataset.utils.dform_filter import build_filter_data

    with transaction.atomic():
        locked = (
            DynamicFormSubmission.objects.select_for_update().select_related("form").filter(pk=submission.pk).first()
        )
        if locked is None:
            return False, str(_("The submission does not exist"))
        if locked.creator_id != user.pk and not getattr(user, "is_superuser", False):
            return False, str(_("Only the applicant can resubmit the submission"))
        if locked.status != DynamicFormSubmission.Status.REJECTED:
            return False, str(_("Only rejected submissions can be resubmitted"))
        if not locked.form.is_active:
            return False, str(_("This form is no longer accepting submissions"))
        try:
            # upload 归属按申请人断言（超管代重提时文件仍属原申请人）
            locked.data = validate_submission_data(locked.form.schema, locked.data or {}, user=locked.creator)
        except ValidationError as exc:
            messages = getattr(exc, "messages", None) or [str(exc)]
            return False, str(messages[0])
        locked.filter_data = build_filter_data(locked.form.schema, locked.data)
        locked.save(update_fields=["data", "filter_data", "updated_time"])
        return create_flow_instance(locked, user)


def submit_from_approval(approval, user):
    """审批通过后自动落库：按审批单快照重建表单提交。返回 (ok, detail)。

    申请人取审批单 creator（不是审批人）；此处只覆盖「操作审批」链路——绑定流程的
    表单提交走流程引擎，不会生成这类审批单。
    """
    from dataset.utils.dform import validate_submission_data
    from dataset.utils.dform_filter import build_filter_data

    payload = approval.payload or {}
    form = DynamicForm.objects.filter(pk=payload.get("form") or "", is_active=True).first()
    if form is None:
        return False, str(_("The form does not exist or is no longer accepting submissions"))
    try:
        # 数据来自申请人提交时的快照：文件归属按申请人（审批单 creator）判定
        data = validate_submission_data(form.schema, payload.get("data") or {}, user=approval.creator)
    except ValidationError as exc:
        messages = getattr(exc, "messages", None) or [str(exc)]
        return False, str(messages[0])

    submission = DynamicFormSubmission.objects.create(
        form=form,
        data=data,
        filter_data=build_filter_data(form.schema, data),
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


def update_from_approval(approval, user):
    """草稿提交经操作审批通过后自动完成：更新既有提交行（不重复建行）。返回 (ok, detail)。

    与「新建提交」链路的区别：目标行已存在（草稿），审批通过 = 提交生效，
    因此按审批快照重校验数据后把状态从 DRAFT 落为已生效；行已提交（幂等重放）视为完成。
    """
    from dataset.utils.dform import validate_submission_data
    from dataset.utils.dform_filter import build_filter_data

    payload = approval.payload or {}
    submission = DynamicFormSubmission.objects.filter(pk=approval.object_pk or "").first()
    if submission is None:
        return False, str(_("The submission does not exist"))
    if submission.creator_id != approval.creator_id:
        return False, str(_("The approval token does not belong to the current user"))
    if submission.status != DynamicFormSubmission.Status.DRAFT:
        # 已提交/已进入其他状态：视为已完成（幂等）
        return True, None
    form = submission.form
    if not form.is_active:
        return False, str(_("This form is no longer accepting submissions"))
    try:
        data = validate_submission_data(form.schema, payload.get("data") or submission.data, user=submission.creator)
    except ValidationError as exc:
        messages = getattr(exc, "messages", None) or [str(exc)]
        return False, str(messages[0])

    submission.data = data
    submission.filter_data = build_filter_data(form.schema, data)
    submission.status = ""
    submission.save(update_fields=["data", "filter_data", "status", "updated_time"])
    logger.info("dform draft submitted by approval. approval:%s submission:%s", approval.pk, submission.pk)
    return True, None


def register_approval_handlers():
    """注册「审批通过后自动完成」的动作（app ready 时调用，可重复执行）。

    两条链路：新建提交（POST 列表）与草稿提交（POST {pk}/submit）——后者更新既有行。
    """
    from approval.utils.approval import register_on_approved

    register_on_approved(r"^/api/dataset/dynamic-form-submissions/?$", submit_from_approval)
    register_on_approved(r"^/api/dataset/dynamic-form-submissions/(?P<pk>[^/.]+)/submit$", update_from_approval)


def sync_dform_instance(instance, status, reason: str = "") -> None:
    """流程实例终态回写表单提交状态：由信号接收器调用（幂等）。"""
    from approval.models.approval import ApprovalInstance

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
