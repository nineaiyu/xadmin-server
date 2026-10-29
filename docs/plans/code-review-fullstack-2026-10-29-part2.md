# xadmin 全栈功能审查（A 篇）— 2026-10-29 · part2（页面清单与详述）

> 主篇：`code-review-fullstack-2026-10-29.md`（总览 / 后端 app 清单 / 发现登记簿 / 统计 / 批次日志）。
> 本篇承接主篇 §2.2（页面清单与结果总表）、§2.3（链路映射表）与 §3（页面详述）；
> 页面状态口径与证据等级见主篇 §0.3。

### 2.2 页面清单与审查结果总表

> 页面粒度：一个路由叶子页 = 一个审查单元。状态集合：未审 / 阅读中 / 链路完整 / 已审 /
> 未验证 / 排除。「示例/调试」不计入业务页 P0/P1 统计。
> 四闭环列（权限/元数据/契约/数据）取值：通过 / 缺口 / 未验证 / —（不适用）。

#### A. 菜单种子驱动的业务页（65）

| 页面 ID | 路由 | 组件 | 归属 app | 状态 | 权限 | 元数据 | 契约 | 数据 | 问题编号 |
|---|---|---|---|---|---|---|---|---|---|
| about | /about/index | about/index | loadjson | 已审 | 通过 | 通过 | 通过 | 通过 | | | | | |
| dashboard | /analysis/dashboard/index | dashboard/index | dataset | 已审 | 通过 | 通过 | 通过 | 通过 | |
| analysis/dataset | /analysis/dataset/index | dashboard/dataset/index | dataset | 已审 | 通过 | 通过 | 通过 | 通过 | |
| analysis/report | /analysis/report/index | analysis/report/index | dataset | 已审 | 通过 | 通过 | 通过 | 通过 | | | | | |
| analysis/screen | /analysis/screen/index | analysis/screen/index | dataset | 已审 | 通过 | 通过 | 通过 | 通过 | | | | | |
| approval/delegation | /approval/delegation/index | approval/delegation/index | approval | 已审 | 通过 | 通过 | 通过 | 通过 |  |
| approval/flow | /approval/flow/index | approval/flow/index | approval | 已审 | 通过 | 通过 | 通过 | 通过 |  |
| approval/index | /approval/index | approval/index | approval | 已审 | 通过 | 通过 | 通过 | 通过 |  |
| approval/instance | /approval/instance/index | approval/instance/index | approval | 已审 | 通过 | 通过 | 通过 | 通过 |  |
| approval/leave | /approval/leave/index | approval/leave/index | approval | 已审 | 通过 | 通过 | 通过 | 通过 |  |
| approval/rule | /approval/rule/index | approval/rule/index | approval | 已审 | 通过 | 通过 | 通过 | 通过 |  |
| chat | /chat/index | chat/index | message | 已审 | 通过 | 通过 | 通过 | 通过 | | | | | |
| form/data | /form-collection/data/index | form/data/index | dataset | 已审 | 通过 | 通过 | 通过 | 通过 | | | | | |
| form/designer | /form-collection/designer/index | form/designer/index | dataset | 已审 | 通过 | 通过 | 通过 | 通过 | | | | | |
| form/my | /form-collection/my/index | form/my/index | dataset | 已审 | 通过 | 通过 | 通过 | 通过 | | | | | |
| integration/ai/config | /integration/ai/config | integration/ai/config | ai | 已审 | 通过 | 通过 | 通过 | 通过 | |
| integration/ai | /integration/ai/index | integration/ai/index | ai | 已审 | 通过 | 通过 | 通过 | 通过 | |
| integration/ai/mcp | /integration/ai/mcp | integration/ai/mcp/index | ai | 已审 | 通过 | 通过 | 通过 | 通过 | |
| integration/api-app | /integration/api-app/index | integration/api-app/index | identity | 已审 | 通过 | 通过 | 通过 | 通过 | | | | | |
| integration/delivery | /integration/delivery/index | integration/delivery/index | task | 已审 | 通过 | 通过 | 通过 | 通过 | | | | | |
| integration/knowledge | /integration/knowledge/index | integration/knowledge/index | ai | 已审 | 通过 | 通过 | 通过 | 通过 | | | | | |
| integration/subscription | /integration/subscription/index | integration/subscription/index | task | 已审 | 通过 | 通过 | 通过 | 通过 | | | | | |
| settings/account-security | /settings/account-security | settings/account-security | settings | 已审 | 通过 | 通过 | 通过 | 通过 | |
| settings/basic | /settings/basic | settings/basic | settings | 已审 | 通过 | 通过 | 通过 | 通过 | |
| settings/captcha | /settings/captcha | settings/captcha | settings | 已审 | 通过 | 通过 | 通过 | 通过 | |
| settings/channel | /settings/channel | settings/channel | settings | 已审 | 通过 | 通过 | 通过 | 通过 | |
| settings/ldap | /settings/ldap | settings/ldap | settings | 已审 | 通过 | 通过 | 通过 | 通过 | |
| settings/ledger | /settings/ledger/index | settings/ledger/index | settings | 已审 | 通过 | 通过 | 通过 | 通过 | |
| settings/login-policy | /settings/login-policy/index | settings/login-policy/index | identity | 已审 | 通过 | 通过 | 通过 | 通过 | |
| settings/message | /settings/message | settings/message | loadjson | 已审 | 通过 | 通过 | 通过 | 通过 | SYSMSG-MISSING-ROW（P2 候选） |
| settings/open-platform | /settings/open-platform | settings/open-platform | settings | 已审 | 通过 | 通过 | 通过 | 通过 | |
| settings/security | /settings/security/index | settings/security/index | settings | 已审 | 通过 | 通过 | 通过 | 通过 | |
| settings/sms | /settings/sms | settings/sms | settings | 已审 | 通过 | 通过 | 通过 | 通过 | |
| system/account-risk | /system/account-risk/index | system/account-risk/index | identity | 已审 | 通过 | 通过 | 通过 | 通过 | |
| system/celery/crontab | /system/celery/crontab/index | system/celery/crontab/index | task | 已审 | 通过 | 通过 | 通过 | 通过 | |
| system/celery/interval | /system/celery/interval/index | system/celery/interval/index | task | 已审 | 通过 | 通过 | 通过 | 通过 | |
| system/celery/logs | /system/celery/logs/index | system/celery/logs/index | task | 已审 | 通过 | 通过 | 通过 | 通过 | |
| system/celery/task | /system/celery/task/index | system/celery/task/index | task | 已审 | 通过 | 通过 | 通过 | 通过 | |
| system/codegen | /system/codegen/index | system/codegen/index | system | 已审 | 通过 | 通过 | 通过 | 通过 | |
| system/config/system | /system/config/system/index | system/config/system/index | system | 已审 | 通过 | 通过 | 通过 | 通过 | |
| system/config/user | /system/config/user/index | system/config/user/index | system | 已审 | 通过 | 通过 | 通过 | 通过 | |
| system/credential | /system/credential/index | system/credential/index | system | 已审 | 通过 | 通过 | 通过 | 通过 | |
| system/dept | /system/dept/index | system/dept/index | identity | 已审 | 通过 | 通过 | 通过 | 缺口 | DATA-P1-01 |
| system/dict | /system/dict/index | system/dict/index | system | 已审 | 通过 | 通过 | 通过 | 通过 | |
| system/directory | /system/directory/index | system/directory/index | identity | 已审 | 通过 | 通过 | 通过 | 通过 | |
| system/export | /system/export/index | system/export/index | task | 已审 | 通过 | 通过 | 通过 | 通过 | |
| system/field | /system/field/index | system/field/index | system | 已审 | 通过 | 通过 | 通过 | 通过 | |
| system/file | /system/file/index | system/file/index | file | 已审 | 通过 | 通过 | 通过 | 通过 | |
| system/logs/login | /system/logs/login/index | system/logs/login/index | audit | 已审 | 通过 | 通过 | 通过 | 通过 | |
| system/logs/operation | /system/logs/operation/index | system/logs/operation/index | audit | 已审 | 通过 | 通过 | 通过 | 通过 | |
| system/mask | /system/mask/index | system/mask/index | audit | 已审 | 通过 | 通过 | 通过 | 通过 | |
| system/menu | /system/menu/index | system/menu/index | system | 已审 | 通过 | 通过 | 通过 | 通过 | |
| system/module | /system/module/index | system/module/index | system | 已审 | 通过 | 通过 | 通过 | 通过 | |
| system/monitor | /system/monitor/index | system/monitor/index | system | 已审 | 通过 | 通过 | 通过 | 通过 | |
| system/my-scope | /system/my-scope/index | system/my-scope/index | loadjson | 已审 | 通过 | 通过 | 通过 | 通过 | |
| system/notice | /system/notice/index | system/notice/index | notifications | 已审 | 通过 | 通过 | 通过 | 通过 | |
| system/notice/read | /system/notice/read/list | system/notice/read/list | notifications | 已审 | 通过 | 通过 | 通过 | 通过 | |
| system/online | /system/online/index | system/online/index | identity | 已审 | 通过 | 通过 | 通过 | 通过 | |
| system/permission | /system/permission/index | system/permission/index | system | 已审 | 通过 | 通过 | 通过 | 缺口 | DATA-P1-01 |
| system/post | /system/post/index | system/post/index | identity | 已审 | 通过 | 通过 | 通过 | 缺口 | DATA-P1-01 |
| system/role | /system/role/index | system/role/index | identity | 已审 | 通过 | 通过 | 通过 | 缺口 | DATA-P1-01 |
| system/tag | /system/tag/index | system/tag/index | system | 已审 | 通过 | 通过 | 通过 | 通过 | |
| system/user | /system/user/index | system/user/index | identity | 已审 | 通过 | 通过 | 通过 | 缺口 | SEC-P0-01 |
| account | /user/info/index | account/index | loadjson | 已审 | 通过 | 通过 | 通过 | 通过 | | | | | |
| user/notice | /user/notice/index | user/notice/index | notifications | 已审 | 通过 | 通过 | 通过 | 通过 | | | | | |

