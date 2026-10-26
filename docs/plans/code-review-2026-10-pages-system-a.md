# 2026-10 全栈功能审查 · 分册：系统域 A（用户组织权限核心 12 页）

> 主册：[code-review-2026-10.md](code-review-2026-10.md)。证据等级：A=代码直接确证，B=跨文件静态推断，C=待运行时验证。
> 本批页面：用户 / 部门 / 岗位 / 标签 / 目录 / 角色 / 数据权限 / 模型标签字段 / 菜单 / 数据字典 / 我的数据范围 / 个人访问令牌。

## 1. 用户管理（/system/user/index）

- **标识**：identity · core_rbac · `system/user/index.vue` + `utils/hook.tsx`（9 个 composable 装配）+ `useUserOptions/Buttons/Panel/...` · 元数据驱动（RePlusPage + defineListPage）· 后端 `identity/views/admin/user.py UserViewSet`（27 条路由）。
- **链路**：`userApi` → `UserViewSet` → `UserSerializer` → `UserInfo`；权限点 29 个（identity 28 + audit `changeHistory:SystemUser`），全量对账通过（主册 §3.1）。
- **前端实现**：桌面 ReSplitPane 左树右表（宽度持久化）/ 移动堆叠双分支共用 plusPageProps（`index.vue:41-50`）；`extraAuth` 10 个动作键与种子一一对应（hook.tsx:32-43）；角色页跳转 `?role=<pk>` 由 routeParams 装配（注释明确序号覆盖陷阱，hook.tsx:99-101）。
- **后端实现**：标准 BaseModelSet 家族（list/create/批量/导入导出/回收站/impersonate/invite 等）；行级动作确认：invite/impersonate 均走 `useConfirm`（`useUserRowHandlers.ts:69,89`）。
- **闭环校验**：权限闭环 ✅（29 点全对账）；元数据闭环 ✅（RePlusPage 消费）；契约闭环 ✅（api 层统一封装）；数据闭环 ✅（数据权限经全局 BaseDataPermissionFilter，B）。
- **问题清单**：无。
- **质量速记**：安全 ✅（重置密码带强度规则、impersonate 有确认+审计）；性能 ✅（composable 拆分后单文件均 <150 行）；可观测 ✅（changeHistory 挂操作日志）；兼容 ✅（移动端分支）。

## 2. 部门管理（/system/dept/index）

- **标识**：identity · core_rbac · 薄壳 `dept/index.vue`（48 行）+ hook · 元数据驱动 · 后端 `identity/views/admin/dept.py DeptViewSet`（18 路由）。
- **链路**：`deptApi` → `DeptViewSet` → `DeptSerializer` → `DeptInfo`。
- **前端实现**：树表（`isTree`）+ 硬删语义警示 alert（`index.vue:28-34`）。
- **后端实现**：`parent` FK PROTECT、`leader` SET_NULL（`identity/models/department.py:28,36`）；带子部门/成员删除触发 `ProtectedError`，由全局异常处理器转业务错误（`packages/xadmin-common/common/core/response/exception.py:79`）——硬删前置条件后端强约束，前端 alert 与之呼应；`assign-managers` 单写口装配预置角色与数据权限（`dept.py:73-96`）；`managed` 恒定本人范围（`dept.py:117-168`）；parent/leader 写侧均做数据范围校验（`identity/serializers/department.py:90-110`）。
- **闭环校验**：权限 ✅；元数据 ✅；契约 ✅；数据 ✅（PROTECT + 范围校验 + 审批纳管 destroy/batch-destroy）。
- **问题清单**：无。
- **质量速记**：安全 ✅（写侧范围校验覆盖 leader/parent）；性能 ✅（user_count 注解 + DynamicPageNumber(1000)）。

## 3. 岗位管理（/system/post/index）

- **标识**：identity · core_rbac · 薄壳 31 行 · 元数据驱动 · 后端 `identity/views/admin/post.py PostViewSet`（13 路由）。
- **链路**：`postApi` → `PostViewSet` → `PostSerializer` → `Post`。
- **后端实现**：`members` 动作 500 条截断并以 total/truncated 显式标记（`post.py:24-29`，防「悄悄少人」）；select_related(dept)；批量更新白名单仅 is_active。
- **闭环校验**：权限 ✅；元数据 ✅；契约 ✅；数据 ✅。
- **问题清单**：无。

## 4. 标签管理（/system/tag/index）

- **标识**：system · core_rbac · 薄壳 36 行 · 元数据驱动 · 后端 `system/views/platform/tag.py TagViewSet`（8 路由）。
- **链路**：`tagApi` → `TagViewSet` → `Tag`；打标动作回落**业务对象写权限点**（与 AI 动作同一匹配函数，`tag.py` 模块注释），不新增对象级权限点。
- **后端实现**：`objects` 读取要求对目标对象可见，不可见与不存在同响应 404 防主键探测（模块注释 :5-8）；被引用标签拒绝删除；usage_count 注解（`tag.py:83`）。
- **闭环校验**：权限 ✅（回落机制有明确声明面 TAGGABLE_MODELS）；元数据 ✅；契约 ✅；数据 ✅。
- **问题清单**：无。
- **质量速记**：安全 ✅（404 防探测、可见域口径显式化）。

