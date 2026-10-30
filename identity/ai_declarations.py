#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""身份与访问域的 AI 动作声明（用户 / 角色 / 部门 / 在线会话）。

约定：本文件只做声明，执行一律复用既有业务接口（``api_action`` 声明式动作），
由 ``ai.services.actions.ai_registry`` 在首次构建工具目录时自动发现，无需改动 ai 应用。
写动作的审批口径见 ``ai_api_actions.requires_approval_high_risk``（非超管走 412）。
"""

from django.utils.translation import gettext_lazy as _

from ai.services.actions.ai_api_actions import IN_QUERY, api_action, requires_approval_high_risk

AI_ACTIONS = {
    # ---- 用户 ----
    "user.create": api_action(
        key="user.create",
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
    "user.detail": api_action(
        key="user.detail",
        label=_("Look up a user's details"),
        description=_("Fetch one user's profile (nickname, contact, dept, roles, active state)"),
        method="GET",
        path="/api/identity/user/<pk>",
        params={
            "pk": {"type": "user", "required": True, "in": "path", "description": "Target user username or nickname"}
        },
    ),
    "user.search": api_action(
        key="user.search",
        label=_("Look up users"),
        description=_("Search users by username / nickname / enabled state; returns the first page (up to 20 rows)"),
        method="GET",
        path="/api/identity/user",
        params={
            "username": {
                "type": "string",
                "required": False,
                "in": IN_QUERY,
                "description": "Username keyword (fuzzy)",
            },
            "nickname": {
                "type": "string",
                "required": False,
                "in": IN_QUERY,
                "description": "Nickname keyword (fuzzy)",
            },
            "is_active": {"type": "bool", "required": False, "in": IN_QUERY, "description": "Filter by enabled state"},
        },
    ),
    "user.update": api_action(
        key="user.update",
        label=_("Update a user's profile"),
        description=_("Update a user's basic attributes (nickname/phone/email); only provided fields change"),
        method="PATCH",
        path="/api/identity/user/<pk>",
        params={
            "pk": {"type": "user", "required": True, "in": "path", "description": "Target user username or nickname"},
            "nickname": {"type": "string", "required": False, "in": "body", "description": "New nickname"},
            "phone": {"type": "string", "required": False, "in": "body", "description": "New phone number"},
            "email": {"type": "string", "required": False, "in": "body", "description": "New email"},
        },
    ),
    "user.set_active": api_action(
        key="user.set_active",
        label=_("Enable or disable a user"),
        description=_("Set a user's active state; disabled users cannot sign in"),
        method="PATCH",
        path="/api/identity/user/<pk>",
        params={
            "pk": {"type": "user", "required": True, "in": "path", "description": "Target user username or nickname"},
            "is_active": {"type": "bool", "required": True, "in": "body", "description": "True enable, False disable"},
        },
    ),
    "user.unblock": api_action(
        key="user.unblock",
        label=_("Unblock a user"),
        description=_("Release a user from login-block state (after repeated password failures)"),
        method="POST",
        path="/api/identity/user/<pk>/unblock",
        params={
            "pk": {"type": "user", "required": True, "in": "path", "description": "Target user username or nickname"}
        },
    ),
    # ---- 角色 ----
    "role.create": api_action(
        key="role.create",
        label=_("Create a role"),
        description=_("Create a new role (user group) with a unique name and code"),
        method="POST",
        path="/api/identity/role",
        params={
            "name": {"type": "string", "required": True, "in": "body", "description": "Role name (unique)"},
            "code": {"type": "string", "required": True, "in": "body", "description": "Role code (unique)"},
            "description": {"type": "string", "required": False, "in": "body", "description": "Role description"},
        },
        # 角色接口既有契约：字段权限树（创建时为空）+ 菜单权限列表（授权另行用 role.grant）
        defaults={"fields": {}, "menu": []},
    ),
    "role.list": api_action(
        key="role.list",
        label=_("List roles"),
        description=_("List roles (user groups) with name, code and enabled state"),
        method="GET",
        path="/api/identity/role",
        params={},
    ),
    "role.grant": api_action(
        key="role.grant",
        label=_("Set role menu permissions"),
        description=_(
            "Grant menus to a role. A menu name resolves to the menu and ALL permissions under it "
            "(same as checking a parent node in the authorization tree). This REPLACES the role's "
            "current menu permissions"
        ),
        method="PATCH",
        path="/api/identity/role/<pk>",
        params={
            "pk": {"type": "role", "required": True, "in": "path", "description": "Target role name or primary key"},
            "menus": {
                "type": "menu",
                "required": True,
                "in": "body",
                # 参数名对 LLM 用复数（menus），落请求体时改写为接口契约字段 menu
                "target": "menu",
                "description": "Menu names or primary keys to grant",
            },
            # 角色接口契约：PATCH 必带 fields（字段权限树）；空 dict = 不改动字段权限
            "fields": {"const": {}, "in": "body"},
        },
    ),
    "role.update": api_action(
        key="role.update",
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
    "role.delete": api_action(
        key="role.delete",
        label=_("Delete a role"),
        description=_("Delete one role (destructive; goes through the approval flow)"),
        method="DELETE",
        path="/api/identity/role/<pk>",
        params={
            "pk": {"type": "role", "required": True, "in": "path", "description": "Target role name or primary key"}
        },
        requires_approval=requires_approval_high_risk,
    ),
    # ---- 部门 ----
    "dept.list": api_action(
        key="dept.list",
        label=_("List departments"),
        description=_("Department tree (name, code, leader, user count)"),
        method="GET",
        path="/api/identity/dept",
        params={},
    ),
    "dept.create": api_action(
        key="dept.create",
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
    "dept.update": api_action(
        key="dept.update",
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
    "dept.delete": api_action(
        key="dept.delete",
        label=_("Delete a department"),
        description=_("Delete one department (destructive; goes through the approval flow)"),
        method="DELETE",
        path="/api/identity/dept/<pk>",
        params={"pk": {"type": "pk", "required": True, "in": "path", "description": "Department id (from dept.list)"}},
        requires_approval=requires_approval_high_risk,
    ),
    # ---- 在线会话 ----
    "online.list": api_action(
        key="online.list",
        label=_("List online users"),
        description=_("Currently online user sessions (user, login type, last active)"),
        method="GET",
        path="/api/identity/online",
        params={},
    ),
    "online.force_logout": api_action(
        key="online.force_logout",
        label=_("Force a user offline"),
        description=_("Revoke all sessions of one user (server-side token revocation + WebSocket kick)"),
        method="POST",
        path="/api/identity/online/force-logout",
        params={"user_pk": {"type": "user", "required": True, "in": "body", "description": "Target user pk"}},
    ),
    # ---- 岗位 / 登录策略 / 账号巡检 / 开放平台（只读） ----
    "post.list": api_action(
        key="post.list",
        label=_("List positions"),
        description=_("List positions (name, code, rank, member count)"),
        method="GET",
        path="/api/identity/posts",
        params={"name": {"type": "string", "required": False, "in": IN_QUERY, "description": "Position name keyword"}},
    ),
    "login_policy.list": api_action(
        key="login_policy.list",
        label=_("List login access policies"),
        description=_("Login access policies (priority, time window, network, effect)"),
        method="GET",
        path="/api/identity/login-policies",
        params={},
    ),
    "account_risk.list": api_action(
        key="account_risk.list",
        label=_("List account risk findings"),
        description=_("Account security findings (risk type, level, account, handled state)"),
        method="GET",
        path="/api/identity/account-risks",
        params={},
    ),
    "api_app.list": api_action(
        key="api_app.list",
        label=_("List open platform applications"),
        description=_("Open platform applications (name, client id, scopes, rate limit, enabled state)"),
        method="GET",
        path="/api/identity/api-applications",
        params={
            "name": {"type": "string", "required": False, "in": IN_QUERY, "description": "Application name keyword"}
        },
        modules=("open_platform",),
    ),
    "pat.list": api_action(
        key="pat.list",
        label=_("List my personal access tokens"),
        description=_("Personal access tokens of the current user (name, scopes, expiry, last used)"),
        method="GET",
        path="/api/identity/personal-access-tokens",
        params={},
        modules=("core_open_credential",),
    ),
    "user.invite": api_action(
        key="user.invite",
        label=_("Invite a user to activate the account"),
        description=_("Send an activation email to a pending user; the account stays unusable until accepted"),
        method="POST",
        path="/api/identity/user/<pk>/invite",
        params={
            "pk": {"type": "user", "required": True, "in": "path", "description": "Target user username or nickname"}
        },
    ),
}