#### B. 框架 / 全屏 / 示例页（19）

| 页面 ID | 路由 | 组件 | 说明 | 状态 | 问题编号 |
|---|---|---|---|---|---|
| frame/welcome | /welcome | welcome/index | 首页 | 已审 | |
| frame/login | /login | login/index | 登录 | 已审 | |
| frame/redirect | /redirect/:path(.*) | layout/redirect.vue | 重定向 | 已审 | |
| frame/invite | /invite/accept | invite/accept | 邀请激活 | 已审 | |
| frame/oauth-callback | /oauth/callback | oauth/callback | OAuth 回跳 | 已审 | |
| frame/oauth-authorize | /oauth/authorize | oauth/authorize | OAuth 同意页 | 已审 | |
| frame/account-settings | /account-settings | account/index | 账户设置（无布局） | 已审 | |
| frame/403 | /error/403 | error/403 | 无权限 | 已审 | |
| frame/404 | /error/404 | error/404 | 不存在 | 已审 | |
| frame/500 | /error/500 | error/500 | 服务异常 | 已审 | |
| frame/module-disabled | /error/module-disabled | error/module-disabled | 模块停用 | 已审 | |
| demo/playground-index | /playground/index | playground/index | 示例/调试 | 已审 | |
| demo/playground-display | /playground/display | playground/display | 示例/调试 | 已审 | |
| demo/playground-layout | /playground/layout | playground/layout | 示例/调试 | 已审 | |
| demo/playground-input | /playground/input | playground/input | 示例/调试 | 已审 | |
| analysis/report/designer | /analysis/report/designer | analysis/report/designer | 全屏设计器 | 已审 | |
| analysis/screen/display | /analysis/screen/display | analysis/screen/display | 全屏投屏（kiosk） | 已审 | |
| analysis/screen/designer | /analysis/screen/designer | analysis/screen/designer | 全屏设计器 | 已审 | |
| dashboard/display | /analysis/dashboard/display | dashboard/index | 展板（kiosk） | 已审 | |

