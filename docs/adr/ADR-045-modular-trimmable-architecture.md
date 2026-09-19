# ADR-045：功能模块化与可裁剪架构（二开友好）

- 日期：2026-09-17
- 状态：**已交付（P1 软裁剪）**；P2–P4 待排期（见文末路线图）
- 相关：[ADR-002](ADR-002-demo-app.md)（demo 默认关闭，本决策是同一思路的体系化）、
  [ADR-035](ADR-035-api-contract-governance.md)（`auto_register_app_url` 只增不减的既有边界）

## 背景

项目功能持续累积，对"拿去做二次开发"的使用者过重，且**没有任何裁剪手段**：

| 维度 | 实测（2026-09-17） |
|------|--------------------|
| 后端 | 8 个 app、~56 模型；`system` 单 app 承载 47 模型 / 53 视图文件 / 28k 行 |
| 前端 | 156 个页面 / ~33k 行（`src/views`）；`src/api` 58 文件 |
| 权限面 | `loadjson/menu.json` 555 行 = 49 页面 + 493 权限点；`menumeta.json` 344 KB |
| 装配点 | 新增/移除一个功能要动 6+ 处：`INSTALLED_APPS` → `urls.py` → 菜单种子 → 权限扫描硬编码映射 → 前端页面/组件映射 → 前端 API 与词条 |
| 既有扩展点 | `XADMIN_APPS` + `auto_register_app_url` 只能导入期追加（不可卸载），且必须改配置重启 |

使用者（含贡献者）唯一的"裁剪"办法是删菜单数据——页面与接口仍在，认知负担与攻击面都没减少。

## 决策

### D1 三级分层 + 发行预设（不做插件市场）

- 模块分三级：`core`（不可裁剪）/ `standard`（默认开，可裁）/ `optional`（按需开）；
- 预设 `MODULE_PRESET`：`full`（默认，全功能）/ `standard`（内核+标配，**推荐二开起点**）/ `core`（仅内核）；
- **明确不做**插件市场与在线安装：Django 单体 + docker 部署下动态加载代码带来安全面与测试成本，
  收益（"能在线装"）远低于"能裁剪"这一真实诉求。P3 只做只读/开关式的「模块管理」页。

### D2 模块声明是唯一事实源

`common/core/modules.py` 的 `MODULES` 声明每个模块的：等级 / 依赖 / 菜单根 name / 补充权限路径 /
请求路由前缀 / 说明。所有裁剪动作从声明派生，禁止在别处再写一份模块清单。
守护测试校验声明与种子一致（菜单 name 拼错即裁剪失效，测试直接失败）。

### D3 软裁剪四层，默认全开零差异

| 层 | 行为 | 实现 |
|----|------|------|
| 路由 | 命中停用模块前缀的请求直接 404（`code=1001`），避免"页面没了、接口还在" | `server/middleware.py::ModuleGateMiddleware` |
| 菜单/权限 | 菜单子树（含权限点）从 `/api/system/routes` 与鉴权结果中隐藏；被清空的目录一并隐藏 | `common/core/modules.py::filter_menu_queryset`（注入 `get_user_menu_queryset` / `get_auths` / 超管路由） |
| 周期任务 | 停用模块的周期任务不注册；历史 beat 条目在启动时清理，重新启用自动恢复 | 装饰器 `module=` 参数 + `common/tasks.py::create_or_update_registered_periodic_tasks` |
| 缓存 | 存在停用模块时启动即清理菜单路由/权限缓存（TTL 24h，避免残留） | `invalidate_trimmed_caches()`（`common/apps.py::ready`） |

默认 `preset=full` 且无覆盖时，所有裁剪路径走零开销旁路，**行为与改造前完全一致**；
前端无需改动：菜单/权限码由后端下发，`CachingAsyncRoutes` 默认关闭（每次登录重新拉取）。

### D4 语义红线

1. 关闭模块只隐藏与拦截，**不删除任何业务数据**，重新开启即恢复；
2. `core` 等级模块不可关闭（启动期报错）；
3. 依赖未满足时 **fail-fast**（报错信息列出 `模块→依赖`），不做隐式连带启用；
4. 模块开关不替代运行期功能开关（`LDAP_AUTH_ENABLED` / `SCIM_ENABLED` / `AI_ASSISTANT_ENABLED` 等保持独立）；
5. 配置变更需重启进程（与 `XADMIN_APPS` 同语义），不做热加载。

### D5 第一批模块划分

