# 2026-10 全栈功能审查 · 主册

> 只读审查产物：只记录问题与证据，不改业务代码。结论以源码为准，文档/注释仅作线索。
> 问题编号格式 `<域>-<级别>-<序号>`（如 `sys-P1-01`），只在审查文档内使用，不回写源码。
> 逐页详情在各分册（本文件只汇总与索引）；本主册对应 `docs/README.md` 索引行「2026-10 全栈功能审查」。

## 0. 审查说明

- **范围**：`xadmin-server/`（14 个业务 app + `packages/xadmin-common` 内核）+ `xadmin-client/` 前端页面；installer / web / docs 不在本轮。
- **方法**：按页面走四条闭环（权限 / 元数据 / 契约 / 数据）；后端四视角（新接手 / 二开者 / 攻击者 / 运维）；横向维度（安全 / 性能 / 可靠 / 兼容）只记发现不逐页展开。
- **证据等级**：A=代码直接确证；B=跨文件静态推断；C=待运行时验证（不写成定论）。
- **统计口径**：页面清单以 `loadjson/menu.json` + 各 `<app>/seeds/menu.json` 的 `menu_type=1`（页面）为准（62 页），外加静态路由页（login / welcome / about / account / invite / oauth / error / playground 等）；路由内省（`django.urls.get_resolver`）共 947 条 URL 作为后端接口全集。
- **未覆盖**：真实运行时行为（登录态 / WS 推送 / Celery 任务实跑 / 文件上传实流）属静态审查不可达，标注「待运行时验证」；installer / web / docs 交付链路审查见索引行「2026-10 交付链路审查」（另行安排）。
- **审查基线**：
  - xadmin-server `539b9194`（fix(core): 修复审查发现的权限收敛与 AI 链路缺陷）+ 工作区暂存改动 42 文件（权限收敛、强制登出、设置字段权限等修复，未提交）；
  - xadmin-client `d015b6db` + 工作区暂存改动 14 文件（dashboard/ai-mcp/ledger/logs 等页面改动，未提交）。
  - 两仓库均存在大量未提交改动：本审查以**工作区现状**（含暂存）为事实源。
- **前置线索**：根目录 `code-review-2026-10-25.md`（当日提交快审，205 行）记录的问题（H-1 仪表盘联动与实时推送互斥、H-2 数据集 filters 字段权限/JSON 路径 500）在 H 批次复核后归入对应页面；其结论未直接采信，均已回源码复核。

## 1. 总览与进度

### 1.1 进度表（应审 62 菜单页 + 9 静态页 = 71）

| 批次 | 分册 | 页面数 | 状态 |
|---|---|---|---|
| 0 | 三张清单（本册 §2） | — | ✅ 已落盘 |
| 1 | pages-system-a（用户/组织/角色/权限/菜单/字典） | 12 | ✅ 已审（分册已落盘） |
| 2 | pages-system-b（配置/通知/文件/监控/模块/代码生成） | 10 | ✅ 已审（分册已落盘） |
| 3 | pages-system-c（审计/日志/脱敏/在线/账号风险） | 5 | ✅ 已审（分册已落盘） |
| 3' | pages-task（定时任务/Webhook/导出中心） | 6 | ✅ 已审（分册已落盘） |
| 4 | pages-settings（系统设置 8 页） | 8 | ✅ 已审（分册已落盘） |
| 5 | pages-approval（审批域 6 页） | 6 | ✅ 已审（分册已落盘） |
| 6 | pages-analysis-integration（分析+AI+开放平台 9 页；webhook 2 页在 task 分册） | 9 | ✅ 已审（分册已落盘） |
| 7 | pages-form-chat（表单采集+聊天室 4 页） | 4 | ✅ 已审（分册已落盘） |
| 8 | pages-misc（静态/匿名/个人页 9 页） | 9 | ✅ 已审（分册已落盘） |
| — | pages-actions（行动建议 + 未验证事项） | — | ✅ 已落盘 |

### 1.2 问题统计（最终）

**覆盖率**：应审 71 页（62 菜单页 + 9 静态页）／已审 71／覆盖率 100%。全局机械对账：757 条 API 路由 × 全部权限点。