> 另有只做重定向的框架条目 `/system/setting/index → /settings/ledger/index`，不计入页面分母。
> `demo` 业务示例页（`src/views/demo`）未挂路由，属孤儿页，见 §2.4。

### 2.3 链路映射表

> 每批审查后填充：页面 → 前端组件 → `src/api` 封装 → 后端 ViewSet/Serializer/Model →
> 权限码 → 菜单种子行。未填 = 该批未审。

| 页面 ID | 前端组件 | src/api | 后端 app/ViewSet | 权限码 | 种子行 |
|---|---|---|---|---|---|
| system/user | `xadmin-client/src/views/system/user/index.vue` + `utils/hook.tsx` | `xadmin-client/src/api/identity/user.ts`（`userApi`）、`src/api/identity/dept.ts`（部门树） | `xadmin-server/identity/views/admin/user.py` `UserViewSet` + `SearchUserViewSet`（`identity/views/search/user.py`） | `list/create/retrieve/partialUpdate/destroy/batchDestroy/upload/exportData/importData/recycle*/empower/resetPassword/resetMfa/logout/unblock/invite/impersonate/imBinding/imUnbind/batchUpdate/preview/previewTrial/changeHistory:SystemUser` | `xadmin-server/identity/seeds/menu.json`（`SystemUser` 页面 + 27 个动作）、`menumeta.json` |
| system/role | `xadmin-client/src/views/system/role/index.vue` + `utils/hook.tsx` | `xadmin-client/src/api/identity/role.ts` | `xadmin-server/identity/views/admin/role.py` `RoleViewSet` + `SearchRoleViewSet` | `list/create/retrieve/partialUpdate/destroy/batchDestroy/recycle*/batchUpdate/exportData/importData/preview:SystemRole` | `xadmin-server/identity/seeds/menu.json`（SystemRole） |
| system/dept | `xadmin-client/src/views/system/dept/index.vue` + `utils/hook.tsx` | `xadmin-client/src/api/identity/dept.ts` | `xadmin-server/identity/views/admin/dept.py` `DeptViewSet` + `SearchDeptViewSet` | `list/create/retrieve/partialUpdate/destroy/batchDestroy/batchUpdate/assignManagers/empower/preview/previewTrial/exportData/importData/changeHistory:SystemDept` | `xadmin-server/identity/seeds/menu.json`（SystemDept） |
| system/post | `xadmin-client/src/views/system/post/index.vue` + `utils/hook.tsx` | `xadmin-client/src/api/identity/post.ts` | `xadmin-server/identity/views/admin/post.py` `PostViewSet` + `SearchPostViewSet` | `list/create/retrieve/partialUpdate/destroy/batchDestroy/recycle*/batchUpdate/members/assign/preview:SystemPost` | `xadmin-server/identity/seeds/menu.json`（SystemPost） |
| system/directory | `xadmin-client/src/views/system/directory/index.vue` + `components/Directory{Aside,Members}.vue` | `xadmin-client/src/api/identity/directory.ts`、`src/api/system/search.ts` | `xadmin-server/identity/views/user/directory.py` `DirectoryViewSet` + `SearchDeptViewSet` / `SearchPostViewSet` | `list:SystemDirectory` / `list:SearchDept` / `list:SearchPost` | `xadmin-server/identity/seeds/menu.json`（SystemDirectory） |
| system/menu | `xadmin-client/src/views/system/menu/index.vue` + `utils/hook.tsx` | `xadmin-client/src/api/system/menu.ts` | `xadmin-server/system/views/admin/menu.py` `MenuViewSet` | `list/create/retrieve/partialUpdate/destroy/batchDestroy/recycle*/rank/permissions/apiUrl/impact/batchUpdate/permissionAudit/exportData/importData:SystemMenu` | `xadmin-server/system/seeds/menu.json`（SystemMenu） |
| system/permission | `xadmin-client/src/views/system/permission/index.vue` + `utils/hook.tsx` | `xadmin-client/src/api/system/permission.ts` | `xadmin-server/system/views/admin/permission.py` `DataPermissionViewSet` | `list/create/retrieve/partialUpdate/destroy/batchDestroy/recycle*/impact/exportData/importData:SystemDataPermission` | `xadmin-server/system/seeds/menu.json`（SystemDataPermission） |
| system/field | `xadmin-client/src/views/system/field/index.vue` + `utils/hook.tsx` | `xadmin-client/src/api/system/field.ts` | `xadmin-server/system/views/admin/modelfield.py` `ModelLabelFieldViewSet` | `list/retrieve/sync/exportData/sync:SystemModelLabelField`（只读台账，无写动作） | `xadmin-server/system/seeds/menu.json`（SystemModelLabelField） |
| system/dict | `xadmin-client/src/views/system/dict/index.vue` + `utils/hook.tsx` | `xadmin-client/src/api/system/dict.ts` | `xadmin-server/system/views/admin/dict.py` `DataDictViewSet` | `list/create/retrieve/partialUpdate/destroy/batchDestroy/batchUpdate/batchActive/refreshCache/move/items/impact/exportData/importData:SystemDataDict` | `xadmin-server/system/seeds/menu.json`（SystemDataDict） |
| system/config/system | `xadmin-client/src/views/system/config/system/index.vue` + `utils/hook.tsx` | `xadmin-client/src/api/system/config/system.ts` | `xadmin-server/system/views/admin/config.py` `SystemConfigViewSet` | `list/create/retrieve/partialUpdate/destroy/invalid/registered-keys/exportData/importData:SystemConfig` | `xadmin-server/system/seeds/menu.json`（SystemConfig） |
| system/config/user | `xadmin-client/src/views/system/config/user/utils/hook.tsx` | `xadmin-client/src/api/system/config/user.ts` | `xadmin-server/system/views/admin/config.py` `UserPersonalConfigViewSet` | `list/create/retrieve/partialUpdate/destroy/invalid/exportData/importData:UserConfig` | `xadmin-server/system/seeds/menu.json`（UserConfig） |
| system/credential | `xadmin-client/src/views/system/credential/index.vue` + `components/CredentialTable.vue` | `xadmin-client/src/api/system/credential.ts` | `xadmin-server/system/views/admin/credential.py` `CredentialViewSet` | `overview:Credential` / `rotate:Credential` | `xadmin-server/system/seeds/menu.json`（SystemCredential） |
| system/tag | `xadmin-client/src/views/system/tag/index.vue` + `utils/hook.tsx` | `xadmin-client/src/api/system/tag.ts` | `xadmin-server/system/views/platform/tag.py` `TagViewSet` | `list/create/retrieve/partialUpdate/destroy/resources/objects/assign/batchAssign:Tag` | `xadmin-server/system/seeds/menu.json`（SystemTag） |
| system/module | `xadmin-client/src/views/system/module/index.vue` + `utils/hook.ts` | `xadmin-client/src/api/system/modules.ts` | `xadmin-server/system/views/platform/modules.py` `SystemModuleViewSet` | `list/apply/reset:SystemModule` | `xadmin-server/system/seeds/menu.json`（SystemModule） |
| system/logs/login | `xadmin-client/src/views/system/logs/login/index.vue` + `utils/hook.tsx` | `xadmin-client/src/api/audit/logs/login.ts` | `xadmin-server/audit/views/admin/loginlog.py` `LoginLogViewSet` | `list/exportData/logout:SystemUserLoginLog` | `xadmin-server/audit/seeds/menu.json`（SystemUserLoginLog） |
| system/logs/operation | `xadmin-client/src/views/system/logs/operation/index.vue` + `utils/hook.tsx` | `xadmin-client/src/api/audit/logs/operation.ts` | `xadmin-server/audit/views/admin/operationlog.py` `OperationLogViewSet` | `list/retrieve/exportData/changeHistory:SystemOperationLog/SystemUser/SystemDept`（共享端点声明） | `xadmin-server/audit/seeds/menu.json`（SystemOperationLog） |
| system/mask | `xadmin-client/src/views/system/mask/index.vue` + `utils/hook.tsx` | `xadmin-client/src/api/system/mask.ts` | `xadmin-server/audit/views/admin/mask.py` `DataMaskRuleViewSet` | `list/create/retrieve/partialUpdate/destroy/preview/exportData/importData:SystemDataMaskRule` | `xadmin-server/audit/seeds/menu.json`（SystemDataMaskRule） |
| system/online | `xadmin-client/src/views/system/online/index.vue` + `utils/hook.tsx` | `xadmin-client/src/api/identity/online.ts` | `xadmin-server/identity/views/admin/online.py` `UserOnlineViewSet` | `list/destroy/forceLogout/batchForceLogout:SystemUserOnline` | `xadmin-server/identity/seeds/menu.json`（SystemUserOnline） |
| system/account-risk | `xadmin-client/src/views/system/account-risk/index.vue` + `utils/hook.tsx` | `xadmin-client/src/api/system/security.ts` | `xadmin-server/identity/views/admin/account_risk.py` `AccountRiskViewSet` | `list/scan/scanStatus/handle/batchHandle/stats:SystemAccountRisk` | `xadmin-server/identity/seeds/menu.json`（SystemAccountRisk） |
| system/celery/task | `xadmin-client/src/views/system/celery/task/index.vue` + `utils/hook.ts` | `xadmin-client/src/api/task/task.ts` | `xadmin-server/task/views/task_periodic.py` `PeriodicTaskViewSet` | `list/create/retrieve/partialUpdate/destroy/enable/run/clone/batchRun/batchEnable/batchDisable/registered/exportData/importData:SystemTask` | `xadmin-server/task/seeds/menu.json`（SystemTask） |
| system/celery/crontab | `xadmin-client/src/views/system/celery/components/SchedulePage.vue` | `xadmin-client/src/api/task/task.ts` | `xadmin-server/task/views/task_periodic.py` `CrontabScheduleViewSet` | `list/create/retrieve/partialUpdate/destroy/references/preview:SystemTaskCrontab` | `xadmin-server/task/seeds/menu.json`（SystemTaskCrontab） |
| system/celery/interval | `xadmin-client/src/views/system/celery/components/SchedulePage.vue` | `xadmin-client/src/api/task/task.ts` | `xadmin-server/task/views/task_periodic.py` `IntervalScheduleViewSet` | `list/create/retrieve/partialUpdate/destroy/references:SystemTaskInterval` | `xadmin-server/task/seeds/menu.json`（SystemTaskInterval） |
| system/celery/logs | `xadmin-client/src/views/system/celery/logs/index.vue` + `utils/hook.tsx` | `xadmin-client/src/api/task/task.ts` | `xadmin-server/task/views/task.py` `TaskExecutionViewSet` + `task_center.py` | `list/retrieve/destroy/batchDestroy/log/stats/cancel/rerun:SystemTaskExecution` | `xadmin-server/task/seeds/menu.json`（SystemTaskExecution） |
| system/export | `xadmin-client/src/views/system/export/index.vue` + `utils/recordCenter.ts` | `xadmin-client/src/api/task/export.ts` / `import.ts` | `xadmin-server/task/views/admin/export.py` / `import_.py` / `task_center.py` | `list/retrieve/destroy/batchDestroy/download/log/stats:SystemExportRecord/SystemImportRecord` + `rerun:SystemTaskExecution` | `xadmin-server/task/seeds/menu.json` |
| system/file | `xadmin-client/src/views/system/file/index.vue` + `utils/hook.tsx` | `xadmin-client/src/api/file/file.ts` | `xadmin-server/file/views/admin/file.py` `UploadFileViewSet` + `file_chunk.py` | `list/create/retrieve/partialUpdate/destroy/upload/download/preview/accessLogs/config/stats/recycle*:SystemUploadFile` | `xadmin-server/file/seeds/menu.json`（SystemUploadFile） |
| system/codegen | `xadmin-client/src/views/system/codegen/index.vue` + `utils/payload.ts` | `xadmin-client/src/api/system/codegen.ts` | `xadmin-server/system/views/admin/codegen.py` `SystemCodeGenViewSet` + `codegen_plan.py` | `models/modelFields/preview/download/aiDraft:SystemCodeGen` + 方案 CRUD | `xadmin-server/system/seeds/menu.json`（SystemCodeGen） |
| system/monitor | `xadmin-client/src/views/system/monitor/index.vue` + `utils/hook.ts` | `xadmin-client/src/api/system/monitor.ts` | `xadmin-server/system/views/platform/monitor.py` `MonitorViewSet` | `list/services/redisInfo/celeryInfo/slowList/taskHealth/history/events/export:SystemMonitor` + `thresholds/updateThresholds` | `xadmin-server/system/seeds/menu.json`（SystemMonitor） |
| system/my-scope | `xadmin-client/src/views/system/my-scope/index.vue` | `xadmin-client/src/api/identity/dept.ts` | `xadmin-server/identity/views/admin/dept.py::managed` | `managed:SystemDept` + `list:SystemUser`（成员跳转） | `xadmin-server/loadjson/menu.json`（SystemMyScope） |
| system/notice | `xadmin-client/src/views/system/notice/index.vue` + `utils/hook.tsx` | `xadmin-client/src/api/system/notice.ts` | `xadmin-server/notifications/views/message.py` | `list/create/retrieve/partialUpdate/destroy/batchDestroy/publish/announcement/recycle*:SystemNotice` | `xadmin-server/notifications/seeds/menu.json`（SystemNotice） |
| system/notice/read | `xadmin-client/src/views/system/notice/read/list.vue` + `hook.tsx` | `xadmin-client/src/api/system/notice.ts` | `xadmin-server/notifications/views/message.py` | `list/retrieve/destroy/batchDestroy/state/allRead:SystemNoticeRead` | `xadmin-server/notifications/seeds/menu.json`（SystemNoticeRead） |
| dashboard | `xadmin-client/src/views/dashboard/index.vue` + `components/ChartCard.vue` | `xadmin-client/src/api/dataset/datasets.ts` | `xadmin-server/dataset/views/dataset.py` `DashboardViewSet` + `dataset/dashboard_data.py` / `ws_dashboard.py` | `list/create/retrieve/partialUpdate/destroy:DataDashboard`（卡片执行走数据集 execute/aggregate） | `xadmin-server/dataset/seeds/menu.json`（DataDashboard） |
| analysis/dataset | `xadmin-client/src/views/dashboard/dataset/index.vue` + `utils/hook.tsx` | `xadmin-client/src/api/dataset/datasets.ts` | `xadmin-server/dataset/views/dataset.py` `DatasetViewSet` + `dataset/utils/dataset*.py` | `list/create/retrieve/partialUpdate/destroy/execute/aggregate/meta:DataDataset` | `xadmin-server/dataset/seeds/menu.json`（DataDataset） |
| integration/ai | `xadmin-client/src/views/integration/ai/index.vue` + `hooks/useAiConsole.ts` | `xadmin-client/src/api/ai/ai.ts` | `xadmin-server/ai/views/assistant.py` + `nl_query.py` + `actions.py` + `services/nl_query/orchestrator.py` | `ask/interpret/run/actionExecute/status/history/tools:AiAssistant` | `xadmin-server/ai/seeds/menu.json`（AiAssistant） |
| integration/ai/config | `xadmin-client/src/views/integration/ai/config.vue` + `utils/useAiProfiles.ts` | `xadmin-client/src/api/ai/ai.ts` | `xadmin-server/ai/views/profiles.py` + `AiAssistantSettingViewSet` | `list/create/retrieve/partialUpdate/destroy/activate/deactivate/test/probe:AiProfile` + `Setting` 系列 | `xadmin-server/ai/seeds/menu.json`（AiAssistantConfig） |
| integration/ai/mcp | `xadmin-client/src/views/integration/ai/mcp/index.vue` + `utils/hook.tsx` | `xadmin-client/src/api/ai/mcp.ts` | `xadmin-server/ai/views/mcp.py` `McpServerViewSet` + `McpEndpointAPIView` | `list/create/retrieve/partialUpdate/destroy/sync/tools/call/logs:AiMcpServers` | `xadmin-server/ai/seeds/menu.json`（AiMcpServers） |

