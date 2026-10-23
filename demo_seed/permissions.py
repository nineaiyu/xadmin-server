#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""演示角色的最小权限白名单（单一事实源）。

演示账号是公网可登录的共享账号，不能沿用「管理员角色减去若干危险权限」的
deny-list 思路：权限点会持续新增，漏掉一个写操作就是一次越权。这里改为
allow-list——演示角色最终只保留本文件声明的页面与权限点，其余一律摘除。

页面范围刻意收敛在「有演示数据、且写操作只影响演示数据」的业务面：
审批 / 表单 / 数据分析 / AI 问答 / 知识库只读 / 示例图书 / 聊天 / 个人中心，
以及用户、角色、部门、岗位、字典、标签的只读管理页。菜单、数据权限、字段权限、
日志、在线用户、文件中心、导出中心、监控、任务调度、Webhook、开放平台应用
等系统治理面不进入演示角色。

`seed_demo_admin` 按本清单设置角色菜单；`load_init_json` 只装载各 app 的
`grants.json`（同一清单的静态子集），两者由守护测试保持一致。
"""

from __future__ import annotations

from typing import Any

#: 演示角色可见的页面（menu_type=MENU，按 path 匹配）。
#: 覆盖业务演示与系统治理的**只读展示**；写操作由权限点白名单单独限制。
DEMO_PAGE_PATHS: tuple[str, ...] = (
    # 系统管理：用户 / 角色 / 部门 / 岗位 / 字典 / 标签（只读）
    "/system/user/index",
    "/system/role/index",
    "/system/dept/index",
    "/system/post/index",
    "/system/dict/index",
    "/system/tag/index",
    # 系统治理展示：菜单/数据权限/字段权限/日志/文件/导出/监控/任务/通知/在线用户
    "/system/menu/index",
    "/system/permission/index",
    "/system/field/index",
    "/system/logs/operation/index",
    "/system/logs/login/index",
    "/system/file/index",
    "/system/export/index",
    "/system/mask/index",
    "/system/monitor/index",
    "/system/celery/task/index",
    "/system/celery/crontab/index",
    "/system/celery/interval/index",
    "/system/celery/logs/index",
    "/system/notice/index",
    "/system/notice/read/list",
    "/system/online/index",
    "/system/account-risk/index",
    "/system/my-scope/index",
    # 集成：开放平台应用 / Webhook 订阅与投递（只读）
    "/integration/api-app/index",
    "/integration/subscription/index",
    "/integration/delivery/index",
    # 关于
    "/about/index",
    # 审批：待办 / 实例 / 请假 / 流程定义 / 委托 / 规则只读
    "/approval/index",
    "/approval/instance/index",
    "/approval/leave/index",
    "/approval/flow/index",
    "/approval/delegation/index",
    "/approval/rule/index",
    # 表单：数据只读 / 设计器只读 / 我的填报可写
    "/form-collection/data/index",
    "/form-collection/designer/index",
    "/form-collection/my/index",
    # 数据分析：数据集 / 看板 / 报表 / 大屏（只读 + 执行）
    "/analysis/dataset/index",
    "/analysis/dashboard/index",
    "/analysis/report/index",
    "/analysis/screen/index",
    # AI 助手与知识库只读
    "/integration/ai/index",
    "/integration/knowledge/index",
    # 示例图书（CRUD 演示）与聊天
    "/demo/book/index",
    "/chat/index",
    # 个人中心与我的通知
    "/user/info/index",
    "/user/notice/index",
)

#: 页面菜单名（grants.json 用名称引用；与 DEMO_PAGE_PATHS 一一对应）。
DEMO_PAGE_NAMES: frozenset[str] = frozenset(
    {
        "SystemUser",
        "SystemRole",
        "SystemDept",
        "SystemPost",
        "SystemDataDict",
        "SystemTag",
        "SystemMenu",
        "SystemDataPermission",
        "SystemModelLabelField",
        "SystemOperationLog",
        "SystemUserLoginLog",
        "SystemUploadFile",
        "SystemExportRecord",
        "SystemDataMaskRule",
        "SystemMonitor",
        "SystemTask",
        "SystemTaskCrontab",
        "SystemTaskInterval",
        "SystemTaskExecution",
        "SystemNotice",
        "SystemNoticeRead",
        "SystemUserOnline",
        "SystemAccountRisk",
        "SystemMyScope",
        "IntegrationApiApp",
        "WebhookSubscription",
        "WebhookDelivery",
        "About",
        "SystemApprovalRequest",
        "SystemApprovalInstance",
        "SystemLeave",
        "SystemApprovalFlow",
        "SystemApprovalDelegation",
        "SystemApprovalRule",
        "FormData",
        "FormDesigner",
        "FormMySubmission",
        "DataDataset",
        "DataDashboard",
        "DataReport",
        "DataScreen",
        "AiAssistant",
        "AiKnowledge",
        "DemoBook",
        "Chat",
        "UserInfo",
        "UserNotice",
    }
)

#: 演示角色允许的权限点（菜单名）。写操作只保留演示业务动作与示例图书 CRUD；
#: 身份治理与系统配置只保留只读展示，日志/监控/任务/Webhook/开放平台同样只读。
DEMO_ALLOWED_PERMISSION_NAMES: frozenset[str] = frozenset(
    {
        # ---- 用户 / 角色 / 部门 / 岗位：只读 + 部门内头像上传 ----
        "list:SystemUser",
        "retrieve:SystemUser",
        "preview:SystemUser",
        "upload:SystemUser",
        "list:SearchUser",
        "list:SystemRole",
        "retrieve:SystemRole",
        "preview:SystemRole",
        "list:SearchRole",
        "list:SystemDept",
        "retrieve:SystemDept",
        "managed:SystemDept",
        "preview:SystemDept",
        "list:SearchDept",
        "list:SystemPost",
        "retrieve:SystemPost",
        "preview:SystemPost",
        "list:SearchPost",
        # ---- 个人中心：资料 / 本人头像 / 本人登录日志 / 通知订阅 ----
        "retrieve:UserInfo",
        "partialUpdate:UserInfo",
        "upload:UserInfo",
        "list:UserLoginLog",
        "list:UserMsgSubscription",
        "retrieve:UserMsgSubscription",
        "backends:UserMsgSubscription",
        "partialUpdate:UserMsgSubscription",
        # ---- 我的通知 ----
        "list:UserNotice",
        "unread:UserNotice",
        "allRead:UserNotice",
        "batchRead:UserNotice",
        # ---- 字典 / 标签：只读 ----
        "list:SystemDataDict",
        "retrieve:SystemDataDict",
        "items:SystemDataDict",
        "list:Tag",
        "retrieve:Tag",
        "resources:Tag",
        "objects:Tag",
        # ---- 系统治理只读展示：菜单 / 数据权限 / 字段权限 / 日志 / 文件 / 导出 ----
        "list:SystemMenu",
        "retrieve:SystemMenu",
        "apiUrl:SystemMenu",
        "permissionAudit:SystemMenu",
        "list:SystemDataPermission",
        "retrieve:SystemDataPermission",
        "list:SystemModelLabelField",
        "retrieve:SystemModelLabelField",
        "lookups:SystemModelLabelField",
        "list:SystemOperationLog",
        "retrieve:SystemOperationLog",
        "list:SystemUserLoginLog",
        "retrieve:SystemUserLoginLog",
        "list:SystemUploadFile",
        "retrieve:SystemUploadFile",
        "preview:SystemUploadFile",
        "download:SystemUploadFile",
        "accessLogs:SystemUploadFile",
        "config:SystemUploadFile",
        "list:SystemExportRecord",
        "retrieve:SystemExportRecord",
        "download:SystemExportRecord",
        "log:SystemExportRecord",
        "stats:SystemExportRecord",
        "list:SystemImportRecord",
        "retrieve:SystemImportRecord",
        "download:SystemImportRecord",
        "log:SystemImportRecord",
        "stats:SystemImportRecord",
        "list:SystemImportTemplate",
        "retrieve:SystemImportTemplate",
        "list:SystemDataMaskRule",
        "retrieve:SystemDataMaskRule",
        "preview:SystemDataMaskRule",
        # ---- 监控 / 任务调度 / 通知 / 在线用户 / 账号风险 / 我的范围（只读） ----
        "list:SystemMonitor",
        "celeryInfo:SystemMonitor",
        "events:SystemMonitor",
        "export:SystemMonitor",
        "history:SystemMonitor",
        "redisInfo:SystemMonitor",
        "services:SystemMonitor",
        "slowList:SystemMonitor",
        "taskHealth:SystemMonitor",
        "thresholds:SystemMonitor",
        "list:SystemTask",
        "retrieve:SystemTask",
        "log:SystemTask",
        "registered:SystemTask",
        "list:SystemTaskCrontab",
        "retrieve:SystemTaskCrontab",
        "list:SystemTaskInterval",
        "retrieve:SystemTaskInterval",
        "list:SystemTaskCenter",
        "list:SystemTaskExecution",
        "retrieve:SystemTaskExecution",
        "log:SystemTaskExecution",
        "stats:SystemTaskExecution",
        "list:SystemNotice",
        "retrieve:SystemNotice",
        "list:SystemNoticeRead",
        "retrieve:SystemNoticeRead",
        "list:SystemUserOnline",
        "retrieve:SystemUserOnline",
        "list:SystemAccountRisk",
        "stats:SystemAccountRisk",
        # ---- 开放平台 / Webhook 只读 ----
        "list:IntegrationApiApp",
        "retrieve:IntegrationApiApp",
        "listGrants:IntegrationApiApp",
        "stats:IntegrationApiApp",
        "list:WebhookSubscription",
        "retrieve:WebhookSubscription",
        "events:WebhookSubscription",
        "list:WebhookDelivery",
        "retrieve:WebhookDelivery",
        # ---- 审批委托 / 规则只读 ----
        "list:SystemApprovalDelegation",
        "retrieve:SystemApprovalDelegation",
        "all:SystemApprovalDelegation",
        "list:SystemApprovalRule",
        "retrieve:SystemApprovalRule",
        "candidateOptions:SystemApprovalRule",
        # ---- 审批：业务动作只作用于演示数据 ----
        "list:SystemApprovalRequest",
        "retrieve:SystemApprovalRequest",
        "stats:SystemApprovalRequest",
        "pendingCount:SystemApprovalRequest",
        "approve:SystemApprovalRequest",
        "reject:SystemApprovalRequest",
        "cancel:SystemApprovalRequest",
        "batchApprove:SystemApprovalRequest",
        "batchReject:SystemApprovalRequest",
        "list:SystemApprovalInstance",
        "retrieve:SystemApprovalInstance",
        "stats:SystemApprovalInstance",
        "pendingCount:SystemApprovalInstance",
        "availableFlows:SystemApprovalInstance",
        "comments:SystemApprovalInstance",
        "returnTargets:SystemApprovalInstance",
        "create:SystemApprovalInstance",
        "approve:SystemApprovalInstance",
        "reject:SystemApprovalInstance",
        "cancel:SystemApprovalInstance",
        "addSign:SystemApprovalInstance",
        "removeSign:SystemApprovalInstance",
        "transfer:SystemApprovalInstance",
        "returnNode:SystemApprovalInstance",
        "addComment:SystemApprovalInstance",
        "urge:SystemApprovalInstance",
        "batchApprove:SystemApprovalInstance",
        "batchReject:SystemApprovalInstance",
        "batchTransfer:SystemApprovalInstance",
        "list:SystemLeave",
        "retrieve:SystemLeave",
        "stats:SystemLeave",
        "create:SystemLeave",
        "partialUpdate:SystemLeave",
        "submit:SystemLeave",
        "cancel:SystemLeave",
        "list:SystemApprovalFlow",
        "retrieve:SystemApprovalFlow",
        "versions:SystemApprovalFlow",
        # ---- 表单：定义只读，我的填报可写 ----
        "list:FormData",
        "retrieve:FormData",
        "formOptions:FormData",
        "list:FormDesigner",
        "retrieve:FormDesigner",
        "schemaHistory:FormDesigner",
        "list:FormMySubmission",
        "retrieve:FormMySubmission",
        "availableForms:FormMySubmission",
        "userOptions:FormMySubmission",
        "create:FormMySubmission",
        "partialUpdate:FormMySubmission",
        "submit:FormMySubmission",
        "resubmit:FormMySubmission",
        # ---- 数据分析：定义只读 + 执行 ----
        "list:DataDataset",
        "retrieve:DataDataset",
        "meta:DataDataset",
        "execute:DataDataset",
        "aggregate:DataDataset",
        "list:DataDashboard",
        "retrieve:DataDashboard",
        "list:DataReport",
        "retrieve:DataReport",
        "run:DataReport",
        "userOptions:DataReport",
        "list:DataScreen",
        "retrieve:DataScreen",
        "command:DataScreen",
        "list:ScreenClipboard",
        "retrieve:ScreenClipboard",
        "list:ScreenTemplate",
        "retrieve:ScreenTemplate",
        "versions:ScreenTemplate",
        # ---- AI 助手 / 知识库只读 ----
        "ask:AiAssistant",
        "interpret:AiAssistant",
        "run:AiAssistant",
        "actionExecute:AiAssistant",
        "status:AiAssistant",
        "list:AiKnowledge",
        "retrieve:AiKnowledge",
        "buildEmbeddingsStatus:AiKnowledge",
        "syncRepoStatus:AiKnowledge",
        # ---- 聊天 ----
        "list:ChatRoom",
        "list:ChatMessage",
        "list:ChatContact",
        "file:ChatMessage",
        "members:ChatRoom",
        "rename:ChatRoom",
        "leave:ChatRoom",
        "createGroup:ChatRoom",
        "ask:ChatRoom",
        "stream:ChatRoom",
        "create:ChatRoom",
        "recall:ChatMessage",
        "upload:ChatMessage",
        # ---- 示例图书 ----
        "list:DemoBook",
        "retrieve:DemoBook",
        "create:DemoBook",
        "partialUpdate:DemoBook",
        "update:DemoBook",
        "push:DemoBook",
        "submit:DemoBook",
        "changeHistory:DemoBook",
        "exportData:DemoBook",
    }
)

#: 演示角色允许的全部菜单名（页面 + 权限点）。
DEMO_ALLOWED_MENU_NAMES: frozenset[str] = DEMO_PAGE_NAMES | DEMO_ALLOWED_PERMISSION_NAMES

#: 演示账号角色 code（与种子 userrole.json / seed_demo_admin 同源）。
DEMO_ROLE_CODE = "demo"

#: 演示账号用户名（与 seed_demo_admin 同源）。
DEMO_ADMIN_USERNAME = "admin"


def demo_menu_ids() -> set[Any]:
    """按白名单计算演示角色应持有的菜单主键集合。

    - 页面：按 ``DEMO_PAGE_PATHS`` 命中，并补齐祖先目录（导航树需要）；
    - 权限点：按 ``DEMO_ALLOWED_PERMISSION_NAMES`` 命中，并补齐祖先页面/目录。
    只读数据库，不修改任何数据；缺失菜单（模块裁剪/演示 app 未启用）自动跳过。
    """

    from system.models import Menu

    rows = list(Menu.objects.filter(deleted_at__isnull=True).values("pk", "parent_id", "path", "name", "menu_type"))
    by_id = {row["pk"]: row for row in rows}
    selected: set[Any] = set()
    for row in rows:
        if row["menu_type"] == Menu.MenuChoices.MENU and row["path"] in DEMO_PAGE_PATHS:
            selected.add(row["pk"])
        elif row["menu_type"] == Menu.MenuChoices.PERMISSION and row["name"] in DEMO_ALLOWED_PERMISSION_NAMES:
            selected.add(row["pk"])

    # 补齐祖先目录：页面/权限点只有在父链完整时才会出现在导航与权限树里。
    for pk in list(selected):
        parent_id = by_id.get(pk, {}).get("parent_id")
        while parent_id and parent_id not in selected:
            selected.add(parent_id)
            parent_id = by_id.get(parent_id, {}).get("parent_id")
    return selected


def demo_role_menu_names() -> frozenset[str]:
    """白名单菜单名集合（供种子/测试断言使用）。"""

    return DEMO_ALLOWED_MENU_NAMES