| 级别 | 数量 | 编号 |
|---|---|---|
| P0 | 0 | — |
| P1 | 1 | 全局-P1-01 共享端点权限码纪律（§3.1；写入侧缓解已落地） |
| P2 | 1 | audit-P2-01 死权限点 `retrieve:SystemUserLoginLog`（system-c §2） |
| E | 1 | analysis-E-01 仪表盘联动重拉反馈（analysis-integration §1，移交 Phase 2 复核） |

按模块分布：audit 1（P2）／全局 1（P1）／analysis 1（E）；其余 12 个审查批次 0 发现。
按问题类型：死配置 1、流程纪律 1、交互增强 1。

**Top 5 风险与亮点**（结论 + 证据位置）：
1. **P1** 共享端点新增不登记优先级会让主权限点随库序漂移——机制已确定性消解现存 4 组（`permission.py:87-96`），遗留为纪律问题。
2. **亮点** 权限闭环零缺口：757 条路由 × 权限点机械对账，48 条无权限点路由全部为设计内豁免且逐一核实（§3.1）。
3. **亮点** 2026-10-25 快审 3 个高危项全部修复并有守护注释（analysis-integration §0）。
4. **亮点** Webhook SSRF 三层防链 + 出站请求公共件（`common/utils/outbound.py` pinned_request）。
5. **P2** 登录日志死权限点（唯一死配置发现，清理成本 S）。

## 2. 三张清单

### 2.1 页面清单（菜单驱动 62 页 + 静态 9 页）

可裁剪性按 `packages/xadmin-common/common/core/modules/catalog.py` 的 ModuleSpec 归属：core_*=不可裁剪；standard/optional 见「模块」列（ops/approval/datamask/ldap=标配，chat/ai/analysis/dform/approval_flow/webhook/open_platform/search/scim=可选）。元数据驱动 = 前端用 RePlusPage 消费 `search-columns`/`search-fields`。