## 3. 页面详述

> 只展开有 P0/P1 或 ≥1 个 P2 的页面，每页 ≤60 行 / 2KB；无问题页面只进 §2.2 总表。

### 3.1 用户管理（system/user）

- 页面：`/system/user/index` → `xadmin-client/src/views/system/user/index.vue`（109 行）；
  后端 `identity/views/admin/user.py::UserViewSet`（371 行）+ `identity/views/search/user.py::SearchUserViewSet`。
- 前端链路：`index.vue → utils/hook.tsx → userActions.tsx / useUserPanel / useUserRowHandlers /
  useUserColumnFormats / useUserFormOptions / useUserRoleRules → api/identity/user.ts`。
  列表配置走 `RePlusPage`（`defineListPage` + `with_meta` 内联元数据），只读表格用
  `ReReadonlyTable`，弹层走 `addDialog/addDrawer/ReActionPanel`，符合页面范式。
- 动作权限：`defineListPage` 的 `extraAuth` 覆盖 resetPassword/empower/logout/unblock/
  resetMfa/preview/changeHistory/imBinding/invite/impersonate；`imUnbind` 在
  `ImBindingPanel.vue:30` 单独 `hasAuth`；`previewTrial` 在 `PermissionPreview.vue:36`
  单独判定；`batchUpdate` 在 `useUserToolbarButtons.ts:59` 判定。与种子 27 个动作逐项对齐。
