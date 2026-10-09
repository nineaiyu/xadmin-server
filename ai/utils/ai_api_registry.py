#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""声明式 AI 动作的注册表：新增能力 = 在此加一条声明（零 AI 专用业务代码）。

从 ``ai_api_actions.py`` 拆出仅因行数门禁（500 行）：该文件的机制部分
（``ApiActionSpec`` / ``api_action`` / 参数解析 / dispatch 执行）与声明部分分离，
导入方向保持单向（registry → ai_api_actions），注册表仍是唯一白名单入口。

**新增系统能力给 AI 用的正确姿势**（按优先级）：
1. 能用一条 ``api_action`` 声明复用的（绝大多数管理操作）→ 在这里或拆分文件
   （``ai_registry_ops.py`` / ``ai_registry_infra.py`` / ``ai_registry_org.py``）加声明；
2. 需要复杂校验/副作用编排的少数动作 → 在 ``ai_builtin_actions.py`` 写实现，
   在 ``ai_actions.ACTION_SPECS`` 登记。

两者混装进同一注册表，调用方（``execute_action`` / ``tool_catalog``）无需区分。

**明确不声明的能力**（红线）：
- ``user.destroy`` / ``user.batch_destroy`` / ``user.reset_mfa``：业务端挂密码二次
  确认权限类（``UserConfirmation.require(ConfirmType.PASSWORD)``），AI 链路无法安全
  携带密码（密码不得进对话/审计/审批快照），故不声明；``user.reset_password`` 因
  接口要求前端 AES 加密协议同理排除；
- 批量类（batch-*）动作一律不声明：AI 单条操作语义即可，批量请走管理页；
- ``approval.cancel``（申请人撤回）：用户可直接在审批中心操作，无 AI 化收益。
"""

from django.utils.translation import gettext_lazy as _

from ai.utils.ai_api_actions import IN_QUERY, api_action
from ai.utils.ai_registry_infra import INFRA_ACTION_SPECS
from ai.utils.ai_registry_ops import OPS_ACTION_SPECS
from ai.utils.ai_registry_org import ORG_ACTION_SPECS

# ---------------------------------------------------------------------------
# 声明式动作注册表（新增能力 = 在此加一条声明，零 AI 专用业务代码）
# ---------------------------------------------------------------------------

ACTION_NOTICE_PUBLISH = "notice.publish"
ACTION_USER_SET_ACTIVE = "user.set_active"
ACTION_USER_SEARCH = "user.search"
ACTION_NOTICE_LIST = "notice.list"
ACTION_USER_UPDATE = "user.update"
ACTION_ROLE_CREATE = "role.create"
ACTION_ROLE_GRANT = "role.grant"

API_ACTION_SPECS = {
    # ---- 声明式动作（复用现有业务接口：权限链/序列化校验/写入逻辑全复用） ----
    # 新增能力只需在此加一条声明，无任何 AI 专用业务代码；
    # path 的 <占位> 由服务端持有，LLM 只填参数值。
    ACTION_NOTICE_PUBLISH: api_action(
        key=ACTION_NOTICE_PUBLISH,
        label=_("Publish a system announcement"),
        description=_("Publish a system-wide announcement visible to all users"),
        # 复用公告管理页的 announcement 端点（AnnouncementSerializer 校验 + 净化 +
        # 发布推送信号），权限点 announcement:SystemNotice
        method="POST",
        path="/api/notifications/notice-messages/announcement",
        params={
            "title": {"type": "string", "required": True, "in": "body", "description": "Announcement title"},
            "message": {"type": "string", "required": True, "in": "body", "description": "Announcement body text"},
            "level": {
                "type": "enum",
                "values": ["info", "primary", "success", "danger"],
                "default": "info",
                "in": "body",
                "description": "Notice level, default info",
            },
        },
        # 公告端点的隐含必填字段（业务接口既有契约，原样满足）：全员公告类型 +
        # 立即可见 + 无指定接收人/附件
        defaults={
            "notice_type": 1,
            "publish": True,
            "notice_user": [],
            "notice_dept": [],
            "notice_role": [],
            "files": [],
        },
    ),
    ACTION_USER_SET_ACTIVE: api_action(
        key=ACTION_USER_SET_ACTIVE,
        label=_("Enable or disable a user"),
        description=_("Set a user's active state; disabled users cannot sign in"),
        # 复用用户管理的部分更新接口（UserViewSet.partial_update：序列化校验、
        # 缓存/会话失效、操作日志），权限点 partialUpdate:UserInfo
        method="PATCH",
        path="/api/identity/user/<pk>",
        params={
            # 参数名与 path 占位同名（pk）；type=user 表示接受用户名/昵称并由服务端解析为 pk
            "pk": {"type": "user", "required": True, "in": "path", "description": "Target user username or nickname"},
            "is_active": {"type": "bool", "required": True, "in": "body", "description": "True enable, False disable"},
        },
    ),
    # ---- 读类动作（查询目录/列表）：dispatch 复用列表接口，结果以调用者数据权限口径返回 ----
    ACTION_USER_SEARCH: api_action(
        key=ACTION_USER_SEARCH,
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
    ACTION_NOTICE_LIST: api_action(
        key=ACTION_NOTICE_LIST,
        label=_("List system announcements"),
        description=_("List published system announcements, optionally filtered by title keyword"),
        method="GET",
        path="/api/notifications/notice-messages",
        params={
            "title": {"type": "string", "required": False, "in": IN_QUERY, "description": "Title keyword (fuzzy)"},
            # 服务端固定：只看「系统公告」类型（notice_type=1），不接受模型指定
            "notice_type": {"const": 1, "in": IN_QUERY},
        },
    ),
    ACTION_USER_UPDATE: api_action(
        key=ACTION_USER_UPDATE,
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
    ACTION_ROLE_CREATE: api_action(
        key=ACTION_ROLE_CREATE,
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
    ACTION_ROLE_GRANT: api_action(
        key=ACTION_ROLE_GRANT,
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
}

# 注册表汇总：本文件（公告/用户/角色基础动作）+ 拆分声明文件（运维观测/基础设施/组织域），
# API_ACTION_SPECS 仍是唯一白名单入口（ai_actions.ACTION_SPECS 再导出）
API_ACTION_SPECS = {
    **API_ACTION_SPECS,
    **OPS_ACTION_SPECS,
    **INFRA_ACTION_SPECS,
    **ORG_ACTION_SPECS,
}
