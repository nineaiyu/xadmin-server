#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""菜单权限点同步内核：扫描/审计/绑定常量与登记表。"""

# 死端点（前端无消费方）：登记豁免，不生成权限点。
# 2026-09-14：`api/system/tasks/interval` 及其前端死代码已删除，此处清空备查。
DEAD_ENDPOINT_PREFIXES = ()
# 不参与扫描的路由前缀（demo 应用按既定决策不投入）
SKIP_ROUTE_PREFIXES = ("api/demo/",)
# 无同源权限点时，按路由前缀指定父菜单（Menu.name，必须为页面菜单）
PARENT_MENU_MAP = {
    "api/system/dynamic-form-submissions": "FormMySubmission",
    "api/system/approval-delegations": "SystemApprovalDelegation",
}
# 审计豁免（有权限点但不在可扫描路由面内，运行期经权限链正则回退命中，权限点有效）：
# - api/chat/*：不在 PERMISSION_SHOW_PREFIX（框架未纳入菜单生成面），权限点手工维护；
# - api-docs/*、api/flower/*、api/system/global-search：路由以无名 pattern / 代理注册，
#   get_all_url_dict 忽略无名路由，故不出现在扫描面内。
AUDIT_SKIP_PREFIXES = ("api/chat/", "api-docs/", "api/flower/", "api/system/global-search")
# 已知「同端点双权限码」重复点：各自服务不同 UI 入口/动作（非脏数据，不报告、不合并）：
# - tasks/executions GET：任务页「日志」按钮(log:SystemTask) 与任务中心抽屉(list:SystemTaskExecution)；
# - logs/operation GET：操作日志页(list:SystemOperationLog) 与用户页「变更历史」(changeHistory:SystemUser)；
# - tasks/periodic/batch-enable POST：批量启用/停用共用端点（batchEnable / batchDisable 两码，
#   授权不区分方向，属已知边界）。
AUDIT_KNOWN_DUPLICATES = {
    ("api/system/tasks/executions$", "GET"),
    ("api/system/logs/operation$", "GET"),
    ("api/system/tasks/periodic/batch-enable$", "POST"),
}
# 已知「单权限码覆盖同端点多方法」：前端以同一权限码驱动查看/保存（拆分权限点会改变页面
# hasAuth 口径），扫描按本表视为全覆盖；键 = 路由正则原文（含 `$`，与权限点 path 同口径），
# 值 = 该权限码覆盖的方法集合。
# - user/{pk}/im-binding GET+POST：管理员代录 IM 身份（查看绑定 / 创建或更新），
#   前端统一用 `imBinding:SystemUser` 判定入口可见性。
SHARED_METHOD_PATHS = {
    "api/system/user/(?P<pk>[^/.]+)/im-binding$": ("GET", "POST"),
    # chat/room/{pk}/members GET+POST：查看成员 / 增删成员共用 members:ChatRoom 权限码
    "api/chat/room/(?P<pk>[^/.]+)/members$": ("GET", "POST"),
    # screens/{pk}/command GET+POST：查询控制态 / 下发控制指令共用 command:DataScreen 权限码
    "api/system/screens/(?P<pk>[^/.]+)/command$": ("GET", "POST"),
}
# 需保持「模型绑定为空」的动作：导入导出链（字段权限回退到 list/create 菜单的口径）
IMPORT_EXPORT_ACTIONS = (
    "export_data",
    "export_async",
    "import_data",
    "import_async",
    "import_validate",
    "import_headers",
)
# 需绑定关联模型的动作（与 get_view_permissions 的 models 口径一致）
CRUD_BIND_ACTIONS = ("list", "create", "retrieve", "partial_update")