- 元数据：`UserSerializer.table_fields`（30 个字段）与列表列格式（头像/用户名/性别/锁定/
  邀请状态/标签/岗位）逐项对应；`search-fields` 由 `UserFilter`（10 个筛选项）驱动；
  `dept` 表单级联由 `search-columns` 的 choices 构建。
- 契约：`src/api/types/permission-preview.ts` 与
  `xadmin-server/system/utils/platform/permission_preview/previews.py:28-65` 响应键逐项一致。
- **本页问题**：
  - `SEC-P0-01`（数据泄漏，A 级）：同页复用的用户搜索接口 `/api/identity/search/user`
    通过 `SearchUserSerializer` 返回 `password` 哈希（详见登记簿）。前端消费点：
    `src/components/SearchPicker/index.vue:58`（通知收件人、审批转交、登录策略目标等选人场景）。
- 其余观察（候选池，未定级）：`UserTree.vue:76` 的 `collectNodePks` 声明 `number[]`，而部门
  主键为 UUID 字符串（类型不精确，运行期按 `node-key="pk"` 仍可用）；
  `PreviewTrial.vue:60-64` 只处理异常、未显式判定 `isSuccess(res)`（后端试算失败目前
  走 4xx，未构成实际静默；待后续契约统一复核）。

### 3.2 角色管理（system/role）

