#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""模块清单（唯一事实源）：内核 / 标配 / 可选模块声明。

自 registry.py 平移（文件行数门禁）；registry 经该模块再导出，既有导入面不变。
"""

from .specs import CORE, OPTIONAL, STANDARD, ModuleSpec

# ---------------------------------------------------------------------------
# 模块清单（唯一事实源）
# ---------------------------------------------------------------------------
MODULES: tuple[ModuleSpec, ...] = (
    # ------------------------- 内核（不可裁剪） -------------------------
    ModuleSpec("core_auth", "身份与访问（登录/注册/重置/会话/MFA/第三方登录）", CORE),
    ModuleSpec("core_rbac", "组织与权限（用户/角色/菜单/部门/字典/数据与字段权限）", CORE),
    ModuleSpec("core_config", "系统配置（站点/安全/消息/水印/个人配置）", CORE),
    ModuleSpec("core_file", "文件与流转（附件/预览/导入/导出下载中心）", CORE),
    ModuleSpec("core_notify", "站内通知中心", CORE),
    ModuleSpec("core_log", "审计与在线（操作日志/登录日志/在线用户）", CORE),
    ModuleSpec("core_open_credential", "个人访问令牌（PAT，个人凭证）", CORE),
    # ------------------------- 标配（默认开启，可裁剪） -------------------------
    ModuleSpec(
        "ops",
        "运维监控（主机监控/定时任务/Flower）",
        STANDARD,
        menus=("SystemMonitor", "celery"),
        routes=(r"^/api/system/monitor", r"^/api/task/", r"^/api/flower/"),
        ws_routes=(r"^/ws/system/monitor/",),
        note="关闭后主机心跳采集与资源告警周期任务一并停止",
    ),
    ModuleSpec(
        "approval",
        "敏感操作审批（拦截 + 审批单 + 多级审批规则）",
        STANDARD,
        menus=("SystemApprovalRequest", "SystemApprovalRule"),
        permissions=("api/approval/approvals", "api/approval/approval-rules"),
        routes=(r"^/api/approval/approvals", r"^/api/approval/approval-rules"),
        note="关闭后 APPROVAL_REQUIRED_PATHS 拦截整体失效（无审批单可落）",
    ),
    ModuleSpec(
        "datamask",
        "数据脱敏",
        STANDARD,
        menus=("SystemDataMaskRule",),
        routes=(r"^/api/audit/mask-rules",),
    ),
    ModuleSpec(
        "ldap",
        "目录同步（LDAP/AD）",
        STANDARD,
        menus=("SettingLdap",),
        routes=(r"^/api/settings/ldap",),
        note="模块控制页面与接口可达；是否真正启用目录同步由 LDAP_AUTH_ENABLED 决定",
    ),
    # ------------------------- 可选（按需开启） -------------------------
    ModuleSpec(
        "chat",
        "聊天室",
        OPTIONAL,
        menus=("Chat",),
        routes=(r"^/api/chat/",),
        ws_routes=(r"^/ws/chat/",),
        note="关闭后 REST、页面与 ws/chat 通道同步拦截",
    ),
    ModuleSpec(
        "ai",
        "AI 助手与知识库",
        OPTIONAL,
        menus=("AiAssistant", "AiAssistantConfig", "AiKnowledge", "AiMcpServers"),
        routes=(r"^/api/ai/",),
        note="聊天室内的 AI 助手属于 chat 模块，不受本开关影响",
    ),
    ModuleSpec(
        "analysis",
        "数据分析（数据集/仪表盘/报表/大屏）",
        OPTIONAL,
        menus=("DataDashboard", "DataDataset", "DataReport", "DataScreen"),
        routes=(
            r"^/api/dataset/datasets",
            r"^/api/dataset/dashboards",
            r"^/api/dataset/screens",
            r"^/api/dataset/reports",
        ),
        ws_routes=(r"^/ws/screen/",),
        note="关闭后定时报表周期任务与 ws/screen 展示通道一并停止",
    ),
    ModuleSpec(
        "dform",
        "表单采集（设计器 / 我的填报 / 表单数据）",
        OPTIONAL,
        menus=("FormDesigner", "FormMySubmission", "FormData"),
        routes=(
            r"^/api/dataset/dynamic-forms",
            r"^/api/dataset/dynamic-form-submissions",
            r"^/api/dataset/form-data",
        ),
    ),
    ModuleSpec(
        "approval_flow",
        "审批流引擎（流程定义/实例/委托/请假）",
        OPTIONAL,
        menus=(
            "SystemApprovalFlow",
            "SystemApprovalInstance",
            "SystemApprovalDelegation",
            "SystemLeave",
        ),
        routes=(
            r"^/api/approval/approval-flows",
            r"^/api/approval/approval-instances",
            r"^/api/approval/approval-delegations",
            r"^/api/approval/leaves",
        ),
    ),
    ModuleSpec(
        "webhook",
        "事件订阅（出站 Webhook）",
        OPTIONAL,
        menus=("WebhookSubscription", "WebhookDelivery"),
        routes=(r"^/api/task/webhooks/",),
    ),
    ModuleSpec(
        "open_platform",
        "开放平台（API 应用 + OAuth 授权码）",
        OPTIONAL,
        menus=("IntegrationApiApp",),
        routes=(r"^/api/identity/api-applications", r"^/api/identity/open/"),
        note="个人访问令牌（PAT）属内核，不随本模块关闭",
    ),
    ModuleSpec(
        "search",
        "全局搜索",
        OPTIONAL,
        menus=("SearchData",),
        permissions=("api/system/global-search",),
        routes=(r"^/api/system/global-search", r"^/api/system/search/"),
    ),
    ModuleSpec("scim", "SCIM 2.0 目录同步", OPTIONAL, routes=(r"^/api/scim/v2/",)),
)
