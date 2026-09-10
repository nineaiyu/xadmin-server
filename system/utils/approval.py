#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""敏感操作审批：判定 / 建单 / 消费令牌 / 审批人解析（纯函数，单测主战场）。

调用入口在 common/core/approval.py 的 ApprovalRequired 装饰器（挂在需要审批的
action 上，DRF dispatch 在权限校验之后执行 handler，装饰器因此晚于权限生效）。

协议（沿用 MFA 412 语义，业务码 1002）：
- 未携带 approval_id 且命中拦截：建 PENDING 单，返回 HTTP 412 +
  ``{"code": 1002, "type": "approval_required", "data": {"approval_id": ...}}``，
  业务代码不执行；
- 携带 approval_id：校验「属主 + 状态 APPROVED + 未消费 + 未过期 + 请求指纹
  （method + path + 脱敏 body）一致」→ 消费放行（consume_time 落值，一次性）；
  仍 PENDING → 再次返回 1002；其余一律 403。

注意：1002 在本项目是通用「业务失败」码（上传类型错误/验证码错误等也在用），
前端分流审批必须同时看 HTTP 412 与 ``type=approval_required``，不能只凭 code。
"""

import json
import re
import uuid

from django.utils.translation import gettext_lazy as _
from rest_framework.exceptions import ValidationError

from common.core.response import ApiResponse
from common.utils import get_logger

logger = get_logger(__name__)

# 前端重发请求时携带令牌的请求头；query 参数 approval_id 作为兜底
APPROVAL_HEADER = "X-Approval-Id"
APPROVAL_QUERY_PARAM = "approval_id"
# 拦截响应的协议标识（前端 http 层按 type 分流，业务码 1002 表示待审批）
APPROVAL_RESPONSE_TYPE = "approval_required"
APPROVAL_PENDING_CODE = 1002

# 重复提交节流窗口（秒），仿 maybe_alert_sensitive_operation 的 cache.add 原子占位
APPROVAL_NOTIFY_THROTTLE_SECONDS = 60


def canonical_params(params) -> str:
    """请求体快照的规范化 JSON（排序键 + 固定默认值），用于一致性比对。"""
    return json.dumps(params, ensure_ascii=False, sort_keys=True, default=str)


def get_request_params(request):
    """取脱敏后的请求体快照：dict 走 desensitize_body，其余（list/字符串）原样。

    multipart（"multipart/form-data" 哨兵串）场景不开放审批：文件无法进快照，
    登记为边界（拦截判定时不会比对出有效指纹）。
    """
    from common.core.middleware import desensitize_body

    body = getattr(request, "request_data", None)
    if body is None:
        body = getattr(request, "data", None)
    if isinstance(body, dict):
        return desensitize_body(body)
    if body is None:
        return {}
    return body


def get_request_object_pk(view) -> str:
    """detail 路由的对象主键（pk 兜底 id），list 路由返回 None。"""
    kwargs = getattr(view, "kwargs", None) or {}
    value = kwargs.get("pk") or kwargs.get("id")
    return str(value) if value is not None else None


def path_intercepted(path: str) -> bool:
    """全局清单判定（APPROVAL_REQUIRED_PATHS，默认空 = 审批整体休眠）。

    与 SENSITIVE_OPERATION_PATHS 同口径：正则清单命中即拦截；非法正则跳过
    并告警（不 500、不放任整个清单失效）。
    """
    from common.core.config import SysConfig

    paths = SysConfig.APPROVAL_REQUIRED_PATHS or []
    if not paths:
        return False
    matched = False
    for pattern in paths:
        if not pattern:
            continue
        try:
            if re.search(pattern, path):
                matched = True
                break
        except re.error:
            logger.warning("approval interception skipped: invalid path regex %s", pattern)
            continue
    return matched


def get_approver_queryset():
    """可审批人集合：角色清单（APPROVAL_APPROVER_ROLES，角色 code）限定，空 = 全部在用超管。"""
    from common.core.config import SysConfig
    from system.models import UserInfo

    role_codes = SysConfig.APPROVAL_APPROVER_ROLES or []
    if role_codes:
        return UserInfo.objects.filter(is_active=True, roles__is_active=True, roles__code__in=role_codes).distinct()
    return UserInfo.objects.filter(is_superuser=True, is_active=True)


def resolve_approvers(applicant):
    """审批人集合 = 可审批人 - 申请人本人（不能自审）。结果为空时调用方必须拒绝建单。"""
    return get_approver_queryset().exclude(pk=applicant.pk)


def can_approve(user) -> bool:
    """审批权限：超管或属可审批人集合；申请人任何时候不能审批自己的单。"""
    if not (user and user.is_authenticated):
        return False
    return get_approver_queryset().filter(pk=user.pk).exists()


def build_module(view) -> str:
    """module 取视图 docstring 首行（同操作日志口径），缺省回退「敏感操作」。"""
    from common.core.utils import get_doc_first_line

    return get_doc_first_line(getattr(view, "__doc__", None)) or _("Sensitive operation")


def find_active_pending(applicant, method, path, params):
    """同指纹的在途 PENDING 单（防止重复点提交刷屏建单）。"""
    from system.models.approval import ApprovalRequest

    digest = canonical_params(params)
    for rec in (
        ApprovalRequest.objects.filter(
            creator=applicant, method=method, path=path, status=ApprovalRequest.Status.PENDING
        )
        .order_by("-created_time")[:10]
        .iterator()
    ):
        if canonical_params(rec.params) == digest:
            return rec
    return None


def notify_approvers(approval, approvers):
    """通知全部审批人（60s 节流，防重复提交刷屏）。"""
    from django.core.cache import cache

    from system.notifications import ApprovalRequestMessage

    if not cache.add(f"approval_notify_{approval.pk}", 1, APPROVAL_NOTIFY_THROTTLE_SECONDS):
        return
    for user in approvers:
        try:
            ApprovalRequestMessage(user, "submitted", approval).publish(is_async=True)
        except Exception:
            logger.warning("send approval notify failed. approval:%s user:%s", approval.pk, user.pk, exc_info=True)


def notify_applicant(approval, event: str):
    """向申请人推送审批结果（通过/驳回）。"""
    from system.notifications import ApprovalRequestMessage

    if not approval.creator:
        return
    try:
        ApprovalRequestMessage(approval.creator, event, approval).publish(is_async=True)
    except Exception:
        logger.warning("send approval result failed. approval:%s event:%s", approval.pk, event, exc_info=True)


def pending_response(approval) -> ApiResponse:
    """待审批协议响应：HTTP 412 + 业务码 1002 + type=approval_required。"""
    return ApiResponse(
        code=APPROVAL_PENDING_CODE,
        status=412,
        detail=_("Operation submitted for approval (No. {}), please retry after it is approved").format(
            str(approval.pk)[:8].upper()
        ),
        data={"approval_id": str(approval.pk), "status": approval.status},
        type=APPROVAL_RESPONSE_TYPE,
    )


def forbidden_response(detail: str) -> ApiResponse:
    return ApiResponse(code=403, status=403, detail=detail, type=APPROVAL_RESPONSE_TYPE)


def create_approval(view, request):
    """建 PENDING 单并通知审批人；无可用审批人时直接报错（避免永久 PENDING）。"""
    from system.models.approval import ApprovalRequest

    approvers = resolve_approvers(request.user)
    if not approvers.exists():
        raise ValidationError(_("No available approver, please contact the administrator to configure approvers"))

    params = get_request_params(request)
    approval = ApprovalRequest.objects.create(
        module=build_module(view)[:64],
        method=request.method,
        path=request.path,
        object_pk=get_request_object_pk(view),
        params=params,
        creator=request.user,
    )
    notify_approvers(approval, approvers)
    return approval


def consume_approval(request, approval_id):
    """消费审批令牌：返回 None 表示放行（调用方继续执行业务），否则返回协议响应。"""
    from django.utils import timezone

    from system.models.approval import ApprovalRequest

    # 令牌来自请求头，非法 UUID 若直接进 filter 会抛 Django ValidationError →
    # 未被 DRF 异常处理器覆盖而返回 500（同 online.py 强制下线的 uuid 解析守护）
    try:
        approval_pk = uuid.UUID(str(approval_id))
    except (AttributeError, TypeError, ValueError):
        return forbidden_response(_("Invalid approval token"))
    approval = ApprovalRequest.objects.filter(pk=approval_pk).first()
    if approval is None:
        return forbidden_response(_("Invalid approval token"))
    if approval.creator_id != request.user.pk:
        return forbidden_response(_("The approval token does not belong to the current user"))

    # 请求指纹一致性：防止「批 A、B」的令牌被拿去执行「C、D」
    fingerprint_ok = (
        approval.method == request.method
        and approval.path == request.path
        and canonical_params(approval.params) == canonical_params(get_request_params(request))
    )
    if not fingerprint_ok:
        approval.status = ApprovalRequest.Status.FAILED
        approval.reason = _("Resent request does not match the approved snapshot")
        approval.save(update_fields=["status", "reason", "updated_time"])
        return forbidden_response(_("The resent request does not match the approved content"))

    if approval.status == ApprovalRequest.Status.PENDING:
        # 审批仍在途：原样返回令牌（不重复建单、不重复通知）
        return pending_response(approval)

    if approval.status != ApprovalRequest.Status.APPROVED:
        detail = _("The approval has been {}").format(approval.get_status_display())
        if approval.status == ApprovalRequest.Status.REJECTED and approval.reason:
            detail = _("The approval was rejected: {}").format(approval.reason)
        return forbidden_response(detail)

    if approval.consume_time is not None:
        return forbidden_response(_("The approval token has already been used"))
    if approval.expired_at and approval.expired_at < timezone.now():
        approval.status = ApprovalRequest.Status.EXPIRED
        approval.save(update_fields=["status", "updated_time"])
        return forbidden_response(_("The approval token has expired"))

    # 原子消费（一次性令牌）：条件更新 + rowcount 判定，防并发重发双消费。
    # 不用 select_for_update：sqlite（单测/E2E 库）不支持 SELECT ... FOR UPDATE，
    # 条件更新在全部后端语义一致
    now = timezone.now()
    consumed = ApprovalRequest.objects.filter(
        pk=approval.pk,
        status=ApprovalRequest.Status.APPROVED,
        consume_time__isnull=True,
    ).update(consume_time=now, updated_time=now)
    if not consumed:
        return forbidden_response(_("The approval token has already been used"))
    return None


def process_approval(view, request):
    """装饰器主入口：返回 None 放行业务，否则返回协议响应（412/403）。

    全局清单为空时整体休眠（渐进启用），已携令牌的重发请求在休眠期直接放行。
    """
    if not path_intercepted(request.path):
        return None

    approval_id = request.headers.get(APPROVAL_HEADER) or request.query_params.get(APPROVAL_QUERY_PARAM)
    if approval_id:
        return consume_approval(request, approval_id)

    approval = find_active_pending(request.user, request.method, request.path, get_request_params(request))
    if approval is None:
        approval = create_approval(view, request)
    return pending_response(approval)


def approve_request(approval, user):
    """审批通过：置 APPROVED + 令牌有效期（APPROVAL_TOKEN_TTL）。返回 (ok, detail)。"""
    import datetime

    from django.utils import timezone

    from common.core.config import SysConfig
    from system.models.approval import ApprovalRequest

    if approval.status != ApprovalRequest.Status.PENDING:
        return False, _("Only pending requests can be approved")
    if approval.creator_id == user.pk:
        return False, _("The applicant cannot approve their own request")
    approval.status = ApprovalRequest.Status.APPROVED
    approval.approver = user
    approval.approved_at = timezone.now()
    approval.expired_at = approval.approved_at + datetime.timedelta(seconds=int(SysConfig.APPROVAL_TOKEN_TTL))
    approval.save(update_fields=["status", "approver", "approved_at", "expired_at", "updated_time"])
    notify_applicant(approval, "approved")
    return True, None


def reject_request(approval, user, reason: str):
    """驳回：reason 必填。返回 (ok, detail)。"""
    from django.utils import timezone

    from system.models.approval import ApprovalRequest

    if approval.status != ApprovalRequest.Status.PENDING:
        return False, _("Only pending requests can be rejected")
    if approval.creator_id == user.pk:
        return False, _("The applicant cannot approve their own request")
    approval.status = ApprovalRequest.Status.REJECTED
    approval.approver = user
    approval.approved_at = timezone.now()
    approval.reason = (reason or "")[:255]
    approval.save(update_fields=["status", "approver", "approved_at", "reason", "updated_time"])
    notify_applicant(approval, "rejected")
    return True, None


def cancel_request(approval, user):
    """申请人撤回：仅本人、仅 PENDING。返回 (ok, detail)。"""
    from system.models.approval import ApprovalRequest

    if approval.creator_id != user.pk:
        return False, _("Only the applicant can cancel the request")
    if approval.status != ApprovalRequest.Status.PENDING:
        return False, _("Only pending requests can be cancelled")
    approval.status = ApprovalRequest.Status.CANCELLED
    approval.save(update_fields=["status", "updated_time"])
    return True, None


def expire_pending_approvals(pending_days: int = None) -> int:
    """PENDING 超时置 EXPIRED（APPROVAL_PENDING_TIMEOUT，默认 3 天，清理任务调用）。"""
    import datetime

    from django.utils import timezone

    from common.core.config import SysConfig
    from system.models.approval import ApprovalRequest

    if pending_days is None:
        pending_days = int(SysConfig.APPROVAL_PENDING_TIMEOUT)
    if not pending_days or pending_days <= 0:
        return 0
    deadline = timezone.now() - datetime.timedelta(days=pending_days)
    count = ApprovalRequest.objects.filter(status=ApprovalRequest.Status.PENDING, created_time__lt=deadline).update(
        status=ApprovalRequest.Status.EXPIRED, updated_time=timezone.now()
    )
    return count


def clean_expired_approvals(keep_days: int = None, batch_size: int = 2000) -> int:
    """清理超过保留期的审批单（APPROVAL_KEEP_DAYS，默认 180 天，分批删）。"""
    import datetime

    from django.db import transaction
    from django.utils import timezone

    from common.core.config import SysConfig
    from system.models.approval import ApprovalRequest

    if keep_days is None:
        keep_days = int(SysConfig.APPROVAL_KEEP_DAYS)
    if not keep_days or keep_days <= 0:
        return 0
    deadline = timezone.now() - datetime.timedelta(days=keep_days)
    total = 0
    while True:
        pks = list(ApprovalRequest.objects.filter(created_time__lt=deadline).values_list("pk", flat=True)[:batch_size])
        if not pks:
            break
        with transaction.atomic():
            deleted, _rows = ApprovalRequest.objects.filter(pk__in=pks).delete()
        total += deleted
    return total
