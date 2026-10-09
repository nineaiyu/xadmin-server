#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""AI 工具面 triage（收口）：候选资源域的「注册 / 不 AI 化」显式决策清单。

`manage.py ai_tool_audit` 对“未在 `API_ACTION_SPECS` 声明”的路由做三态判定：

1. **已声明**：被 AI 统一工具层覆盖（正常态，不进入候选）；
2. **triage 命中 exempt**：已确认**不 AI 化**（本清单登记理由，审计可查）；
3. **triage 命中 register 或未命中任何登记**：计入缺口 —— `--fail-on-gap` 退出码 1。

因此本清单是“新模块出生即被 AI 面感知”的抓手：新增资源域（或改判）必须在这里登记一次，
巡检才回到零缺口；`--fail-on-gap` 因此可安全入 CI（守护新路由不被静默漏掉）。
"""

#: 资源域 → (决策, 理由)；决策取值：exempt（不 AI 化）/ register（应注册，待补声明）
TRIAGE_RESOURCES = {
    "notifications": ("exempt", "站内信 / 公告 / 消息模板 / 订阅矩阵为用户消息中心，由页面链路维护"),
    "settings": ("exempt", "系统设置面（邮件/短信/MFA/注册/LDAP/登录限流）为管理员人工配置"),
    # 独立前缀 app：resource_key 取 api/ 后第一段，整域一条登记
    "approval": (
        "exempt",
        "审批域整体不 AI 直调——审批单（412 协议载体）由业务页面与审批中心处理，"
        "流程/规则为管理员配置面（节点/审批人改动需人工确认），委托为个人工作流设置，"
        "实例动作走审批中心（412 协议 + 审批权限链），请假业务由请假页面驱动",
    ),
    "dataset": (
        "exempt",
        "数据分析与动态表单域整体不 AI 化——数据集为分析取数配置、仪表盘/大屏为展示配置"
        "（定时报表由任务中心驱动）、表单设计器为页面级配置面、填报数据走表单中心（含审批与草稿态机）",
    ),
    "identity": (
        "exempt",
        "身份与访问域整体不 AI 直调——用户/角色/部门/岗位为组织权限配置面（写动作涉及账号与授权安全，"
        "AI 只读侧走已注册的 user.list 等动作），登录/注册/重置/会话为认证面，通讯录为只读人员名录链路，"
        "个人中心（资料/绑定/改密）与个人访问令牌为用户自助凭证面，账号安全巡检与登录策略为安全运维面，"
        "开放平台应用凭据为集成运维面",
    ),
    "file": ("exempt", "文件中心（上传/预览/回收站）为页面链路，含上传策略校验"),
    "audit": ("exempt", "审计日志查询为运维排障面（含脱敏与数据域控制），数据脱敏规则为安全配置面"),
    "task": (
        "exempt",
        "任务中心查询与取消/重跑为运维操作面；下载中心记录查询与产物下载、导入记录与失败报告、"
        "导入模板为页面链路；Webhook 订阅与投递记录为集成运维面",
    ),
    "system/codegen": (
        "exempt",
        "代码生成器为开发者页面工具（模型清单/字段勾选/预览/下载为 GUI 向导链路，产物下载为文件动作），不进 AI 工具面",
    ),
    "system/codegen-plans": (
        "exempt",
        "代码生成方案的保存/取用为同一开发者页面工具的配套存储（个人级表单状态），与生成器同口径不进 AI 工具面",
    ),
    "system/credentials": ("exempt", "凭据总览与轮换为高危运维动作，需人工确认"),
    "system/dict": ("exempt", "数据字典为平台配置面（AI 读侧走字典选项下发）"),
    "system/modules": ("exempt", "模块裁剪为运维动作，需人工确认影响面"),
    "system/search": ("exempt", "全局搜索为前端交互内部接口"),
}


def resource_key(url: str) -> str:
    """候选 URL → 资源域键（``api/system/menu/...`` → ``system/menu``；``api/identity/user/...`` → ``identity``）。"""
    parts = [part for part in str(url or "").strip("/").split("/") if part and part != "api"]
    if not parts:
        return "?"
    if parts[0] == "system" and len(parts) > 1:
        return f"system/{parts[1]}"
    return parts[0]


def triage_for(url: str) -> tuple[str, str] | None:
    """返回 ``(决策, 理由)``；未登记返回 None（计入缺口，需在 TRIAGE_RESOURCES 补决策）。"""
    entry = TRIAGE_RESOURCES.get(resource_key(url))
    if entry is None:
        return None
    return entry[0], entry[1]


def is_registered(url: str) -> bool:
    """是否已登记 triage（无论 exempt 或 register）。"""
    return resource_key(url) in TRIAGE_RESOURCES
