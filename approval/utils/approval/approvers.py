#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""敏感操作审批：审批人解析与在途单查找。"""

from django.utils.translation import gettext_lazy as _

from .payload import canonical_params


def get_approver_queryset():
    """可审批人集合：显式角色清单（APPROVAL_APPROVER_ROLES）∪ 职能权限反查
    （APPROVAL_APPROVER_PERMS，get_users_by_perms 按权限码推导）；两者皆空 = 全部在用超管。

    权限反查覆盖「按职能授权」场景：持有审批相关权限码（如 approve:SystemApprovalRequest）
    的用户即审批人，无需逐个维护角色清单。申请人始终不能自审（resolve_approvers 排除）。
    """
    from common.core.config import SysConfig
    from system.models import UserInfo
    from system.services import get_users_by_perms

    role_codes = SysConfig.APPROVAL_APPROVER_ROLES or []
    perms = SysConfig.APPROVAL_APPROVER_PERMS or []
    if not role_codes and not perms:
        return UserInfo.objects.filter(is_superuser=True, is_active=True)
    queryset = UserInfo.objects.none()
    if role_codes:
        queryset = (
            queryset
            | UserInfo.objects.filter(is_active=True, roles__is_active=True, roles__code__in=role_codes).distinct()
        )
    if perms:
        queryset = queryset | get_users_by_perms(perms)
    return queryset.distinct()


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
    from approval.models.approval import ApprovalRequest

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