- 页面链路：`role/index.vue`（47 行）→ `utils/hook.tsx`（92 行）→
  `useRoleMenuTree`（菜单全量 + 字段节点注入）/ `useRoleFormOptions`（授权树）/
  `RoleForm.vue`（回显失败遮罩）/ `useRoleColumns` / `useRoleButtons` → `api/identity/role.ts`。
- 权限闭环：`preview:SystemRole` 走 `extraAuth`；新增/编辑受 `list:SystemMenu` +
  `retrieve:SystemRole` 双重前置（`hook.tsx:29-69`）——缺 retrieve 时禁编辑，避免空树提交
  清空授权；`beforeSubmit` 在未触碰授权树时不提交 `menu/fields`，后端「未提交键不覆盖」。
- 元数据：`RoleSerializer.table_fields` 与列格式一致；列表 `ListRoleSerializer` 的
  `field` 仅为空数组占位，编辑回显走 retrieve 的 `{menuPk: [fieldPk]}`（`role.py:86-95`）。
- 本页问题：`DATA-P1-01`（角色「用户数」包含软删除用户；列表注解与单对象回退口径不一致）。
- 候选池：`RoleSerializer.create` 的 `fields` 无缺省（`role.py:129` `validated_data.pop("fields")`），
  API 直接创建角色必须显式传 `{}`；前端新增已兜底传空字典（`useRoleFormOptions.ts:62-66`）。

