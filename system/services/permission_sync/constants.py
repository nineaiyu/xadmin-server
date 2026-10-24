#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""菜单权限点同步内核：框架级约定常量。

app 侧例外（扫描跳过 / 审计豁免 / 父菜单归属 / 运行期自建权限点 / custom 权限面
豁免）已迁至各 app 的 ``declarations.py``，聚合视图见同包 ``declarations.py``；
本模块只保留与具体 app 无关的动作口径约定。
"""

# 死端点（前端无消费方）：登记豁免，不生成权限点。
# 2026-09-14：`api/task/interval` 及其前端死代码已删除，此处清空备查。
DEAD_ENDPOINT_PREFIXES = ()
# 框架自动按钮/标准 CRUD 的权限动作（与 components/RePlusPage 的自动按钮同源）。
# 其余动作属于「页面自定义能力」（rank/permissions/impact/enable/...），审计单列
# custom_action 组提示管理员按 `<lowerCamelAction>:<PascalComponent>` 约定命名；
# 权限码保持兼容，不强制重命名存量数据。
FRAMEWORK_PERMISSION_ACTIONS = frozenset(
    {
        "list",
        "retrieve",
        "create",
        "update",
        "partial_update",
        "destroy",
        "batch_destroy",
        "batch_update",
        "export_data",
        "export_async",
        "import_data",
        "import_async",
        "import_validate",
        "import_headers",
        "recycle_list",
        "recycle_restore",
        "recycle_purge",
        "choices",
        "fields",
        "columns",
    }
)
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