- 标配：`ops`（运维监控）、`approval`（敏感操作审批）、`datamask`、`ldap`；
- 可选：`chat`、`ai`、`analysis`、`dform`、`approval_flow`、`webhook`、`open_platform`、`search`、`scim`；
- 内核：身份与访问、组织与权限、配置、文件与流转、通知、审计与在线、PAT（均不可关）。

`standard` 预设一次性移除 9 个可选模块：约 10.6k 行前端代码、130 个权限点、5 个周期任务。

## 兼容性与风险

- **默认 full 零行为差异**：存量部署不改配置即与改造前完全一致（全量 pytest 2449 通过为证）；
- 关闭模块后前端若仍访问旧路由（书签/历史），得到 404（预期语义：功能不存在）；
- 菜单过滤在首次访问时查询一次菜单表并缓存（进程内），菜单数据变更后需重启才反映到裁剪集合——
  与"配置变更需重启"一致，不引入新的不一致窗口；
- 已知边界：前端构建产物仍包含全部页面（运行期裁剪）；权限点种子仍全量导入（停用模块的权限点只是不生效）。
  （原「WebSocket 通道不拦截」边界已于 2026-09-18 收敛，见下方增量。）

## 验证方式（随交付）

- 单测 `tests/unit/common/test_modules.py`（33 例）：声明一致性（含与 `loadjson/menu.json` 的菜单名对齐）、
  预设与增删覆盖、未知模块/内核被关/非法预设/依赖未满足四类启动期报错、路由 404 与放行、
  菜单子树与被清空目录隐藏、权限前缀推导与误伤防护、用户菜单查询继承过滤、`/api/system/routes`
  实际接口下菜单与权限码消失（超管与非超管两侧）、周期任务注册/不注册/历史清理、缓存失效链路；
- 全量 backend：`pytest` 2452 passed / 1 skipped，`ruff check` + `ruff format --check` 全绿；
- E2E：smoke 门禁 9 passed（chromium，覆盖登录/侧边菜单渲染/部门与用户管理/角色授权树/登出）；
  全量 fresh 双浏览器一轮因本机负载出现 webkit 后段成批超时（**既有负载瞬态特征**：失败集中在
  webkit、单条耗时最长 17 分钟、chromium 仅 3 例既有 flaky），隔离复跑 `a11y`（3/3）与
  `data-mask --repeat-each=2`（4/4）webkit 全绿，确认非回归。

## 交付记录（2026-09-17，P1）

- 新增 `common/core/modules.py`（模块声明 + 解析 + 四层裁剪派生数据 + 缓存失效）；
- 新增 `server/middleware.py::ModuleGateMiddleware` 并挂载到 MIDDLEWARE（RequestMiddleware 之后）；
- `server/conf.py` / `server/settings/base.py` 增加 `MODULE_PRESET` / `MODULE_ENABLE` / `MODULE_DISABLE`；
- `common/core/permission.py`、`system/views/routes.py` 接入菜单过滤；
- `common/celery/decorator.py` 增加 `module=` 参数，`common/tasks.py` 按启用模块注册/清理；
  标注模块归属的周期任务：chat / analysis ×2 / ldap / approval ×3 / approval_flow ×2 / ops ×2；
- `common/apps.py::ready` 增加模块配置校验与裁剪缓存清理；
- `config_example.yml` 与 xadmin-docs 配置说明补充 `MODULE_*` 用法；
- 文档：`docs/architecture/模块化与功能裁剪.md`（模块清单与裁剪矩阵）；本 ADR；
- 全量回归：backend `pytest` 2452 passed / 1 skipped、`ruff check` + `ruff format --check` 全绿；
- 前端零改动（菜单/权限码由后端下发，`CachingAsyncRoutes` 默认关闭）。

## P2 交付记录（2026-09-17）：种子裁剪 + 清单 CLI

- **种子裁剪**（`common/core/modules.py::ModuleSeedFilter` + `load_init_json`）：
  配置了停用模块时，把过滤后的种子写入临时目录再交给 `loaddata`——停用模块的菜单、
  仅被其引用的 menumeta、指向这些菜单的字段权限行、角色/数据权限里的菜单绑定一并剔除，
  **新装库即为精简形态**；未配置停用模块时走原始种子文件（零行为差异）。
  过滤口径与运行期共用同一纯函数 `compute_hidden_menu_pks`（含"被清空的目录一并隐藏"）。
