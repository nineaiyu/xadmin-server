#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""敏感操作审批：多级审批链（规则匹配 / 级次快照 / 当前级助手）。

与流程引擎（ApprovalFlow）的分工：本模块只服务「拦截式审批」——保留 412 + 一次性
令牌协议，用显式的有序级次替代全局审批人集合；未命中规则的单保持单级扁平语义。
"""

import re

from django.utils.translation import gettext_lazy as _


def resolve_rule(path: str):
    """按请求路径匹配启用的审批规则：priority 大者优先，并列取创建时间新者。

    未命中返回 None（调用方回退全局审批人逻辑）；非法正则跳过不中断（与
    APPROVAL_REQUIRED_PATHS 的容错口径一致）。
    """
    from system.models import ApprovalRule

    if not path:
        return None
    queryset = ApprovalRule.objects.filter(is_active=True).prefetch_related("levels")
    for rule in queryset.order_by("-priority", "-created_time"):
        for pattern in rule.path_patterns or []:
            if not pattern:
                continue
            try:
                if re.search(str(pattern), path):
                    return rule
            except re.error:
                continue
    return None


def resolve_level_users(level):
    """级次候选人：user=用户名清单，role=角色 code 清单（仅启用账号，两者均支持逗号多值）。"""
    from system.models import UserInfo

    values = [value.strip() for value in str(level.assignee_value or "").split(",") if value.strip()]
    if not values:
        return UserInfo.objects.none()
    if level.assignee_type == "role":
        return UserInfo.objects.filter(
            is_active=True,
            roles__is_active=True,
            roles__deleted_at__isnull=True,
            roles__code__in=values,
        ).distinct()
    return UserInfo.objects.filter(is_active=True, username__in=values)


def build_steps(rule, applicant):
    """规则级次 → 建单快照数据；任一级无可用人（或仅申请人）＝ fail-closed。

    返回 ``(steps, error)``：steps 为 ``[{"order","name","assignee_type",
    "assignee_value","users"}]``；error 为可读文案（成功为 None）。
    """
    levels = list(rule.levels.order_by("order"))
    if not levels:
        return None, _("Approval rule {name} has no approval levels").format(name=rule.name)
    steps = []
    for level in levels:
        # 申请人不能自审：剔除后为空即该级无人可审（与全局审批人口径一致）
        users = [user for user in resolve_level_users(level) if user.pk != applicant.pk]
        if not users:
            return None, _("No available approver for level {order} of rule {name}").format(
                order=level.order, name=rule.name
            )
        steps.append(
            {
                "order": level.order,
                "name": level.name or "",
                "approve_type": level.approve_type,
                "assignee_type": level.assignee_type,
                "assignee_value": level.assignee_value or "",
                "users": users,
            }
        )
    return steps, None


def create_steps(approval, steps):
    """按快照数据落级次行并绑定候选人（建单路径专用）。"""
    from system.models import ApprovalRequestStep

    rows = []
    for item in steps:
        row = ApprovalRequestStep.objects.create(
            request=approval,
            name=item["name"],
            order=item["order"],
            approve_type=item.get("approve_type") or "OR",
            assignee_type=item["assignee_type"],
            assignee_value=item["assignee_value"],
        )
        row.assignees.set(item["users"])
        rows.append(row)
    return rows


def current_step(approval):
    """当前级 = 最早的 PENDING 级次（无链或已结束返回 None）。"""
    return approval.steps.filter(status="PENDING").order_by("order").first()


def can_act(approval, user) -> bool:
    """当前级候选人判定（扁平单恒 False：由全局审批人逻辑负责授权）。

    会签（AND）级已通过的人不再可操作（重复点击由动作层唯一约束兜底）：
    让「通过」按钮对已处理者自然消失，避免点了才报错。
    """
    if not (user and getattr(user, "is_authenticated", False)):
        return False
    if (approval.current_level or 0) <= 0:
        return False
    if not approval.current_assignees.filter(pk=user.pk).exists():
        return False
    step = current_step(approval)
    if step is not None and step.approve_type == "AND":
        return not step.actions.filter(approver=user).exists()
    return True


def sync_current_level(approval, step=None):
    """同步主单的当前级投影（列表展示与待办查询的唯一数据源）。

    权威数据在 steps 表；current_level/current_assignees 只是投影，必须在
    建单 / 逐级推进 / 终态（通过、驳回、撤回、过期）四处同步，否则会出现
    「列表显示有当前级、但没人能审」或反之的漂移。
    """
    from system.models import ApprovalRequest

    if step is None:
        ApprovalRequest.objects.filter(pk=approval.pk).update(current_level=0)
        approval.current_assignees.clear()
        approval.current_level = 0
        return
    ApprovalRequest.objects.filter(pk=approval.pk).update(current_level=step.order)
    approval.current_assignees.set(step.assignees.all())
    approval.current_level = step.order