| # | 页面 | 路由 | 前端入口 | 后端 app | 模块/可裁剪 | 元数据 | 状态 |
|---|---|---|---|---|---|---|---|
| 1 | 用户管理 | /system/user/index | system/user | identity | core_rbac | ✅ | 未审 |
| 2 | 部门管理 | /system/dept/index | system/dept | identity | core_rbac | ✅ | 未审 |
| 3 | 岗位管理 | /system/post/index | system/post | identity | core_rbac | ✅ | 未审 |
| 4 | 标签管理 | /system/tag/index | system/tag | system | core_rbac | ✅ | 未审 |
| 5 | 目录配置 | /system/directory/index | system/directory | identity | core_rbac | ✅ | 未审 |
| 6 | 角色管理 | /system/role/index | system/role | identity | core_rbac | ✅ | 未审 |
| 7 | 数据权限 | /system/permission/index | system/permission | system | core_rbac | ✅ | 未审 |
| 8 | 模型标签字段 | /system/field/index | system/field | system | core_rbac | ✅ | 未审 |
| 9 | 菜单管理 | /system/menu/index | system/menu | system | core_rbac | ✅ | 未审 |
| 10 | 数据字典 | /system/dict/index | system/dict | system | core_rbac | ✅ | 未审 |
| 11 | 我的数据范围 | /system/my-scope/index | system/my-scope | system | core_rbac | ✅ | 未审 |
| 12 | 个人访问令牌 | /system/credential/index | system/credential | system | core_open_credential | ✅ | 未审 |
| 13 | 个人配置 | /system/config/user/index | system/config/user | system | core_config | ✅ | 未审 |
| 14 | 系统配置 | /system/config/system/index | system/config/system | system | core_config | ✅ | 未审 |
| 15 | 公告管理 | /system/notice/index | system/notice | notifications | core_notify | ✅ | 未审 |
| 16 | 公告已读 | /system/notice/read/list | system/notice/read/list | notifications | core_notify | ✅ | 未审 |
| 17 | 我的消息 | /user/notice/index | user/notice | notifications | core_notify | ✅ | 未审 |
| 18 | 文件管理 | /system/file/index | system/file | file | core_file | ✅ | 未审 |
| 19 | 导出中心 | /system/export/index | system/export | task | core_file | ✅ | 未审 |
| 20 | 主机监控 | /system/monitor/index | system/monitor | system | ops(标配) | 部分 | 未审 |
| 21 | 模块管理 | /system/module/index | system/module | system | core | 部分 | 未审 |
| 22 | 代码生成 | /system/codegen/index | system/codegen | devtools | 可裁剪(条件注册) | ✅ | 未审 |
| 23 | 操作日志 | /system/logs/operation/index | system/logs/operation | audit | core_log | ✅ | 未审 |
| 24 | 登录日志 | /system/logs/login/index | system/logs/login | audit | core_log | ✅ | 未审 |
| 25 | 脱敏规则 | /system/mask/index | system/mask | audit | datamask(标配) | ✅ | 未审 |
| 26 | 在线用户 | /system/online/index | system/online | identity | core_log | ✅ | 未审 |
| 27 | 账号风险 | /system/account-risk/index | system/account-risk | identity | core_log | ✅ | 未审 |
| 28 | 任务执行记录 | /system/celery/logs/index | system/celery/logs | task | ops(标配) | ✅ | 未审 |
| 29 | 任务列表 | /system/celery/task/index | system/celery/task | task | ops(标配) | ✅ | 未审 |
| 30 | Crontab 任务 | /system/celery/crontab/index | system/celery/crontab | task | ops(标配) | ✅ | 未审 |
| 31 | 周期任务 | /system/celery/interval/index | system/celery/interval | task | ops(标配) | ✅ | 未审 |
| 32 | Webhook 订阅 | /integration/subscription/index | integration/subscription | task | webhook(可选) | ✅ | 未审 |
| 33 | Webhook 投递 | /integration/delivery/index | integration/delivery | task | webhook(可选) | ✅ | 未审 |
| 34 | 基础设置 | /settings/basic | settings/basic | settings | core_config | ❌(表单) | 未审 |
| 35 | 安全设置 | /settings/security/index | settings/security | settings | core_config | ❌(表单) | 未审 |
| 36 | 短信设置 | /settings/sms | settings/sms | settings | core_config | ❌(表单) | 未审 |
| 37 | 消息设置 | /settings/message | settings/message | settings | core_config | ❌(表单) | 未审 |
| 38 | 配置台账 | /settings/ledger/index | settings/ledger | settings | core_config | ✅ | 未审 |
| 39 | LDAP 目录同步 | /settings/ldap | settings/ldap | settings | ldap(标配) | ❌(表单) | 未审 |
| 40 | 开放平台设置 | /settings/open-platform | settings/open-platform | settings | open_platform(可选) | ❌(表单) | 未审 |
| 41 | 登录策略 | /settings/login-policy/index | settings/login-policy | identity | core_auth | ✅ | 未审 |
| 42 | 审批单 | /approval/index | approval/index | approval | approval(标配) | ✅ | 未审 |
| 43 | 审批流定义 | /approval/flow/index | approval/flow | approval | approval_flow(可选) | ✅ | 未审 |
| 44 | 审批实例 | /approval/instance/index | approval/instance | approval | approval_flow(可选) | ✅ | 未审 |
| 45 | 请假管理 | /approval/leave/index | approval/leave | approval | approval_flow(可选) | ✅ | 未审 |
| 46 | 审批委托 | /approval/delegation/index | approval/delegation | approval | approval_flow(可选) | ✅ | 未审 |
| 47 | 审批规则 | /approval/rule/index | approval/rule | approval | approval(标配) | ✅ | 未审 |
| 48 | 仪表盘 | /analysis/dashboard/index | dashboard | dataset | analysis(可选) | 部分 | 未审 |
| 49 | 数据集 | /analysis/dataset/index | dashboard/dataset | dataset | analysis(可选) | ✅ | 未审 |
| 50 | 报表 | /analysis/report/index | analysis/report | dataset | analysis(可选) | ✅ | 未审 |
| 51 | 大屏 | /analysis/screen/index | analysis/screen | dataset | analysis(可选) | 部分 | 未审 |
| 52 | AI 助手 | /integration/ai/index | integration/ai | ai | ai(可选) | 部分 | 未审 |
| 53 | AI 配置 | /integration/ai/config | integration/ai/config | ai | ai(可选) | ❌(表单) | 未审 |
| 54 | 知识库 | /integration/knowledge/index | integration/knowledge | ai | ai(可选) | ✅ | 未审 |
| 55 | MCP 服务 | /integration/ai/mcp | integration/ai/mcp | ai | ai(可选) | ✅ | 未审 |
| 56 | API 应用 | /integration/api-app/index | integration/api-app | identity | open_platform(可选) | ✅ | 未审 |
| 57 | 表单设计器 | /form-collection/designer/index | form/designer | dataset | dform(可选) | ❌(设计器) | 未审 |
| 58 | 我的填报 | /form-collection/my/index | form/my | dataset | dform(可选) | ✅ | 未审 |
| 59 | 表单数据 | /form-collection/data/index | form/data | dataset | dform(可选) | ✅ | 未审 |
| 60 | 聊天室 | /chat/index | chat | message | chat(可选) | ❌(IM) | 未审 |
| 61 | 关于页 | /about/index | about | system(平台种子) | core | ❌ | 未审 |
| 62 | 个人中心 | /user/info/index | account | identity | core_auth | ❌ | 未审 |
| S1 | 登录 | /login | login | identity | core_auth | ❌ | 未审 |
| S2 | 欢迎页 | /welcome | welcome | — | core | ❌ | 未审 |
| S3 | 邀请接受 | /invite/accept | invite | identity | core_auth | ❌ | 未审 |
| S4 | OAuth 回调 | /oauth/callback | oauth | identity | core_auth | ❌ | 未审 |
| S5 | OAuth 授权 | /oauth/authorize | oauth | identity | open_platform(可选) | ❌ | 未审 |
| S6 | 账号设置 | /account-settings | account | identity | core_auth | ❌ | 未审 |
| S7 | 旧设置页(隐藏) | /system/setting/index | settings/basic 复用 | settings | core_config | ❌ | 未审 |
| S8 | 错误页 | /error/* | error | — | core | ❌ | 未审 |
| S9 | Playground | /playground/* | playground | — | 开发用 | ❌ | 未审 |

### 2.2 后端 app 清单

模型数=类定义计数（models.py / models/）；ViewSet 数=views 下 `class *ViewSet`；@action 数为 grep 计数。`integrations`（第三方适配）与 `demo`/`demo_seed`（条件注册）无独立菜单页；`mfa`/`captcha` 无 ViewSet（APIView/服务层形态）。

| app | 职责 | 模型 | ViewSet | @action | 迁移数 | 代码行 | 种子 |
|---|---|---|---|---|---|---|---|
| system | 平台域（菜单/配置/字典/权限/监控/代码生成入口） | 16 | 16 | 42 | 2 | 20,293 | ✅ |
| identity | 用户与组织（用户/部门/角色/岗位/在线/风险/API 应用） | 15 | 18 | 40 | 2 | 16,610 | ✅ |
| dataset | 数据分析（数据集/仪表盘/报表/大屏/动态表单） | 9 | 10 | 11 | 2 | 9,430 | ✅ |
| approval | 审批（敏感操作审批 + 审批流引擎） | 13 | 6 | 36 | 3 | 10,360 | ✅ |
| ai | AI（档案/聊天记录/知识库/用量/MCP） | 6 | 6 | 28 | 5 | 10,873 | ✅ |
| task | 任务（Celery 任务/Crontab/Webhook/导出） | 8 | 10 | 21 | 4 | 6,109 | ✅ |
| settings | 系统设置模型（多组设置端点） | 1 | 24 | 5 | 1 | 4,151 | ✅ |
| notifications | 通知（公告/用户通知/渠道） | 5 | 7 | 12 | 1 | 3,829 | ✅ |
| file | 文件（附件/预览/导入导出） | 4 | 1 | 6 | 3 | 3,507 | ✅ |
| audit | 审计（操作日志/登录日志/脱敏规则） | 3 | 4 | 2 | 4 | 3,110 | ✅ |
| message | 聊天室（消息/房间/AI 面板） | 3 | 4（views.py + views_ai.py） | — | 1 | 4,520 | ✅ |
| mfa | 多因子（confirm/otp 两视图集） | 1 | 2（views.py） | — | 1 | 1,485 | ✅ |
| captcha | 验证码（服务层渲染，无视图类） | 1 | 0（services 形态） | — | 1 | 674 | ✅ |
| integrations | 第三方适配 | 0 | 0 | — | 0 | 1,426 | ❌ |
| packages/xadmin-common | 内核（ORM/DRF 基座/元数据/缓存/权限引擎） | — | — | — | — | 24,226 (191 文件) | — |

> 统计口径备注：首轮 ViewSet 计数仅扫描 `views/` 目录，漏计平铺 `views.py` 的 app（message/mfa），已按 `grep "^class.*ViewSet" views.py` 修正如上；模型数为类定义计数， Integrations/demo 条件注册。

### 2.3 链路映射表（页面 → 前端组件 → api → ViewSet → 权限点）

路由内省 947 条 URL 的模块分布：identity 182 / system 134 / approval 66 / dataset 69 / task 69 / settings 55 / ai 47 / notifications 35 / audit 28 / file 18 / message 14 / common 13 / mfa 11 / demo 16。
逐页完整链路（组件 → api 封装 → ViewSet → Serializer/Model → 权限点）在各分册逐页表格内记录；本册只保存汇总口径。权限点总量与缺口核对方式：`python manage.py sync_menu_permissions --dry-run`（期望缺口 0，静态审查引用其历史结论，运行时复核标注 C）。

## 3. 横向结论（随批次滚动）

### 3.1 权限引擎（全局机械对账，已完成）

**方法**：Django 路由内省 947 条（其中 API 路由 757）× 全部种子权限点（729 条菜单记录中 menu_type=2），
镜像运行时口径（`permission_path_matches` 的 `$` 精确 fullmatch / 无 `$` 段边界前缀 + `permission_meta.SHARED_LIST`
剥后缀 + `PERMISSION_WHITE_URL` 白名单）逐条匹配。

**结论**：

1. **权限点覆盖闭环：通过**。757 条 API 路由中，48 条无权限点且不在白名单，逐一核实均为设计内豁免：
   匿名认证端点 13 条（login/register/refresh/token/reset/verify/captcha/capabilities/invite×2/rules，
   `identity/views/auth/*.py` 均显式 `permission_classes = []` 或 `AllowAny`，A 级）；
   基础设施 5 条（health/csp-report/ops-alert/backup-alert/countries，AllowAny）；
   metrics 1 条（令牌三重门禁：METRICS_ENABLED+METRICS_TOKEN+Bearer，`packages/xadmin-common/common/api/metrics.py:29-40`）；
   SCIM 7 条（独立认证体系，`identity/scim/`，待运行时验证其 token 门禁）；
   demo 16 条（条件注册的开发示例模块，非生产菜单面）；
   静态/验证码媒体 5 条；`api/system/configs/{key}` 1 条（`system/views/user/configs.py:54-64`：
   覆写 permission_classes 但匿名读取仅限 `WEB_SITE_CONFIG` 键、写入走 `@auth_required`+自服务键白名单+站点级键仅超管，防护完整）。
   **批次 2 补正**：`/api/system/config/{system,user}/registered-keys` 2 条经 `shared_list_action` 装饰器
   注册（`system/views/admin/config.py:58,87`），运行时按父级 list 权限解析——离线扫描读不到装饰器副作用
   （`permission_meta.py:43-45` 注释明确此盲区），非缺口。
2. **同 (path,method) 多权限码**：4 组（操作日志 GET×3 码、settings basic GET/PATCH×2 码、任务执行 GET×2 码），
   运行时已由 `SHARED_PERMISSION_PRIORITY`（`packages/xadmin-common/common/core/permission.py:87-96`）
   确定性选主 + `get_user_field_queryset_for_menus` 字段权限并集（:75-81）——历史「随库序漂移」已修，
   **遗留风险（P1）**：新增共享端点必须显式登记优先级或改解析，属流程纪律而非代码缺陷（与 AGENTS.md §八 口径一致）。
3. **无路由权限点（功能开关）**：`ongoing:SystemApprovalInstance`、`all:SystemApprovalDelegation` 经
   `user_has_permission` 按设计消费（`approval/views/approval_flow.py:212`、`approval/views/approval_delegation.py:65`），
   非死配置；`invite` 权限点同理（`identity/views/admin/user.py:192`）。
4. **死配置（P2）**：`retrieve:SystemUserLoginLog`（`audit/seeds/menu.json`，path `api/audit/logs/login/(?P<pk>[^/.]+)$`）
   ——路由内省中登录日志 ViewSet 无 detail retrieve 路由（仅 list 与 `{pk}/logout`），该权限点无运行时消费面。
5. **api-docs 3 个权限点**（SpectacularSwagger/Spectacular/SpectacularRedoc）与 `api-docs/*` 路由对账通过（未计入 API 集合）。

## 4. 批次日志

### 批次 0（启动检查 + 三张清单）

- 完成：§0 审查说明、§1 进度表、§2 三张清单（页面 71 / app 15 / 路由内省 947）。
- 发现数：P1×1（§3.1 历史缺口转记）。
- 已读文件：工作区 `AGENTS.md`、`xadmin-server/AGENTS.md`、`xadmin-client/AGENTS.md`、`docs/README.md`、`docs/ci-gates.md`、`CODE-REVIEW-PROMPT.md`、`code-review-2026-10-25.md`（线索）；命令产物：两仓 git status/HEAD、菜单种子解析（729 条菜单记录，其中页面 62）、各 app 规模统计、`loadjson/menu.json`+11 个 app 种子的 menu_type=0/1 枚举、`catalog.py` ModuleSpec 清单。
- 遗留：分册文件均未创建；页面级审查未开始。
- 下次起点：批次 1（system-a 分册：用户/部门/岗位/标签/目录/角色/数据权限/模型字段/菜单/字典/我的数据范围/PAT）。

### 批次 1（system-a：12 页核心 RBAC 页）

- 完成：`code-review-2026-10-pages-system-a.md` 落盘，12 页四条闭环全部通过，P0/P1/P2/E 均 0。
- 发现数：0（全局发现见主册 §3.1）。
- 正向：前后端双保险防呆（角色空树禁编辑、部门 PROTECT+alert、凭据轮换密码二次确认）、my-scope 四态状态机为全站标杆。
- 已读文件：见分册文末清单（前端 14 + 后端 10 + 内核 4）。
- 遗留：无。
- 下次起点：批次 2（system-b 分册：个人配置/系统配置/公告管理/公告已读/我的消息/文件管理/导出中心/主机监控/模块管理/代码生成）。

### 批次 2（system-b：10 页）

- 完成：`code-review-2026-10-pages-system-b.md` 落盘，10 页闭环通过，P0/P1/P2/E 均 0。
- 发现数：0；主册 §3.1 补正 registered-keys 2 条（装饰器注册表覆盖，非缺口）。
- 正向：文件域安全设计（事务原子上传/预览不直出/访问审计/限流）、用户消息防水平越权写、导出本人取值域显式取舍。
- 下次起点：批次 3（system-c 分册：操作日志/登录日志/脱敏规则/在线用户/账号风险 + task 分册：任务执行/任务列表/Crontab/周期任务/Webhook 订阅/Webhook 投递）。

### 批次 3（system-c 5 页 + task 6 页）

- 完成：`code-review-2026-10-pages-system-c.md`、`code-review-2026-10-pages-task.md` 落盘。P0/P1/E=0，P2=1（audit-P2-01 死权限点）。
- 重点确认：Webhook SSRF 三层防链（https 强制/私网字面量拒绝/发送侧 pinned_request 防 DNS rebinding）；审计不可灭迹口径三处一致（操作日志/登录日志/webhook 投递）；在线用户批量删除强制逐行保副作用；强退提权防护双入口；脱敏正则 ReDoS 子进程隔离。
- 下次起点：批次 4（settings 分册：基础/安全/短信/消息/台账/LDAP/开放平台/登录策略 8 页）。

### 批次 4（settings 8 页）

- 完成：`code-review-2026-10-pages-settings.md` 落盘。P0/P1/P2/E 均 0。
- 重点确认：BaseSettingViewSet 保存契约与 `get_permission_field_groups` 写入侧字段面隔离（主册 §3.1 P1 的缓解落地证据）。
- 下次起点：批次 5（approval 分册：审批单/流程定义/实例/请假/委托/规则 6 页）。

### 批次 5（approval 6 页）

- 完成：`code-review-2026-10-pages-approval.md` 落盘。P0/P1/P2/E 均 0。
- 重点确认：审批引擎 CAS 并发防护、三层授权收口、自审双防护、scope 页签与角标同源。
- 未验证事项新增：审批实例多级链完整状态流转矩阵（加签/转办/回退组合）未逐条枚举（§7）。
- 下次起点：批次 6（analysis-integration 分册：仪表盘/数据集/报表/大屏/AI 助手/AI 配置/知识库/MCP/API 应用 11 页）。

### 批次 6（analysis-integration 9 页，webhook 2 页已在批次 3）

- 完成：`code-review-2026-10-pages-analysis-integration.md` 落盘。P0/P1/P2=0，E=1（analysis-E-01）。
- **重大结论**：2026-10-25 快审 3 个高危项（H-1 联动/推送互斥、H-2a filters 字段权限、H-2b JSON 路径 500）在当前源码全部修复并有守护注释与测试（暂存/已提交）；NL 查数走「LLM→受限 DSL→数据集引擎」架构，无注入面。
- 未验证事项新增：WS screen/dashboard 通道鉴权、MCP server URL 写入侧校验公共件复用确认（§7）。
- 下次起点：批次 7（form-chat 分册：表单设计器/我的填报/表单数据/聊天室 4 页）。

### 批次 7（form-chat 4 页）

- 完成：`code-review-2026-10-pages-form-chat.md` 落盘。P0/P1/P2/E 均 0。
- 正向：同模型双视图取值域差异显式化（我的填报 creator 隔离 vs 表单数据走数据权限编译器）；聊天附件复用文件中心安全内核。
- 下次起点：批次 8（misc 分册 9 页）。

### 批次 8（misc 9 页）+ 收尾

- 完成：`code-review-2026-10-pages-misc.md` 落盘；专项汇总（§5）、行动建议（§6 + pages-actions 分册）、未验证事项（§7）齐备。
- 认证链确认：Login/Register/ResetPassword/PasswordVerify 四类限流器全覆盖；令牌族统一 AES 密文库。
- **Phase 1 全部批次完成，覆盖率 100%（71/71）。**

## 5. 专项汇总（按 §5.7 口径，详细证据在各分册）

### 5.1 功能完整性
71 页四条闭环（权限/元数据/契约/数据）全部通过；无功能缺失发现。元数据协议覆盖 44/71 页，其余为表单页/设计器/IM/聚合面板等刻意不走协议的形态（各分册已逐一标注理由，无「应走未走」的存量页）。

### 5.2 前端复用（功能层）
页面普遍遵循「壳 + composable 装配」范式（user 页 9 个 composable、role 页 4 个）；`defineListPage` 统一列表页装配；四态状态机有公共件（ReEmpty/useConfirm/normalizeError/isSuccess）。抽样未见明显重复数据流——详细量化属 Phase 2。

### 5.3 后端模块化
抽查范围内无超 500 行文件、无上帝类；ViewSet 均为薄视图 + services 下沉（codegen/permission_sync/webhook/task_log 等）；跨 app 引用走 `system.services.modelset`（RolePreviewAction 等共享动作）与显式 import，`check_cross_app_imports` 门禁在管。

### 5.4 二开友好性
扩展点文档化充分（permission_meta 装饰器注册、APPROVAL_BIZ_SYNCERS、通知渠道单文件制、ModuleSpec 声明）；代码生成器与 CLI 同源；demo 插件三通道装配有守护测试。

### 5.5 规范符合度
| 类型 | 违规数 | 典型证据 |
|---|---|---|
| 元数据协议 | 0 | 有 table_fields 的视图均混入两 Action（全局路由内省 + 抽查） |
| 权限种子 | 1（死配置） | audit-P2-01 |
| 契约先行 | 0（未发现漂移，B） | docs/schema 镜像与 check:contract 门禁在管 |
| 行数/函数门禁 | 0 | 抽查均达标 |
| i18n | 0（本批未发现新增裸文案） | |
| 缓存键 | 0 | 通知未读汇总键按前缀登记（user_site_msg.py:34） |
| docstring 首行 | 0 | 抽查视图类首行均 ≤24 字名词短语 |

### 5.6 安全（静态）
按类别：对象级越权 0（批次 1-8 逐页核对 queryset 收口）；上传/路径 0（文件中心三重校验 + kind 一致性）；SSRF 0（webhook 三层防链 + 出站公共件）；注入 0（数据集 DSL 白名单引擎、NL 查数同链）；密钥泄漏 0（凭据总览不回传值、webhook secret 加密一次性回显、设置密文 write_only）；限流 0 缺口（登录/注册/找回/口令核验/上传全覆盖）；审计 0 缺口（日志不可灭迹口径三处一致 + 文件访问审计 + MCP 调用审计）。

### 5.7 性能与容量（静态）
抽查全部列表页有 select_related/prefetch 或注解计数；深翻页用 Keyset；聚合面板 10s 缓存 + 主动失效；NL/数据集字段权限 memo 缓存。未发现 N+1 与无界查询。

### 5.8 可靠性与可观测（静态）
并发防护三例（审批 CAS、webhook retry 行锁+代际、模块 apply 写锁）；事务边界（上传整批原子、投递重置同事务）；异常受控（批量逐项隔离 + failures 明细、解密失败受控口径）；静默降级均有显式标记（截断/降级/跳过）。

### 5.9 兼容与升级
种子幂等（load_init_json --check 门禁）、迁移线性演进纪律（AGENTS §四）、共享端点向后兼容由 SHARED_PERMISSION_PRIORITY 保证。存量库升级路径属交付链路审查范围（另行安排）。

## 6. 行动建议（并入 DEV-TASKS 候选，详见 [pages-actions.md](code-review-2026-10-actions.md)）

| 优先级 | 建议 | 涉及文件 | 工作量 | 验证方式 | 回归风险 |
|---|---|---|---|---|---|
| P2 | 删除死权限点 `retrieve:SystemUserLoginLog`（无路由无消费） | `audit/seeds/menu.json` | S | `sync_menu_permissions --dry-run` 缺口 0 + `permission-audit` | 低（无消费面） |
| P1 | 新增共享端点须登记 `SHARED_PERMISSION_PRIORITY` 或改解析——写入 AGENTS「新增共享请先改解析」已有条目，建议在 PR 模板/checklist 固化 | `packages/xadmin-common/common/core/permission.py` | S | 守护测试 test_shared_permission_endpoints | 无 |
| E | 仪表盘联动重拉期间加轻量 loading 指示 | `xadmin-client/src/views/dashboard/components/ChartCard.vue` | S | 视觉基线 | 低 |

## 7. 未验证事项（静态审查边界，全部 C 级）

1. **运行时行为**：登录防枚举延时口径、MFA 412 协议实流、WS screen/dashboard/chat 通道鉴权与断线重连、模块裁剪后旧 token 的菜单缓存即时性。
2. **审批引擎流转矩阵**：多级链加签/转办/回退与终态组合未逐条枚举（已有守护测试 test_sensitive_approval_mounts.py 覆盖挂载面）。
3. **MCP server URL 写入侧校验**是否复用 `validate_outbound_config_url`（出站公共件存在，未逐行确认）。
4. **暂存改动（42+14 文件）**未提交：本审查以工作区现状为事实源；其是否通过全量测试未实跑（静态审查不执行 pytest）。
5. **交付链路**（installer/web/docs、安装/升级实测）不在本轮范围。
6. **Phase 2 前端专项**（横切 A–F + 逐页 UI + 量化指标）尚未执行，交接信息见 [pages-actions.md](code-review-2026-10-actions.md) 三节。