- **清单 CLI**（`manage.py modules`）：打印模块清单（等级/状态/页面数/路由数）与
  `config.yml` 片段，支持 `--preset/--enable/--disable` 预演（不改运行期状态）与 `--config` 只输出片段；
  非法组合与真实启动一致 fail-fast。
- 测试：`tests/unit/system/test_module_seed.py`（12 例：纯函数口径、真实种子文件过滤、
  全文件引用完整性、空目录剔除回归、命令接线）+ `tests/unit/system/test_modules_command.py`（8 例）。
- **真实建库验证**（sqlite 临时库，走 `migrate → 建超管 → load_init_json` 完整安装路径）：

  | 预设 | 菜单行 | 菜单 meta | 字段权限 | admin 角色菜单绑定 |
  |------|-------:|----------:|---------:|-------------------:|
  | `standard` | 391 | 413 | 159 | 197 |
  | `full`（默认） | 549 | 575 | 171 | 210 |

  差值即 9 个可选模块的裁剪量；`standard` 下停用模块残留菜单 **0**（含"子模块全停用后
  一并剔除"的三个空目录 数据分析/集成/表单采集）。行数与种子文件行数的差额来自
  `menu.json` 中 6 行软删除记录（既有数据，非本次改动）。
- 修复过程中由"真实建库验证"抓出两个纯单测覆盖不到的缺陷（已修复并加回归用例）：
  `compute_hidden_menu_pks` 传入生成器时只遍历一次导致空目录修剪失效；
  `model_names` 中 `menumeta` 早于 `menu` 导致 meta 过滤拿到空引用集。
- **测试纪律**：测试库不跑真实 `load_init_json`（命令会全局改写 `ModelSignal.send` 并写库，
  污染同进程后续用例——本轮实测导致 28 例失败），改为「替换父类 handle 捕获入参 + 数据层引用
  完整性校验」；`tests/conftest.py` 新增共用 `module_config` fixture。
- **P2b 前端构建期裁剪：评估不立项**（理由与触发条件见架构文档 §八）。

## P3/P4 交付记录（2026-09-17）：管理页 + 脚手架 + 硬裁剪

- **模块管理页**（系统管理 → 模块管理，`/system/module/index`）：只读展示当前预设、
  模块等级/依赖/覆盖范围/启停状态与可复制的裁剪配置片段。后端
  `GET /api/system/modules`（`system/views/modules.py`，权限点 `list:SystemModule` 入种子）；
  前端 `src/api/system/modules.ts` + `src/views/system/module/index.vue` + 中英词条；
  E2E 覆盖在 `e2e/system-pages.e2e.ts`（双浏览器 20 passed）。
- **app 侧模块声明扩展点**：`{app}/modules.py` 的 `MODULES` 随 app 安装自动纳入清单
  （与 `XADMIN_APPS` → `{app}/config.py` 对称）；`module_index()` 对重复 id fail-fast，
  声明导入失败只告警跳过（扩展点故障不拖垮内核）。
- **`manage.py generate_module`**：为已安装 app 生成 `{app}/modules.py`，并打印后续步骤；
  只写目标 app 自己的文件，不动 core。
- **`manage.py module remove <id> [--apply]`**（硬裁剪）：输出可核对计划——种子清理范围、
  后端引用清单（路径类原文匹配 + 标识类带引号匹配，跳过 `.venv*`，tests 只计数）、
  前端待删文件（由菜单 component 反查）与 i18n 词条；`--apply` 只做种子清理且**原文件先归档
  到工作区回收站 `_delete/module-<id>-<时间戳>/`**，可整体还原；代码删除由人工按清单执行。
- 测试：`tests/unit/system/test_generate_module.py`（10 例：生成物语法校验、参数组合、
  重复 id/未装 app/已存在文件三类拒绝、声明发现/重复 id/坏声明跳过）+
  `tests/unit/system/test_module_remove_command.py`（9 例：预演不动文件、apply 改写并归档、
  未变更文件不改写、剔除口径与运行期一致、前端与词条提示）。
- **P4b（`system` 物理拆子 app）：评估不立项**——收益边界与成本实测见架构文档 §八
  （45 模型 / 7 迁移含 122.6 KB 全量重建 / 125 个外部文件 import `system.models` /
  `common.DbAuditModel` 硬编码 `system.DeptInfo`），登记三条触发条件。

## 交付记录（2026-09-17）：正式库部署 + 种子冲突预检修复