## 5. 通讯录（/system/directory/index）

- **标识**：identity · core_rbac · `directory/index.vue`（76 行）· 元数据驱动（只读 OnlyListModelSet）· 后端 `identity/views/user/directory.py`（3 路由）。
- **链路**：`directoryApi` → `DirectoryViewSet` → `DirectorySerializer` → `UserInfo`（仅 is_active）。
- **后端实现**：只读无写动作；数据权限随全局 BaseDataPermissionFilter；按部门浏览含全部下级（`recursion_dept_info`，`directory.py:43-50`）；岗位维度显式过滤软删除（M2M join 不经默认管理器，模块注释 :8-9）。
- **闭环校验**：权限 ✅（只读 + 数据权限收敛）；元数据 ✅；契约 ✅；数据 ✅。
- **问题清单**：无。
- **质量速记**：隐私 ✅（phone/email 可检索但受数据权限与脱敏序列化约束，B）。

## 6. 角色管理（/system/role/index）

- **标识**：identity · core_rbac · `role/index.vue`（46 行）+ `utils/hook.tsx`（69 行）+ 4 子 composable · 元数据驱动 · 后端 `identity/views/admin/role.py RoleViewSet`（17 路由）。
- **链路**：`roleApi` → `RoleViewSet` → `RoleSerializer` → `UserRole`。
- **前端实现**：授权树不可用（缺 `list:SystemMenu` 或加载失败）时**禁用新增/编辑并显示警示**——空树上保存会清空角色授权，防呆设计（`hook.tsx:29-46`，同时关 create/update/partialUpdate 三键，注释明确「只关 update 仍会露出口子」）。
- **后端实现**：内置角色三入口保护——destroy/batch_destroy 的 queryset 排除（`role.py:62-69`）、回收站 purge 排除（:71-73）、perform_update 拒改 code/is_active（:75-87）；删除/批删 `@ApprovalRequired` 可纳审批。
- **闭环校验**：权限 ✅；元数据 ✅；契约 ✅；数据 ✅（内置角色防误删 + 审批高危）。
- **问题清单**：无。
- **质量速记**：可靠 ✅（前端防呆 + 后端三入口同口径，双保险）。

## 7. 数据权限（/system/permission/index）

- **标识**：system · core_rbac · 薄壳 38 行 + hook（依赖注册表缺失时 alert + 禁用新增/编辑）· 元数据驱动 · 后端 `system/views/admin/permission.py DataPermissionViewSet`（15 路由）。
- **链路**：`permissionApi` → `DataPermissionViewSet` → `DataPermissionSerializer` → `DataPermission`。
- **后端实现**：list 注解三个范围计数并 `distinct=True` 纠正多关系放大（`permission.py:54-64`）；删除/批删可纳审批。
- **闭环校验**：权限 ✅；元数据 ✅；契约 ✅；数据 ✅。
- **问题清单**：无。

## 8. 模型标签字段（/system/field/index）

- **标识**：system · core_rbac · 薄壳 22 行（`selection=false` 显式关闭多选列，符合约定）· 元数据驱动 · 后端 `system/views/admin/modelfield.py ModelLabelFieldViewSet`。
- **链路**：`modelFieldApi` → `ModelLabelFieldViewSet` → `ModelLabelField`。
- **后端实现**：choices / lookups / sync（模型字段同步）/ export 动作；字段树是字段权限与数据权限的注册表面。
- **闭环校验**：权限 ✅（含 `sync:SystemModelLabelField` 等点）；元数据 ✅；契约 ✅；数据 ✅（sync 幂等，C：未实跑）。
- **问题清单**：无。

## 9. 菜单管理（/system/menu/index）

- **标识**：system · core_rbac · `menu/index.vue`（192 行，自研树页 + 抽屉编辑，非 RePlusPage——树是主心智，取舍有文档注释）· 后端 `system/views/admin/menu.py MenuViewSet`（21 路由）。
- **链路**：`menuApi` → `MenuViewSet` → `MenuSerializer` → `Menu`。
- **前端实现**：工具栏/行内 hover/右键菜单三层入口共用同一动作清单；拖拽排序按 `auth.rank` 门控（`index.vue:116`）；高度实测 hook 取代 calc 魔法值。
- **后端实现**：内置 `permission-audit` 只读检测（正向缺口/游离/重复/字段未配置四类，`menu.py:184-226`）——与主册 §3.1 的机械对账同源，运维面闭环；批量生成权限 dry_run 预览 + 零匹配显式报错不静默（`menu.py:296-302`）；回收站按 deleted_at 成组恢复/后代先于父级清除（:84-97）；写后精确失效缓存（:308-310）。
- **闭环校验**：权限 ✅；元数据 ✅；契约 ✅；数据 ✅（成组恢复/清除防悬挂）。
- **问题清单**：无。
- **质量速记**：二开 ✅（permission_sync 内核化，视图薄委托）。

## 10. 数据字典（/system/dict/index）

