#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""流程发起期辅助：无候选原因文案与实例抄送人解析。

两者都只在发起链路使用（engine.create_instance），不依赖引擎状态；
独立成模块以保持 engine 在行数门禁内。
"""

from typing import Any

from django.utils.translation import gettext_lazy as _

from .constants import _users


def _no_approver_detail(node: Any, applicant: Any) -> str:
    """节点无候选时的失败原因（含解决路径）：发起校验 fail-closed 的用户可读提示。

    leader 节点区分子场景给出可操作建议（申请人无部门 / 部门无负责人 / 负责人即
    申请人本人）；post 节点区分「岗位不存在或停用」与「岗位无在岗成员」；其余
    （角色无成员、指定用户不存在、表单字段未解析出用户名、委托展开后为空等）按
    节点审批人配置排查。仅做提示文案，不改变 fail-closed 语义。
    """
    if node.assignee_type == node.AssigneeType.LEADER:
        dept = getattr(applicant, "dept", None)
        if dept is None:
            return str(
                _(
                    "Node {} has no available approver: the applicant has no department yet. "
                    "Please assign a department with leader to the applicant first"
                )
            ).format(node.name)
        if getattr(dept, "leader", None) is None:
            return str(
                _(
                    "Node {} has no available approver: the applicant's department has no leader. "
                    "Please configure a department leader first"
                )
            ).format(node.name)
        if dept.leader_id == applicant.pk:
            return str(
                _(
                    "Node {} has no available approver: the applicant is the department leader. "
                    "Please adjust the department leader or the approver of this node"
                )
            ).format(node.name)
    if node.assignee_type == node.AssigneeType.POST:
        from identity.models import Post

        codes = [
            value.strip() for value in str(node.assignee_value or "").replace("，", ",").split(",") if value.strip()
        ]
        posts = Post.objects.filter(code__in=codes, is_active=True, deleted_at__isnull=True)
        if not posts.exists():
            return str(
                _(
                    "Node {} has no available approver: the configured posts do not exist or are disabled. "
                    "Please check the post configuration of this node"
                )
            ).format(node.name)
        if not _users().objects.filter(is_active=True, posts__in=posts).exists():
            return str(
                _(
                    "Node {} has no available approver: no active user holds the configured posts. "
                    "Please assign members to the posts first"
                )
            ).format(node.name)
    return str(_("Node {} has no available approver. Please check the approver configuration of this node")).format(
        node.name
    )


def _resolve_instance_cc(path: Any, applicant: Any, extra: Any = None) -> Any:
    """实例抄送人：全部可达节点 cc 并集 + 发起时追加（去重、仅启用用户、不含申请人）。

    标识兼容「用户 pk」与「用户名」两种形态：设计器节点与发起弹窗可直接沿用
    审批人选择器的用户名，API 调用方可传 pk；非法标识静默跳过（抄送为附加能力，
    不阻断发起）。标识集合一次批量解析（pk 与用户名各走同一查询），不逐标识查库，
    逐标识仍保持「主键优先、用户名兜底」的解析语义。
    """
    from django.db.models import Q

    UserInfo = _users()

    identifiers = []
    for node in path or []:
        for item in node.cc_users or []:
            text = str(item or "").strip()
            if text and text not in identifiers:
                identifiers.append(text)
    for item in extra or []:
        text = str(item or "").strip()
        if text and text not in identifiers:
            identifiers.append(text)
    if not identifiers:
        return []
    identifiers = identifiers[:20]
    pk_field = UserInfo._meta.pk
    pk_values, names = [], []
    for text in identifiers:
        try:
            pk_values.append(pk_field.to_python(text))
        except Exception:  # noqa: BLE001 非主键形态（用户名）走名称匹配
            names.append(text)
    if not pk_values and not names:
        return []
    query = Q()
    if pk_values:
        query |= Q(pk__in=pk_values)
    if names:
        query |= Q(username__in=names)
    by_pk: dict[str, Any] = {}
    by_name: dict[str, Any] = {}
    for candidate in UserInfo.objects.filter(query):
        by_pk[candidate.pk] = candidate
        by_name.setdefault(candidate.username, candidate)

    users = []
    for text in identifiers:
        user = None
        try:
            user = by_pk.get(pk_field.to_python(text))
        except Exception:  # noqa: BLE001 非主键形态（用户名）走下方兜底
            user = None
        if user is None:
            user = by_name.get(text)
        if user and user.is_active and user.pk != applicant.pk and user not in users:
            users.append(user)
    return users