- **部署**：`load_init_json` 导入 **2797 objects**；重启 `server` / `celery-worker` / `celery-heavy` /
  `celery-beat` 四容器（均 healthy）；`/api/system/modules` 返回 **401（非 404）**——路由已在运行进程注册；
  以独立进程（`MODULE_DISABLE=["chat"]`）对**真实库**复核实库裁剪口径：预设 full 下停用 chat →
  路由拦截 `^/api/chat/`、权限前缀 `api/chat/`、菜单可见数 550 → 542（= 1 菜单 + 7 权限点），
  内核菜单不受影响。前端无需部署（本环境无 web 容器，站点由客户端自行构建）。
- **顺带修复的既有缺陷（部署过程中实测复现）**：`loaddata` 把整次导入放在单事务里
  （`loaddata.py:102`），一行冲突会回滚**全部** fixture（含菜单与权限点）。现场是
  `seed_demo_org --reset` 按 code 删除了内置种子的 `demo_expense`（固定主键）并用新主键重建，
  此后 `load_init_json` 导入同 code 撞唯一约束 → **整次种子导入回滚**，正式库的种子管线已不可用。
  三项修复：
  1. **根因**：`seed_demo_org._clean()` 不再删除内置种子固定主键的流程行（存在时由
     `update_or_create` 复用，主键保持稳定）；
  2. **兜底**：新增 `system/utils/seed.py::filter_conflicting_rows`——导入前做自然键冲突预检
     （字段级 `unique=True` + 单字段 `UniqueConstraint`，含角色/菜单的"未删除唯一"条件约束），
     被库内其它主键占用时跳过该种子行，并级联剔除引用行、从 m2m 列表移除主键；
     **库内数据优先、种子让位**，跳过项逐条打印（本次正式库跳过 4 处：1 流程 + 2 节点 + 1 版本）；
  3. **收敛**：种子装配统一走 `build_seed_fixtures`（读文件 → 模块裁剪 → 冲突预检 → 落盘；
     无裁剪无冲突时直接用仓库原始文件，零改动、不产生临时文件）。
- 测试：新增 `tests/unit/system/test_seed_conflict.py`（9 例：无冲突零改动、同主键不算冲突、
  自然键冲突跳过、级联剔除、条件唯一约束、m2m 裁剪、真实种子端到端）+ `seed_demo_org` reset 边界用例；
  全量 `pytest` **2505 passed / 1 skipped**、`ruff check` + `format --check` 全绿。

## 交付记录（2026-09-17）：裁剪预设端到端验证 + CLI help 缺陷修复

- **standard 预设端到端验证**（此前只有解析层单测，未在真实裁剪配置下跑过）：
  - 新装路径：独立库 `E2E_DB_FILENAME=e2e_standard.sqlite3` + `MODULE_PRESET=standard`
    走 `e2e_seed.py`（删除旧库 → migrate → 种子按模块裁剪）→ smoke + system-pages（chromium）
    **16 passed**：聊天室/AI/数据分析/表单采集/审批流/事件订阅/开放平台/全局搜索/SCIM
    九个可选模块被裁掉后，登录、菜单渲染、部门/用户/角色权限、系统页与模块管理页全部正常。
  - 用例缺陷修正：模块管理页 E2E 原先写死 `full` 预设的期望值（模块条数、配置片段），
    standard 下必然假失败；改为与 `/api/system/modules` 接口返回值对齐，现可在任一预设下运行。
  - 负向对照（**同一 URL、同一库、仅换预设**，排除"路径本就不存在"的干扰）：

    | 端点 | 归属 | `full` | `standard` |
    |------|------|--------|-----------|
    | `/api/chat/room` | chat（可选） | 401 | **404** |
    | `/api/system/ai/assistant/status` | ai（可选） | 401 | **404** |
    | `/api/system/webhooks/subscriptions` | webhook（可选） | 401 | **404** |
    | `/api/system/leaves` | approval_flow（可选） | 401 | **404** |
    | `/api/system/monitor/overview` | ops（标配） | 401 | 401 |
    | `/api/system/approvals` | approval（标配） | 401 | 401 |
    | `/api/system/mask-rules` | datamask（标配） | 401 | 401 |
    | `/api/system/user`、`/api/system/modules` | 内核 | 401 | 401 |

    404 确系模块网关拦截，而非路由不存在；标配与内核不受影响。