- **标识**：system · core_rbac · 薄壳 35 行 · 元数据驱动 · 后端 `system/views/admin/dict.py DataDictViewSet`（17 路由）。
- **后端实现**：is_locked 内置字典删除保护（批删先查 locked 再过滤，`dict.py:86-93`，注释明确覆写基类必须保留 `@action`）；items 消费端在 PERMISSION_WHITE_URL（结构元数据口径已文档化）；batch-active / move / refresh-cache 动作齐备。
- **闭环校验**：权限 ✅；元数据 ✅；契约 ✅；数据 ✅。
- **问题清单**：无。

## 11. 我的数据范围（/system/my-scope/index）

- **标识**：system · core_rbac · `my-scope/index.vue`（333 行）+ 3 组件 + 状态机纯函数 · **非元数据驱动**（只读聚合页，数据源 `GET /api/identity/dept/managed`）· 后端即 DeptViewSet.managed。
- **前端实现**：四态显式状态机（denied/error/empty/ready，`resolveMyScopeState`）——无权限时说明所需权限点（`managed:SystemDept`）而非显示 0 统计（`index.vue:49-50,224-247`）；卡片视图 200 条渲染上限 + 加载更多（:100-111）；层级视图 el-tree-v2 虚拟树 + 全量展开键集合；成员预览/跳转用户页按 `list:SystemUser` 二次门控（:92,171-180）。
- **后端实现**：见 §2 部门（managed 动作：本人管辖部门 ∪ 下级，注解计数显式排除软删用户与列表口径一致，`dept.py:127-158`）。
- **闭环校验**：权限 ✅（页面权限点 + 服务端恒定本人范围）；元数据 ➖（刻意不走协议，决策有注释）；契约 ✅；数据 ✅（软删口径三处对齐）。
- **问题清单**：无。
- **质量速记**：四态/a11y/性能均为全站标杆页（aria-label、服务端计数单一事实源、渲染放量）。

## 12. 个人访问令牌（/system/credential/index —— PAT 页：/system/credential 属系统域 12 号页面；此处为「凭据与密钥」页）

- **标识**：system · core_open_credential · `credential/index.vue`（302 行，只读聚合页 + 三表分卡）· **非元数据驱动**（后端 GenericViewSet 仅 overview/rotate 两端点，无元数据面——页面头注释明确「勿按存量手写表格迁移」的口径）· 后端 `system/views/admin/credential.py CredentialViewSet`。
- **链路**：`credentialApi.overview/rotate` → `CredentialViewSet` → `system/services/credential`。
- **后端实现**：overview 不回传任何值（密文明文都不回，模块注释 :5-7）；rotate 仅服务端自生成凭据可原地轮换、scope 枚举显式收口（:76-79）、**密码二次确认**（`UserConfirmation.require(ConfirmType.PASSWORD)`，:44-49）；审计留痕 module=system:credential。
- **前端实现**：无权限态（ReEmpty + 说明）/失败重试态/明文凭据告警横幅（plaintext 计数，`index.vue:199-206`）/三表客户端分页（PAGE_SIZE=20，过滤重置页码 :77-81）。
- **闭环校验**：权限 ✅（overview:Credential + rotate:Credential）；元数据 ➖（刻意）；契约 ✅；数据 ✅。
- **问题清单**：无。

> 注：菜单种子中的「个人访问令牌」页（`/system/credential/index`，identity `PersonalAccessTokenViewSet`，白名单个人凭证端点）与「凭据与密钥」总览页同名不同物；PAT 端点为个人凭证自管（PERMISSION_WHITE_URL `^/api/identity/personal-access-tokens(/|$)`），实现面在 identity/views/user/token.py，归入本页一并核对：权限自管口径与 MFA/passkeys 一致（settings/custom.py:47-52 注释），✅。

## 批次 1 小结

- **发现数**：P0=0 / P1=0 / P2=0 / E=0（死权限点与共享端点为全局发现，记主册 §3.1）。
- **正向结论**：本批 12 页四条闭环全部通过；多数页面存在「前后端双保险」防呆（角色空树禁编辑、部门硬删 PROTECT+alert、凭据轮换密码确认）。代码组织（页面=壳+composable；后端=ViewSet+services）一致成熟。
- **已读文件**（前端 14 + 后端 10）：`system/user/index.vue`、`user/utils/hook.tsx`、`user/utils/useUserRowHandlers.ts`（grep）、`dept/index.vue`、`role/index.vue`、`role/utils/hook.tsx`、`permission/index.vue`、`field/index.vue`、`menu/index.vue`、`my-scope/index.vue`、`credential/index.vue`、`post/tag/dict` 薄壳（结构同型，wc 确认 <80 行）；`identity/views/admin/{role,dept,post,user(路由面),directory}.py`、`system/views/admin/{permission,menu,dict,modelfield,credential}.py`、`system/views/platform/tag.py`、`identity/models/department.py`（FK 段）、`identity/serializers/department.py`（validate 段）、`packages/xadmin-common/common/core/{permission,permission_meta,url_inventory(段),response/exception(段)}.py`、`server/settings/custom.py`（白名单）。
