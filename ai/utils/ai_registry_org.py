#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""AI 声明式动作：组织域补充 + 审批中心 + 站内信。

从 ``ai_api_registry.py`` 拆出仅因行数门禁：本文件只做声明，机制（``api_action`` /
参数解析 / dispatch 执行 / 权限双门）全部在 ``ai_api_actions.py``，汇总仍以
``ai_api_registry.API_ACTION_SPECS`` 为唯一白名单入口。
"""

from django.utils.translation import gettext_lazy as _

from ai.utils.ai_api_actions import IN_QUERY, api_action, requires_approval_high_risk

ACTION_USER_CREATE = "user.create"
ACTION_USER_DETAIL = "user.detail"
ACTION_USER_UNBLOCK = "user.unblock"
ACTION_ONLINE_LIST = "online.list"
ACTION_ONLINE_FORCE_LOGOUT = "online.force_logout"
ACTION_DEPT_LIST = "dept.list"
ACTION_DEPT_CREATE = "dept.create"
ACTION_DEPT_UPDATE = "dept.update"
ACTION_DEPT_DELETE = "dept.delete"
ACTION_ROLE_LIST = "role.list"
ACTION_ROLE_UPDATE = "role.update"
ACTION_ROLE_DELETE = "role.delete"
ACTION_MENU_LIST = "menu.list"
ACTION_PERMISSION_LIST = "permission.list"
ACTION_APPROVAL_LIST = "approval.list"
ACTION_APPROVAL_PENDING_COUNT = "approval.pending_count"
ACTION_APPROVAL_STATS = "approval.stats"
ACTION_APPROVAL_APPROVE = "approval.approve"
ACTION_APPROVAL_REJECT = "approval.reject"
ACTION_MESSAGE_UNREAD_LIST = "message.unread_list"
ACTION_MESSAGE_MARK_ALL_READ = "message.mark_all_read"

ORG_ACTION_SPECS = {
    # ---- 组织域补充：用户 / 在线 / 部门 / 角色 / 菜单 / 数据权限 ----
    ACTION_USER_CREATE: api_action(
        key=ACTION_USER_CREATE,
        label=_("Create a user"),
        description=_("Create a user account with username, initial password and basic profile"),
        method="POST",
        path="/api/identity/user",
        params={
            "username": {"type": "string", "required": True, "in": "body", "description": "Login username (unique)"},
            "password": {
                "type": "string",
                "required": True,
                "in": "body",
                "description": "Initial password (must satisfy the password rules)",
            },
            "nickname": {"type": "string", "required": False, "in": "body", "description": "Display name"},
            "phone": {"type": "string", "required": False, "in": "body", "description": "Phone number"},
            "email": {"type": "string", "required": False, "in": "body", "description": "Email address"},
        },
    ),
    ACTION_USER_DETAIL: api_action(
        key=ACTION_USER_DETAIL,
        label=_("Look up a user's details"),
        description=_("Fetch one user's profile (nickname, contact, dept, roles, active state)"),
        method="GET",
        path="/api/identity/user/<pk>",
        params={
            "pk": {"type": "user", "required": True, "in": "path", "description": "Target user username or nickname"}
        },
    ),
    ACTION_USER_UNBLOCK: api_action(
        key=ACTION_USER_UNBLOCK,
        label=_("Unblock a user"),
        description=_("Release a user from login-block state (after repeated password failures)"),
        method="POST",
        path="/api/identity/user/<pk>/unblock",
        params={
            "pk": {"type": "user", "required": True, "in": "path", "description": "Target user username or nickname"}
        },
    ),
    ACTION_ONLINE_LIST: api_action(
        key=ACTION_ONLINE_LIST,
        label=_("List online users"),
        description=_("Currently online user sessions (user, login type, last active)"),
        method="GET",
        path="/api/identity/online",
        params={},
    ),
    ACTION_ONLINE_FORCE_LOGOUT: api_action(
        key=ACTION_ONLINE_FORCE_LOGOUT,
        label=_("Force a user offline"),
        description=_("Revoke all sessions of one user (server-side token revocation + WebSocket kick)"),
        method="POST",
        path="/api/identity/online/<pk>/force-logout",
        params={
            "pk": {"type": "user", "required": True, "in": "path", "description": "Target user username or nickname"}
        },
    ),
    ACTION_DEPT_LIST: api_action(
        key=ACTION_DEPT_LIST,
        label=_("List departments"),
        description=_("Department tree (name, code, leader, user count)"),
        method="GET",
        path="/api/identity/dept",
        params={},
    ),
    ACTION_DEPT_CREATE: api_action(
        key=ACTION_DEPT_CREATE,
        label=_("Create a department"),
        description=_("Create a department under an optional parent department"),
        method="POST",
        path="/api/identity/dept",
        params={
            "name": {"type": "string", "required": True, "in": "body", "description": "Department name"},
            "code": {"type": "string", "required": False, "in": "body", "description": "Department code"},
            "parent": {
                "type": "pk",
                "required": False,
                "in": "body",
                "description": "Parent department id (top-level if omitted)",
            },
            "description": {"type": "string", "required": False, "in": "body", "description": "Description"},
        },
    ),
    ACTION_DEPT_UPDATE: api_action(
        key=ACTION_DEPT_UPDATE,
        label=_("Update a department"),
        description=_("Rename a department or change its code/parent/description; only provided fields change"),
        method="PATCH",
        path="/api/identity/dept/<pk>",
        params={
            "pk": {"type": "pk", "required": True, "in": "path", "description": "Department id (from dept.list)"},
            "name": {"type": "string", "required": False, "in": "body", "description": "New name"},
            "code": {"type": "string", "required": False, "in": "body", "description": "New code"},
            "description": {"type": "string", "required": False, "in": "body", "description": "New description"},
        },
    ),
    ACTION_DEPT_DELETE: api_action(
        key=ACTION_DEPT_DELETE,
        label=_("Delete a department"),
        description=_("Delete one department (destructive; goes through the approval flow)"),
        method="DELETE",
        path="/api/identity/dept/<pk>",
        params={"pk": {"type": "pk", "required": True, "in": "path", "description": "Department id (from dept.list)"}},
        requires_approval=requires_approval_high_risk,
    ),
    ACTION_ROLE_LIST: api_action(
        key=ACTION_ROLE_LIST,
        label=_("List roles"),
        description=_("List roles (user groups) with name, code and enabled state"),
        method="GET",
        path="/api/identity/role",
        params={},
    ),
    ACTION_ROLE_UPDATE: api_action(
        key=ACTION_ROLE_UPDATE,
        label=_("Update a role"),
        description=_("Rename a role or change its code/description; menu permissions are not touched"),
        method="PATCH",
        path="/api/identity/role/<pk>",
        params={
            "pk": {"type": "role", "required": True, "in": "path", "description": "Target role name or primary key"},
            "name": {"type": "string", "required": False, "in": "body", "description": "New name"},
            "code": {"type": "string", "required": False, "in": "body", "description": "New code"},
            "description": {"type": "string", "required": False, "in": "body", "description": "New description"},
            # 角色接口契约：PATCH 必带 fields（字段权限树）；空 dict = 不改动字段权限。
            # 刻意不接受 menu 参数——menu 会整体替换权限，改权限请用 role.grant
            "fields": {"const": {}, "in": "body"},
        },
    ),
    ACTION_ROLE_DELETE: api_action(
        key=ACTION_ROLE_DELETE,
        label=_("Delete a role"),
        description=_("Delete one role (destructive; goes through the approval flow)"),
        method="DELETE",
        path="/api/identity/role/<pk>",
        params={
            "pk": {"type": "role", "required": True, "in": "path", "description": "Target role name or primary key"}
        },
        requires_approval=requires_approval_high_risk,
    ),
    ACTION_MENU_LIST: api_action(
        key=ACTION_MENU_LIST,
        label=_("List menus and permissions"),
        description=_("Menu tree including permission points (for discussing authorization scope)"),
        method="GET",
        path="/api/system/menu",
        params={},
    ),
    ACTION_PERMISSION_LIST: api_action(
        key=ACTION_PERMISSION_LIST,
        label=_("List data permission rules"),
        description=_("Row-level data permission rules (model, scope, bound roles/depts/users)"),
        method="GET",
        path="/api/system/permission",
        params={},
    ),
    # ---- 审批中心（只读查询 + 高危裁决） ----
    ACTION_APPROVAL_LIST: api_action(
        key=ACTION_APPROVAL_LIST,
        label=_("List approval requests"),
        description=_("Approval requests visible to the caller (mine to approve / submitted by me), newest first"),
        method="GET",
        path="/api/approval/approvals",
        params={
            "status": {
                "type": "string",
                "required": False,
                "in": IN_QUERY,
                "description": "Filter by status keyword (pending/approved/rejected/cancelled/expired/failed)",
            }
        },
    ),
    ACTION_APPROVAL_PENDING_COUNT: api_action(
        key=ACTION_APPROVAL_PENDING_COUNT,
        label=_("Count my pending approvals"),
        description=_("Number of approval requests waiting for me to review"),
        method="GET",
        path="/api/approval/approvals/pending-count",
        params={},
    ),
    ACTION_APPROVAL_STATS: api_action(
        key=ACTION_APPROVAL_STATS,
        label=_("Show approval statistics"),
        description=_("Approval stats of recent 30 days (submitted/approved/rejected, average duration, my pending)"),
        method="GET",
        path="/api/approval/approvals/stats",
        params={},
    ),
    ACTION_APPROVAL_APPROVE: api_action(
        key=ACTION_APPROVAL_APPROVE,
        label=_("Approve an approval request"),
        description=_(
            "Approve one approval request as its reviewer (destructive decision; goes through the approval "
            "flow itself). Request id comes from approval.list; the applicant cannot approve their own request"
        ),
        method="POST",
        path="/api/approval/approvals/<pk>/approve",
        params={
            "pk": {
                "type": "pk",
                "required": True,
                "in": "path",
                "description": "Approval request id (from approval.list)",
            }
        },
        requires_approval=requires_approval_high_risk,
    ),
    ACTION_APPROVAL_REJECT: api_action(
        key=ACTION_APPROVAL_REJECT,
        label=_("Reject an approval request"),
        description=_(
            "Reject one approval request with a mandatory reason (destructive decision; goes through the "
            "approval flow itself). Request id comes from approval.list"
        ),
        method="POST",
        path="/api/approval/approvals/<pk>/reject",
        params={
            "pk": {
                "type": "pk",
                "required": True,
                "in": "path",
                "description": "Approval request id (from approval.list)",
            },
            "reason": {"type": "string", "required": True, "in": "body", "description": "Rejection reason"},
        },
        requires_approval=requires_approval_high_risk,
    ),
    # ---- 站内信 ----
    ACTION_MESSAGE_UNREAD_LIST: api_action(
        key=ACTION_MESSAGE_UNREAD_LIST,
        label=_("List my unread messages"),
        description=_("Current user's unread notifications and announcements (with totals)"),
        method="GET",
        path="/api/notifications/site-messages/unread",
        params={},
    ),
    ACTION_MESSAGE_MARK_ALL_READ: api_action(
        key=ACTION_MESSAGE_MARK_ALL_READ,
        label=_("Mark all my messages as read"),
        description=_("Mark every unread message of the current user as read"),
        method="PATCH",
        path="/api/notifications/site-messages/all-read",
        params={},
    ),
}