- **顺带修复的 CLI 缺陷**：`modules` / `module` / `generate_module` 三个命令把 `gettext_lazy`
  惰性对象传给了 argparse 的 `help=`，Python 3.12+ 下 `manage.py xxx --help` 直接抛
  `TypeError: expected string or bytes-like object, got '__proxy__'`（命令本体正常、
  顶层 `manage.py --help` 也不受影响，所以此前未被发现）。改为普通字符串（与项目既有命令一致）
  并移除由此失效的导入；新增守护测试 `tests/unit/system/test_command_help.py`——遍历本地全部
  自定义命令各渲染一次 help，新增命令自动纳入覆盖。
- 门禁：后端 `pytest` **2530 passed / 1 skipped**、`ruff check` + `ruff format --check` 全绿；
  前端 `typecheck`（tsc + vue-tsc）与 `pnpm build` 通过。
- **P5 影响面预演固化为命令**：先以一次性只读脚本在部署库上验证了可行性，随后固化为
  `manage.py modules --impact`（`system/utils/module_impact.py` + CLI 渲染）——隐藏范围与
  运行期/种子共用 `compute_hidden_menu_pks`，同一 resolution 下**预演口径 ≡ 运行期过滤口径**
  （测试 `test_impact_matches_runtime_filter` 逐类比对）。命令只读：不改配置、不写库、不重启；
  DB 不可达 / 未 migrate 时给出可区分的报错文案。数值与一次性脚本**交叉一致**。
- **部署环境只读预演**（在运行中的 docker 部署内执行 `manage.py modules --preset <preset> --impact`；
  库内 551 条菜单行 / 8 角色 / 4 用户）：

  | 预设 | 停用模块 | 隐藏（目录/页面/权限点） | 裁剪后仍可见（页面/权限点） |
  |------|---------|------------------------|---------------------------|
  | `standard` | 9 | 4 / 17 / 137 | 33 / 351 |
  | `core` | 13 | 6 / 25 / 203 | 25 / 285 |

  - 角色影响：授权数据**无需改动**（运行期过滤，开回即恢复）。其中「示例-员工」40 项绑定里
    34 项属于可选模块（该演示角色本就围绕动态表单/审批流设计），`System Administrator` 550 项绑定的
    157 项会隐藏——**若要裁剪演示环境，需同步调整演示角色，否则其可见前台近乎为空**；
  - 数据保留：命中可选模块的 14 张表（动态表单/审批流/数据集/聊天室等）数据全部保留，裁剪不删任何行；
  - 该环境当前为 `preset=full`（与升级前零差异），切换只需改 `config.yml` 的 `MODULE_PRESET` 并重启。

## 后续路线图

| 批次 | 内容 | 状态 |
|------|------|------|
| P3 | 二开路径文档 + `generate_module` 脚手架 + app 侧声明扩展点 + 「模块管理」页面 | ✅ 2026-09-17 |
| P2d | WS 通道级裁剪（见下节增量） | ✅ 2026-09-18 |
| P4a | 硬裁剪脚本 `manage.py module remove`（计划 / 种子清理 / 归档回滚） | ✅ 2026-09-17 |
| P4b | `system` 物理拆子 app | ✖ 评估不立项（三条触发条件见架构文档 §八） |
| P2b | 前端构建期裁剪：✖ 评估不立项（触发条件：发布产物体积硬约束 / 组件名错配真实故障） | ✖ 已关闭 |
| P2c | `loadjson` 目录级拆分（`loadjson/menu/{module}.json`） | ✖ 评估不立项（2026-09-17）：目录拆分只改变种子文件的组织方式，裁剪与删除诉求已由「种子过滤器（`ModuleSeedFilter`，新装库即为精简形态）」+「硬裁剪脚本 `module remove`」覆盖；拆分会改变既有二开对 `loadjson/*.json` 的路径引用，迁移成本大于收益。重新评估触发条件：种子文件需要按模块**独立升级分发** |
| P5 | 影响面预演 `manage.py modules --impact`（只读：隐藏范围 / 逐模块明细 / 角色影响 / 数据体量，与运行期同口径） | ✅ 2026-09-17 |

## 增量（2026-09-18）：WS 通道级裁剪（第六层）

原「已知边界：WebSocket 通道不拦截」收敛：`ModuleSpec` 新增 `ws_routes` 声明，
`common/core/modules/gate.py::ModuleTrimWebsocketMiddleware` 在 ASGI 链路
（`AllowedHostsOriginValidator` 之内、认证中间件之外）对停用模块的通道于认证与
consumer 之前拒绝（close `4404`，语义=通道不存在）。

设计取舍：

- **归属单点声明**：通道归属写在模块注册表（`ws_routes`），准入层从声明派生——
  与路由/菜单/周期任务同源，不在别处再写一份清单；