### 3.3 部门管理（system/dept）

- 页面链路：`dept/index.vue` → `utils/hook.tsx`（97 行）→ `useDeptColumns`（人数跳转）/
  `useDeptFormOptions` / `useDeptRoleRules` / `useDeptManagers` / `useDeptPanel` →
  `api/identity/dept.ts`；管理抽屉走 `ReActionPanel`，授权预览走统一预设
  `openPermissionPreview`。
- 权限闭环：`extraAuth` = empower/preview/assignManagers/changeHistory；管理入口按
  「预览 || 用户列表 || 授权 || 管理员」任一权限展示（`hook.tsx:81-86`）；成员跳转按
  `list:SystemUser` 且 count>0 才可点（`deptColumns.tsx:26-31`）。
- 数据闭环：`DeptSerializer` 创建时默认挂到操作者部门（`department.py:91-102`）；
  上级循环校验（:144-146）；`managers` 只读、写口唯一 `assign-managers`（:73-96）。
- 本页问题：`DATA-P1-01`（「用户数」含软删除用户）。
- 候选池：`_assign_authorizations` 对越权 roles/rules 静默丢弃（`department.py:113-132`），
  与用户页 `_assert_scope_fields` 的显式拒绝口径不一致（用户可能以为授权成功）。

### 3.4 岗位管理（system/post）

- 页面链路：`post/index.vue` → `utils/hook.tsx` → `usePostColumns` / `usePostButtons` /
  `usePostFormOptions` / `PostMembersDialog.vue`（`MemberTagEditor`，增量 `{add, remove}`）→
  `api/identity/post.ts`。
- 权限闭环：`members:SystemPost`（只读成员）与 `assign:SystemPost`（分配）**双权限点**建模；
  只有 assign 时以可编辑打开，只有 members 时只读打开（`hook.tsx:28-45`、`usePostButtons.ts:42`）。
- 元数据：`table_fields` 含 `dept_name/user_count`；`dept` 无权限时表单选项禁用（`usePostFormOptions.ts:22-31`）。
- 本页问题：`DATA-P1-01`（「成员数」含软删除用户；成员清单按在用用户过滤，前后不一致）。
- 候选池：`PostViewSet.members/assign`（`post.py:76-122`）未按调用者数据范围过滤目标用户/
  名单，只持有 `members:SystemPost` 即可看到岗位内全部用户名（不涉及权限授予，影响有限）。

### 3.5 通讯录（system/directory）

- 页面链路：`directory/index.vue` → `DirectoryAside.vue`（部门树 `search/dept`、岗位清单
  `search/post`）→ `DirectoryMembers.vue`（`usePagedList` + 服务端分页，卡片/列表双视图）→
  `MemberDetail.vue`；数据源 `api/identity/directory.ts`。
- 权限闭环：整页受 `list:SystemDirectory`；部门/岗位维度分别受 `list:SearchDept` /
  `list:SearchPost`；无权限时显示 `ReEmpty` 兜底（`index.vue:40-74`）。
- 数据闭环：`DirectoryViewSet` 以 `UserInfo.objects.filter(is_active=True)` 为基线，
  数据权限随全局过滤器；`DirectorySerializer` 字段集精简（无角色/数据权限等管理面字段，
  `directory.py:12-39`）。
- 结论：四闭环通过；无 P0/P1。候选池：页面未走 RePlusPage（展示型卡片/列表，非标准表格），
  与「列表页范式」存在形式差异，但无 el-table/权限逻辑手写问题。

## 4. 专项汇总（续）

> 主篇 §4.2–4.8 的展开；结论均来自本轮静态阅读，运行期验证见主篇 §9.2。

### 4.2 元数据协议专项

- 列表页主路径统一为 `SearchFieldsAction`/`SearchColumnsAction` + `table_fields` → 前端
  RePlusPage/ReTable 消费；菜单种子提供 `search-columns`/`search-fields` 的字段与动作来源。
- 偏离主路径的页面均已逐页确认是「展示型/非表格」而非漏接：通讯录（卡片+分页）、聊天室
  （消息流+WS）、仪表盘/大屏（画布/可视化）、动态表单填写页（表单渲染器）——不涉及
  手写 `el-table` 与权限判断。
- 字段权限在 `BaseModelSerializer` 做 fail-closed 裁剪（未授权字段不输出），受控 lookup
  在过滤层做同口径字段白名单；两者共用 `request.fields`，未发现「列表隐藏、导出泄漏」的
  静态路径。
