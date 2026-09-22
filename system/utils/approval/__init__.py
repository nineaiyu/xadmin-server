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

本包按职责拆分（constants / approved_actions / payload / approvers / notify /
lifecycle / queries / periodic），对外 API 由本文件统一再导出，导入路径保持
``system.utils.approval`` 不变。
"""

from .approved_actions import ON_APPROVED_HANDLERS, register_on_approved, run_on_approved, snapshot_payload
from .approvers import build_module, can_approve, find_active_pending, get_approver_queryset, resolve_approvers
from .chains import (
    build_steps,
    can_act,
    create_steps,
    current_step,
    resolve_level_users,
    resolve_rule,
    sync_current_level,
)
from .constants import (
    APPROVAL_HEADER,
    APPROVAL_NOTIFY_THROTTLE_SECONDS,
    APPROVAL_PAYLOAD_MAX_SIZE,
    APPROVAL_PENDING_CODE,
    APPROVAL_PENDING_COUNT_CACHE_SECONDS,
    APPROVAL_QUERY_PARAM,
    APPROVAL_REMIND_CACHE_SECONDS,
    APPROVAL_RESPONSE_TYPE,
    APPROVAL_STATS_WINDOW_DAYS,
)
from .lifecycle import (
    approve_request,
    cancel_request,
    consume_approval,
    create_approval,
    process_approval,
    reject_request,
)
from .notify import (
    _emit_approval_event as _emit_approval_event,  # noqa: PLC0414 显式再导出（测试按私有名导入）
)
from .notify import (
    forbidden_response,
    notify_applicant,
    notify_approvers,
    notify_step,
    pending_response,
)
from .payload import canonical_params, get_request_object_pk, get_request_params, path_intercepted
from .periodic import clean_expired_approvals, expire_pending_approvals, remind_pending_approvals
from .queries import approval_stats, invalidate_pending_count_cache, pending_count_for, pending_queryset_for

__all__ = [
    "APPROVAL_HEADER",
    "APPROVAL_NOTIFY_THROTTLE_SECONDS",
    "APPROVAL_PAYLOAD_MAX_SIZE",
    "APPROVAL_PENDING_CODE",
    "APPROVAL_PENDING_COUNT_CACHE_SECONDS",
    "APPROVAL_QUERY_PARAM",
    "APPROVAL_REMIND_CACHE_SECONDS",
    "APPROVAL_RESPONSE_TYPE",
    "APPROVAL_STATS_WINDOW_DAYS",
    "ON_APPROVED_HANDLERS",
    "approval_stats",
    "approve_request",
    "build_module",
    "build_steps",
    "can_act",
    "can_approve",
    "cancel_request",
    "canonical_params",
    "clean_expired_approvals",
    "consume_approval",
    "create_approval",
    "create_steps",
    "current_step",
    "expire_pending_approvals",
    "find_active_pending",
    "forbidden_response",
    "get_approver_queryset",
    "get_request_object_pk",
    "get_request_params",
    "invalidate_pending_count_cache",
    "notify_applicant",
    "notify_approvers",
    "notify_step",
    "path_intercepted",
    "pending_count_for",
    "pending_queryset_for",
    "pending_response",
    "process_approval",
    "register_on_approved",
    "reject_request",
    "remind_pending_approvals",
    "resolve_approvers",
    "resolve_level_users",
    "resolve_rule",
    "run_on_approved",
    "snapshot_payload",
    "sync_current_level",
]