- **零变化优先**：未声明 `ws_routes` 的通道（内核 `ws/message`、`ws/tasks/log`）不拦截；
  未配置停用模块（`preset=full` 且无覆盖）时零开销直通（不编译/不匹配正则）；
- **提前实施理由**：原触发条件为「真实二开裁剪诉求」，但成本实测仅 ~120 行
  （声明字段 + 准入中间件 + 守护/演练），且能消除「页面与 REST 已隐藏、WS 仍可连」
  的不一致语义，故不等触发（评估见长期优化方案 §5.2 A5）。

当前覆盖：`ws/chat`（chat）、`ws/screen`（analysis）、`ws/system/monitor`（ops）。

验证：`tests/unit/common/test_modules.py::TestWebsocketGate`（WS 正则编译、准入拒绝/
直通、HTTP scope 旁路、全量零开销）；`tests/integration/common/test_module_trim_drill.py`
新增第六层运行期断言与**通道归属双向对齐**守护（routing 实际注册通道 ↔
`WS_CHANNEL_OWNERSHIP` 登记表，新增通道漏声明即失败），裁剪矩阵演练 9 → 16 例。

## 增量（2026-09-19）：后台覆盖层（管理页可编辑，待重启生效）

模块管理页从只读升级为可编辑：选预设 + 增删模块，写入一行后台覆盖（`system.ModuleOverride`），
重启进程后生效。语义见架构文档 §五之四。

设计取舍：

- **不写 config.yml**：生产镜像的 config.yml 是空的且未挂载（配置走 `env_file`），
  写文件会在容器重建/升级后丢失；DB 覆盖跨 dev 与生产一致、随库持久化。
- **不做热更新**：标准部署是 web / celery / beat 多容器，页面请求跑在 web 容器内且无
  docker socket，无法重启兄弟容器；装配语义要求全进程一次性生效，热更新会留下
  「接口已 404、beat 还在跑该模块任务」的半残状态。故页面只展示「待重启生效」的差异与
  重启命令，不做页内重启。
- **覆盖整体替换基线**：存在覆盖行时 `preset/enable/disable` 整体替换 config.yml / 环境变量，
  不做叠加（避免两处配置互相影响的隐式语义）；无行时行为与改造前完全一致。
- **写路径不失效进程缓存**：「当前生效」= 启动时 `lru_cache` 的 `resolve_modules()`；
  「待生效」= 每次现算的 `desired_modules()`（**刻意不缓存**）。保存不得调用
  `reset_module_state()`，否则等同于热更新。
- **解析推迟到首次使用**：`AppConfig.ready()` 只校验部署基线（`validate_deployment_config()`，
  只读 settings），覆盖行的读取推迟到首次实际解析（中间件装配期，早于任何请求）。
  起因是实测：在 `ready()` 中读库会建立指向「尚未创建的测试库」的连接，破坏 pytest 的
  测试库创建（`transaction=True` 用例大面积 `no such table`），Django 的「app 初始化期
  访问数据库」告警正是指此。`load_override()` 本身仍 fail-safe（表未建 / 库不可达时回退
  部署基线并告警），不阻断启动。
- **缓存清理随之推迟**：`invalidate_trimmed_caches()` 不再在 `ready()` 调用，改由进程内
  首次 `resolve_modules()` 触发一次（中间件装配期，仍早于请求）；无停用模块时零开销。
- **校验同启动口径**：写入前调用同一个 `preview_modules()`，未知模块 / 内核被关 /
  依赖不满足 → 400，文案与启动失败一致。
- **逃生舱**：覆盖行引用已移除模块会让首次解析 fail-fast（服务不可用）且管理页不可用，
  故 `manage.py modules` 增加 `--clear-override`（直接删行、不经过解析），并把 `modules`
  加入 `ready()` 的 excludes，保证恢复命令能在解析失败前执行。
- **空 ≠ 缺席**：覆盖行存在即生效（即使与基线等价）；要回到基线须显式清除覆盖。

验证：`tests/unit/common/test_modules_override.py`（优先级 / fail-safe / 保存不改生效态 /
diff）；`tests/unit/system/test_modules_api.py::TestSystemModuleWriteApi`（apply 落库与
pending、非法组合 400、reset、普通用户 403）+ 种子权限点守护；
`tests/unit/system/test_modules_command.py::TestClearOverride`（恢复通道）；
E2E `system-pages.e2e.ts`（保存 → 待重启差异 → 恢复）。