- 结论：协议闭环成立；`SEC-P0-01` 是序列化器输出面问题（派生字段绕过字段权限/写读约束），
  不是元数据下发链路问题。

### 4.3 契约专项

- 服务端 schema 为真源（`docs/schema/`），OpenAPI 768 条端点、前端生成类型与 schema 镜像
  一致；`check:contract` 6/6 生成物有消费方，未发现两侧形状漂移。
- 响应封包统一 `code=1000` 成功，业务码 1001/1002/… 由后端明示；前端成功判定已收敛
  （`success-code` 仅 1 处基线），未发现以 HTTP 200 掩盖失败的新路径。
- 共享端点：4 组 `(path,method)` 多权限码均在视图声明处用 `@shared_permissions` 登记，
  运行时解析以 path 为键的覆盖风险由注册表优先级 + 守护测试兜底。
- 盲区：`SEC-P0-01` 说明「生成类型通过」不等于「输出字段安全」——OpenAPI 门禁只证明
  形状一致，不证明字段该不该出现；修复时需同步重新生成类型。

### 4.4 数据权限与字段权限专项

- 数据权限：`get_filter_queryset` 无授权即 `none()`（fail-closed），部门祖先链 + 个人授权
  同池 OR 合并；授权池按「版本号 + 用户 + 部门 + 菜单上下文」短缓存，变更经信号自增版本。
- 菜单上下文：显式 action 授权优先、无可用显式授权回退同资源 list；页面/目录上下文展开
  为其下 `list:*`，与运行时列表口径一致。
- 字段权限：序列化器 fail-closed；受控 lookup 对非超管校验字段白名单；`PkMultipleFilter`
  等选项接口按同一字段面收敛。
- 已登记问题：`DATA-P1-01`（关系计数未滤软删除）；候选：岗位成员越数据范围、部门授权
  静默丢弃、`/media/` 仅登录态（C-02）。

### 4.5 安全专项（静态）

- 认证：JWT 黑名单/用户级/会话级失效合并一次读取；PAT scope + IP 白名单 fail-closed；
  OAuth state 一次性 + 浏览器 Cookie 绑定 + OIDC nonce；Passkey 挑战一次性消费与计数器
  单调性；MFA 确认级别校验、恢复码哈希一次性认领。
- 输入面：验证码票据 HMAC 签名 + 一次性 nonce + scene/action/sid/fp/IP 绑定；受控 lookup
  只放行九种 lookup 且禁跨关系；上传按扩展名/大小/配额并做归属校验；聊天附件仅本人上传件
  可引用、取件按房间成员鉴权。
- 出站：Webhook/AI/MCP/OAuth 统一 `pinned_request`（协议白名单、地址归属、白名单、固定
  解析、逐跳重定向复检）；凭据落库 v3（HKDF+AES-GCM）并按声明清单掩码。
- 已登记：`SEC-P0-01`、`AUTH-P1-01`；候选：坏密文 500、注册通道复核。未发现 SQL 拼接/
  模板注入/任意文件读取的静态路径；日志脱敏名单覆盖口令/令牌/密钥字段。

### 4.6 性能与容量专项（静态）

- 列表查询：元数据驱动的分页 + 关联 `select_related/prefetch_related`（模型集自动预取），
  未发现列表页逐行查库的新路径；字典 usage 每行 3 次读取为候选。
- 权限与缓存：授权池短缓存 + 版本失效；字段权限随请求缓存；验证码/限流/锁定均走 Redis
  原子计数；在线用户/WS 推送按批处理，避免逐用户桥接。
- 前端：bundle 代码 +11.3KB/15KB、i18n +7.1KB/15KB，主 chunk 69.7KB，仍在预算内但余量
  收窄；路由级懒加载与入口闭包 45 条通过。
- 限制：本轮未做压测/火焰图，容量结论只到「静态无放大路径」；E2E perf 未执行。

### 4.7 可靠性与可观测专项（静态）

- 异常：全局处理器把未预期异常归一为 500 通用文案（不泄内部细节），业务异常保留可读
  detail；限流/配额用 `ReadableThrottled` 保留可区分语义。
- 审计：操作日志按方法/敏感 GET 白名单落库，请求体脱敏；慢请求阈值可配；登录成功/失败/
  策略命中均有日志与 Webhook 事件。
- 验证码：决策/校验/消费三阶段遥测 + 告警阈值；处置层冷却与登录锁定联动；通知渠道对
  未绑定用户以 warning 聚合留痕。
- 运行期未验证：Celery 重试/取消、WS 推送、通知渠道真实投递、指标端点（默认不挂载）。

### 4.8 二开与规范符合度

- 跨 app 依赖：`check_cross_app_imports` 通过（allowlist 18 项、框架契约缝 8 条、反向依赖 0）；
  业务层只经 `services.py`/`contracts` 消费他 app。
- 扩展点：模块注册表按预设裁剪；新增列表页/动作需同步种子权限点与 docstring 首行；本轮
  84 页的权限码与菜单种子逐项对账，未发现未入库动作（通配告警 13 条已单列）。
- 规范：台账编号 0、文件/函数行数 0 超限、i18n 对称、文档索引/路径/ADR 全通过；前端
  23 项 `check:*` 通过。
- 结论：二开范式可复用；本报告登记的问题均为实现层缺陷，不是规范体系缺口。
