# xadmin 全栈功能审查（A 篇）— 2026-10-29

> **状态唯一源**：本文件是本次审查的进度、清单、发现登记簿与批次日志的唯一权威来源；
> 上下文压缩后先读本文件再继续。
> 审查对象：`xadmin-server/` + `xadmin-client/`。`xadmin-installer/`、`xadmin-web/`、
> `xadmin-docs/` 不在本轮（发现交付层问题只记「移交交付审查」）。
> 方法与口径：全量阅读、结论三态（通过/缺口/未验证）、证据等级 A/B/C、P0 必须有 A/B 级静态证据。

## 0. 审查说明

### 0.1 基线与环境

| 项 | 值 |
|---|---|
| 审查日期 | 2026-10-29 |
| server 基线 | `db3d4154`（工作区干净，0 项未提交改动） |
| client 基线 | `80935c52`（工作区干净，0 项未提交改动） |
| 后端版本 | `server/const.py` `VERSION = "5.0.2"` |
| 前端版本 | `package.json` `version = 5.0.2` |
| 只读边界 | 未修改任何源码/种子/迁移/测试；唯一写入为本文件与 `plans/README.md` 索引行 |
| 门禁证据 | 只读门禁输出见 §4.9（后台执行，命令与结果原样记录） |

### 0.2 事实底座核对（对照提示词 §0.4）

| 事实 | 提示词值 | 实测值 | 结论 |
|---|---|---|---|
| 后端版本 | 5.0.2 | 5.0.2 | 一致 |
| 前端版本 | 5.0.2 | 5.0.2 | 一致 |
| `.vue` 总数 | 309 | 309 | 一致 |
| 各域 `.vue` 计数 | system106/approval33/settings29/analysis28/integration27/account19/form18/login10/chat9/dashboard9/welcome6/error5/playground4/oauth2/about1/demo1/invite1/user1 | 与提示词逐项一致 | 一致 |
| 后端 app 清单 | 14 业务 + demo/demo_seed/devtools | 与提示词一致；`integrations` 仅 `apps.py + sdk/`，`captcha` 无 `views.py`（用 `http.py/services.py`） | 一致（结构差异按实际枚举） |
| 组件条目 | 69（另 README/overlay/__tests__） | `ls src/components` = 72 行 = 69 + 3 | 一致 |
| hooks 条目 | 57 | 57 | 一致 |
| 布局模式 | 7 种 | 待 §4 专项核对 | 未验证 |
| 页面清单来源 | 路由 + `views/**/routes.ts` + 菜单种子 + 全目录 | 已按四来源枚举（见 §2.2） | 一致 |

**事实漂移：0 条**（对照 §0.4；`integrations` 无业务代码属代码事实，不是文档漂移，进入 §3 后端审查）

### 0.3 阅读台账口径

- 业务文件只能标「完整 / 排除（附理由）」，不允许「抽查」。
- 历史迁移不作为业务逻辑阅读对象（模型定义必读，迁移与模型一致性按需核对）。
- 排除项：`src/api/types/openapi.d.ts`（生成物）、`dist/`、`node_modules/`、`__pycache__/`、
  `data/`（运行期数据）、图片/字体/第三方源码。
- 行号引用写「仓库根相对路径」：`xadmin-server/...`、`xadmin-client/...`。

## 1. 总览与统计

### 1.1 规模（全量枚举，2026-10-29）

| 面 | 数量 | 口径 |
|---|---|---|
| 路由叶子页（业务） | 65 | 菜单种子 `menu_type=1` 的页面（去重后 65 个 path） |
| 框架/全屏/示例页 | 19 | 静态路由 + `views/**/routes.ts`（见 §2.2） |
| **应审页面合计** | **84** | 65 业务 + 19 框架/全屏/示例 |
| 后端业务 app | 14 | ai/approval/audit/captcha/dataset/file/identity/integrations/message/mfa/notifications/settings/system/task |
| 后端业务代码 | 约 8.55 万行 / 678 文件 | 不含迁移、`__pycache__` |
| 内核包 `common` | 约 2.48 万行 / 192 文件 | `packages/xadmin-common/common` |
| 前端 views | 约 11.7 万行 / 309 `.vue` | 不含 api/components |
| 前端 api | 约 6.94 万行 / 78 文件 | 含类型声明 |
| 权限点（种子） | 669 行 / 664 唯一 `(path,method)` / 4 组重复 | 见 §4.1 |
| 菜单目录 | 14 个 directory | `menu_type=0` |

### 1.2 当前覆盖率（滚动更新）

| 指标 | 当前 | 分母 |
|---|---|---|
| 页面覆盖率（已审） | 84 | 84 |
| 链路完整率（链路完整 + 已审） | 84 | 84 |
| 后端业务 app 核心链路覆盖 | 14（全部业务 app + `common` 内核 + `integrations`/`demo`/`demo_seed`/`devtools` 已全量补读） | 14 |
| 后端非业务面 | 4 已补读（内核/集成/示例/开发工具） | 4 |
| 文件阅读率 | 后端非测试源码 + 前端运行时源码全覆盖（明细见 part4 §1） | — |
| 行数阅读率 | 精确统计见 part4 §1 | — |

> 续审批次 17–23 见 [part3](code-review-fullstack-2026-10-29-part3.md#1-续审范围与阅读记录)；
> 全量补读收口见 [part4](code-review-fullstack-2026-10-29-part4.md)。

### 1.3 问题统计（滚动更新）

| 级别 | 数量 | 说明 |
|---|---|---|
| P0 | 2 | `SEC-P0-01`；`CHAT-P0-01`（part4） |
| P1 | 8（主篇 2 + 续审 3 + 全量补读 3，见 part4 §2.2） | `DATA-P1-01`、`AUTH-P1-01`；`LDAP-P1-01`、`AI-P1-01`、`EXPORT-P1-01`；`ICON-P1-01`、`OAUTH-P1-01`、`SETTING-P1-01` |
| P2 | 0（候选池 48 条：主篇 14 + 续审 10 + 全量补读 24） | 候选不等同登记；按根因合并后再决定升级 |
| E | 0 | |
| 未验证事项 | 7 | 见 §9.2（运行期/真实外部服务） |

## 2. 清单

### 2.1 后端 app 清单

> 计数口径：模型类数（`models` 下的 Model 子类）、视图文件数、`@action` 数、seeds JSON 数、
> tasks/signals 文件数。阅读状态滚动更新。

| app | 职责 | 模型数 | 视图文件 | actions | seeds | tasks | signals | 阅读状态 | 问题编号 |
|---|---|---|---|---|---|---|---|---|---|
| ai | AI 平台（档案/聊天记录/知识库/用量/MCP/NL 查数） | 6 | 9 | 29 | 5 | 2 | 0 | 部分（NL/动作/MCP/档案/前端三页已读；知识库检索/观测未读完） | |
| approval | 审批流引擎（定义/版本/实例/节点/委托/规则） | 13 | 8 | 36 | 10 | 2 | 2 | 已审（实例/动作/引擎/委托/规则/请假 + 前端 6 页） | |
| audit | 审计日志 / 脱敏规则 / 归档 | 3 | 7 | 2 | 8 | 0 | 1 | 部分（日志/脱敏读写已读；归档/清理未读） | |
| captcha | 验证码协议/票据/风险/处置 | 2 | 0（http.py/services.py） | 0 | 0 | 2 | 2 | 已审（协议/票据/风险/处置/服务编排/题型基座与主要题型；统计/观测/告警未读） | |
| dataset | 数据集/仪表盘/大屏/报表/动态表单 | 11 | 8 | 15 | 5 | 0 | 0 | 已审（数据集/仪表盘/报表/大屏/动态表单 + 前端页面） | |
| file | 文件上传/分片/存储/访问日志 | 4 | 5 | 6 | 8 | 0 | 0 | 部分（上传/分片/下载/审计已读；预览/清理未读） | |
| identity | 用户/组织/角色/令牌/登录/MFA/OAuth/SCIM/LDAP | 16 | 33 | 40 | 8 | 3 | 2 | 部分（组织/账号/OAuth/MFA/WebAuthn/登录策略已读；SCIM/LDAP/令牌细节未读完） | SEC-P0-01、AUTH-P1-01 |
| integrations | 仅 `apps.py` + `sdk/`（无 models/views/seeds） | 0 | 0 | 0 | 0 | 0 | 0 | 排除（无业务面；SDK 出站与连接按调用方链路审查） | |
| message | 聊天室（房间/消息/WS/回执） | 3 | 2 | 11 | 5 | 2 | 0 | 已审（房间/消息/WS/附件取件鉴权 + chat 页） | |
| mfa | 多因子（OTP/邮件/短信/Passkey/恢复码） | 1 | 1 | 9 | 0 | 0 | 0 | 已审 | |
| notifications | 通知渠道/模板/站内信/发件箱 | 5 | 5 | 12 | 7 | 2 | 1 | 已审（订阅/模板/公告/渠道后端/站内信） | |
| settings | 系统设置（基础/安全/邮件/短信/LDAP/OAuth/SCIM/IM/Webhook） | 1 | 14 | 8 | 7 | 0 | 1 | 已审（11 页 + 保存契约 + 连接测试服务；邮件/短信投递实现细节未读） | |
| system | 平台域（用户视图/菜单/权限/字典/配置/字段/监控/代码生成/搜索/模块） | 16 | 22 | 43 | 7 | 1 | 2 | 部分（菜单/权限/字段/字典/配置/标签/模块/监控/代码生成已读；搜索/部分任务视图未读） | |
| task | 任务中心/定时任务/导入导出/Webhook 投递 | 8 | 9 | 21 | 7 | 0 | 2 | 部分（执行/周期/产物/Webhook 已读；导入导出服务实现未读完） | |
| demo | 二开示例（示例/调试，不计业务 P0/P1） | 1 | 1 | 3 | 2 | 2 | 0 | 排除（示例面） | |
| demo_seed | 演示数据命令 | 0 | 0 | 0 | 0 | 0 | 0 | 排除（命令面） | |
| devtools | 开发工具（代码生成器宿主） | 0 | 0 | 0 | 0 | 0 | 0 | 排除（开发工具面） | |
| common（内核） | DRF 基座/权限/缓存/元数据/契约/通知/存储 | — | — | — | — | — | — | 已审（权限/序列化器/元数据/过滤/数据权限/认证/凭据/脱敏/出站/验证码/中间件/异常） | |

### 2.2 页面清单与审查结果总表

> **已拆分至 [part2](code-review-fullstack-2026-10-29-part2.md#22-页面清单与审查结果总表)**：
> 65 个菜单种子业务页 + 19 个框架/全屏/示例页的逐页状态与四闭环结论。当前统计：
> 已审 84 / 未审 0 / 排除 0（分母 84）。

### 2.3 链路映射表

> **已拆分至 [part2](code-review-fullstack-2026-10-29-part2.md#23-链路映射表)**：
> 页面 → 前端组件 → src/api → 后端 ViewSet/Serializer/Model → 权限码 → 菜单种子行。

### 2.4 孤儿页 / 未挂路由页面

| 文件 | 说明 | 状态 |
|---|---|---|
| `xadmin-client/src/views/demo/book/index.vue` | 二开示例页；无静态路由、无菜单种子引用（搜索反证：仅示例目录自身引用） | 已审（示例面，不计业务页 P0/P1） |

## 3. 页面详述

> 有 P0/P1 或 ≥1 个 P2 的页面详述见
> [part2 §3](code-review-fullstack-2026-10-29-part2.md#3-页面详述)；无问题页面只进
> part2 §2.2 总表。

## 4. 专项汇总

### 4.1 权限闭环专项

- 种子权限点 669 行 / 664 唯一 `(path,method)`；4 组同 path 多码：
  `api/audit/logs/operation$|GET`（list:SystemOperationLog / changeHistory:SystemUser / changeHistory:SystemDept）、
  `api/settings/basic$|GET|PATCH`（retrieve/partialUpdate:SettingBasic / SettingWatermark）、
  `api/task/executions$|GET`（list:SystemTaskExecution / log:SystemTask）。
  → 与工作区 AGENTS 所述 4 组共享端点一致；已核对声明处：
  `xadmin-server/settings/views/basic.py:35`、`xadmin-server/audit/views/admin/operationlog.py:78`、
  `xadmin-server/task/views/task.py:79` 均用 `@shared_permissions` 登记，
  `tests/unit/common/test_core_permission.py:244-293` 锁定「注册表 ⊇ 兜底表」与主权限点顺序一致。
- 运行时解析以 path 为键（`xadmin-server/packages/xadmin-common/common/core/permission.py:146`
  `get_user_permission`），多码登记会互相覆盖——注册表优先级待读码确认。
- 前端 `check:menu-permissions` / `check:menu-components` 结果见 §4.9。

### 4.2–4.8 专项汇总

详见 [part2 §4](code-review-fullstack-2026-10-29-part2.md#4-专项汇总续)：
- 4.2 元数据协议；4.3 契约；4.4 数据权限与字段权限；4.5 安全（静态）；
- 4.6 性能与容量（静态）；4.7 可靠性与可观测（静态）；4.8 二开与规范符合度。


### 4.9 门禁证据（只读执行，2026-10-29）

原始日志：`/tmp/xadmin-review/backend-gates.log`、`/tmp/xadmin-review/frontend-gates.log`。

| 面 | 结果 | 关键输出 |
|---|---|---|
| 后端 ruff | 通过 | `1948 files already formatted` / `All checks passed!` |
| 跨 app import | 通过 | allowlist 18 项、框架契约缝 8 条、common→server 反向依赖 0 |
| 文件/函数行数 | 通过 | 1003 文件 / 超 500 行 0；函数 ≥100 行 0 |
| 台账/菜单标题 | 通过 | 未发现台账编号；docstring 首行均 ≤24 字 |
| 验证码三件套 | 通过 | 覆盖 1 / 豁免 8；无旧模式；资产 1 manifest |
| 文档五件套 | 通过 | 事实 33 条、索引全覆盖、路径可达、ADR 92 篇一致；体积仅 DEV-TASKS 41.99KB（豁免基线 42KB） |
| 缓存键 | 通过 | R3 无前缀冲突 |
| 迁移漂移 | 通过 | `No changes detected`（DB 不可达仅告警） |
| 前端 typecheck | 通过 | `tsc --noEmit` + `vue-tsc --noEmit --skipLibCheck` 无输出 |
| 契约四件套 | 通过 | OpenAPI 768 条 / 缺失 0；schema 镜像一致；6/6 生成物有消费方；生成类型一致 |
| 表格/弹层/表单 | 通过 | 裸表 11/11 登记；声明式弹层 4/4；手写表单 21/21 豁免 |
| 颜色/字号 | 通过 | 颜色 16 文件/94 处（基线 16）；裸 px 字号 0 |
| hooks/文件行数 | 通过 | 348 hook 文件 ≥120 行 0；巨型文件基线 0 |
| i18n | 通过 | zh 5067 / en 5067 对称；静态引用 3575 全部命中 |
| 菜单对账 | 通过（有告警） | 种子 669 点 + demo 19 点；13 个动作来自无法静态定名的 `usePageAuth`（按通配消费处理） |
| 其他 | 通过 | as-unknown 0；模块环 0；入口闭包 45 条；platform-config 108 键；success-code 1 处（基线）；template-root 0；quarantine 0；version 5.0.2 一致 |
| bundle-size | 通过（接近预算） | 代码 415.9KB / 基线 404.6KB（+11.3KB，预算 15KB）；i18n 72.3KB / 65.2KB（+7.1KB，预算 15KB）；主 chunk 69.7KB |

说明：门禁全绿只能证明「约定守护未被破坏」，不证明功能正确性；本次 P0/P1 均不被现有门禁覆盖
（`check:menu-permissions` 的 13 个通配动作告警即一处证据盲区）。

## 5. 根因与系统性整改

1. **派生输出字段脱离序列化器安全契约**（`SEC-P0-01`）。`SearchUserSerializer` 重写 `Meta` 后
   以模型全字段生成 `read_only_fields`，让 `password` 成为可读字段——安全约束原本只挂在
   `UserSerializer` 的 `extra_kwargs` 上，子类重写即失效。根因不是单个字段，而是「敏感字段
   只靠各序列化器自觉声明」。系统整改：在 `BaseModelSerializer` 建敏感字段拒绝表并默认
   强制 `write_only`/剔除（显式 opt-in 才可输出），再加静态门禁扫描全部序列化器输出面；
   种子字段白名单同步清理。
2. **关系计数绕过软删除管理器**（`DATA-P1-01`）。`Count()` 注解不经过默认管理器，四组
   计数因此把软删除用户算进去，而回退路径与成员列表按管理器排除了软删除，同页数字互相
   矛盾。根因是 `RelationCountMixin` 未把「软删除模型必须带 filter」写成契约。系统整改：
   三处 `relation_count_fields` 改为带 `filter=Q(<related>__deleted_at__isnull=True)` 的
   注解，第四处手写注解同改；补单测锁定列表/retrieve/回退三路径一致。
3. **全局登录开关与登录策略按入口各写一遍**（`AUTH-P1-01`）。`SECURITY_LOGIN_ACCESS_ENABLED`
   只在账密登录与验证码登录视图检查，OAuth 回调、登录 MFA 验证、注册自动登录、文档站登录均未检查；
   注册自动登录也未走 `complete_login` 的登录策略/MFA/成功处置收口。根因是「登录收口」
   只是文档约定，没有落在唯一的签发函数上。系统整改：把开关检查与策略求值下沉到
   `complete_login`/`issue_session_token` 共同入口（或独立的登录入口守卫），各登录视图
   只负责认证因子；为账密/验证码/OAuth/MFA/注册五条路径补同一组回归用例。
4. **外部输入解密异常按入口各自归一**（`AUTH-AES-DECRYPT-500` 候选）。`invite` 与改密
   链路已把解密异常转成受控拒绝，但登录/注册/重置/发码仍直接调用 `AESCipherV2.decrypt`；
   v1 兼容开关开启时坏 base64 会抛异常，最终成为匿名端点 500。系统整改：统一走
   `decrypt_submitted_password` 同口径的受控解密助手，异常/空结果一律按可读拒绝。
5. **验证码/限流/锁定类加固的取舍总体成立**：签名票据 + 一次性 nonce + 场景/动作/sid/fp/IP
   绑定、MFA 确认级别校验、恢复码哈希一次性认领、出站 SSRF 固定解析、OAuth state 与
   浏览器 Cookie 绑定，均未见可绕过的静态证据；本批只登记上述可控面缺陷，不把
   「可被管理员配置的内网目标/回环放行」误报为漏洞。

## 6. 候选清单（R / PERF / SEC / BREAK / OPT）

### 6.1 P2 候选池（未编号，按根因合并后再决定是否升级）

| 候选 | 根因键 | 证据 | 影响页 | 建议 |
|---|---|---|---|---|
| 岗位成员接口未按调用者数据范围过滤目标用户 | POST-MEMBER-DATASCOPE | `xadmin-server/identity/views/admin/post.py:76-122`：`members` 返回岗位全部在用成员、`assign` 可添加任意在用用户（仅校验存在/启用） | system/post | 读取侧按 `get_filter_queryset(UserInfo...)` 收敛；写入侧校验目标用户可见性（或明确「岗位成员不受用户数据权限约束」并写入文档） |
| 部门授权对越权角色/规则静默丢弃 | DEPT-AUTH-SILENT-DROP | `xadmin-server/identity/serializers/department.py:113-132` 用 `get_filter_queryset` 过滤后 `set()`，越权项不报错；用户页同场景显式 400（`identity/serializers/user.py:242-253`） | system/dept | 统一为「逐项校验 + 可读拒绝」，或前端回显实际生效项 |
| 字典列表每行 3 次 `dict_usage()`（各 3 次 Redis GET） | DICT-USAGE-NX3 | `xadmin-server/system/serializers/dict.py:103-118`；`system/utils/platform/dict.py:49-58` | system/dict | 每行取一次 usage 后在序列化器 context 缓存；或合并为一次 pipeline |
| 角色创建 API 强制 `fields` 键 | ROLE-CREATE-FIELDS | `xadmin-server/identity/serializers/role.py:129` `validated_data.pop("fields")`（无默认），直接 API 创建不带 `fields` → 400 | system/role | `fields` 设 `default=dict` 或在 create 中用 `pop("fields", {})` |
| 用户搜索前端 `PreviewTrial` 未显式判定业务码 | FE-200-BIZCODE | `xadmin-client/src/views/system/user/components/preview/PreviewTrial.vue:60-64` 只 catch 异常；HTTP 200 + code≠1000 时静默 | system/user | 统一 `if (!isSuccess(res))` 提示；纳入前端约定扫描 |
| 目录页未走 RePlusPage | DIRECTORY-CUSTOM-LIST | `xadmin-client/src/views/system/directory/components/DirectoryMembers.vue` 自建卡片/列表 + 分页 | system/directory | 展示型页面可接受；如后续加批量动作需回归 RePlusPage 范式 |
| `UserTree.collectNodePks` 类型与主键实态不符 | FE-TYPE-DRIFT | `xadmin-client/src/views/system/user/components/UserTree.vue:76-86` 声明 `number[]`，部门 pk 为 UUID 字符串 | system/user | 改为 `Array<string | number>` |
| 委托「全部流程」语义无法通过新建/编辑写入 | DELEGATION-ALLFLOWS-UNREACHABLE | `xadmin-server/approval/models/approval_delegation.py:32` 定义空列表 = 全部流程；`approval/serializers/approval_delegation.py:105-106` 对新建/显式空值直接拒绝；前端 `delegation/index.vue` 仍展示「全部流程」标签（仅存量行可达） | approval/delegation | 明确产品口径：允许空=全部流程，或从模型/文案移除该语义并迁移存量行 |
| 定时报表对全部非超管可见（无 visibility 维度） | REPORT-NO-VISIBILITY | `xadmin-server/dataset/views/analysis_common.py:19-32`（模型无 visibility 时不做收敛）、`dataset/serializers/report.py:93-108`（仅掩码收件人）；报表名称/数据集/调度/状态对全员可见 | analysis/report | 明确「报表全员共享只读」产品口径，或补 visibility（personal/shared）并收敛列表/详情 |
| Webhook 投递「重试」按钮 show 返回数字 10 | FE-SHOW-NUMBER | `xadmin-client/src/views/integration/delivery/utils/hook.tsx:73-79`：`Boolean(...) && 10` 作为 `show` 返回值（非布尔），排序/显隐语义依赖框架对数字的容错；同文件未使用 `index` 字段 | integration/delivery | 改为布尔返回 + 独立 `index: 10`，并加单测锁定 |
| 菜单 `permissions` 动作按客户端 `views` 字符串导入视图 | MENU-VIEW-IMPORT | `xadmin-server/system/views/admin/menu.py:310-344` | system/menu | 校验 `view` 必须出现在 `api-url` 路由索引中再导入（防任意模块导入副作用） |
| 系统消息订阅列表对「注册表有、库内无行」的消息类型直接 KeyError | SYSMSG-MISSING-ROW | `xadmin-server/notifications/views/notifications.py:123` `sub = msg_type_sub_mapper[message_type]`（无 get_or_create 兜底，用户订阅侧有兜底） | settings/message | 与用户订阅同口径补建/兜底，避免新增消息类型未入种子时整页 500 |
| 匿名认证端点坏密文可打出 500 | AUTH-AES-DECRYPT-500 | `packages/xadmin-common/common/base/crypto.py:324-348`：`AESCipherV2.decrypt` 对 v1（`Salted__`）格式直接 `base64.b64decode`/CBC 解包，坏 base64、短密文、错误填充分别抛 `binascii.Error`/`IndexError`/`ValueError`；调用点未捕获：`identity/utils/auth/captcha.py:103-104`（账密登录用户名+口令）、`identity/views/auth/register.py:73`、`reset.py:83`、`verify_code.py:349`、`login.py:151`（验证码登录口令）。默认 `SECURITY_AES_V1_DECRYPT_ENABLED=True`、各 `*_ENCRYPTED_ENABLED=True`。对照：`invite.py:121-127` 与 `decrypt_submitted_password`（`identity/serializers/userinfo.py:35-58`）已把异常转受控拒绝。复现（v1 开关置 True）：`AESCipherV2("token").decrypt("x")` → `binascii.Error`。 | login/reset/register | 统一受控解密助手（异常/空结果均返回可读拒绝码），登录/注册/重置/发码/邀请共用；补坏 base64、短密文、错密钥、v1 关闭四组用例 |
| 注册提交不复核邮箱/短信通道开关 | REGISTER-CHANNEL-RECHECK | `identity/views/auth/register.py:60-111` 只检查 `SECURITY_REGISTER_ACCESS_ENABLED`；发码侧按 `SECURITY_REGISTER_BY_EMAIL_ENABLED`/`BY_SMS_ENABLED` 收口（`verify_code.py:57-69,331-338`），提交侧未复核；对照 `reset.py:46-57` 与 `login.py:178-188` 均在提交侧复核通道开关，防止「发码后关闭通道」仍可完成。 | login（注册） | 与 reset/login 同口径：`verify_sms_email_code` 后按 `query_key` 复核通道开关，关闭即拒绝（验证码作废） |

## 7. 优化路线图

| 阶段 | 内容 | 验收 |
|---|---|---|
| 立即（P0） | `SEC-P0-01`：移除 `SearchUserSerializer` 的 `password` 输出；`BaseModelSerializer` 建敏感字段拒绝表；清理字段白名单种子；补序列化单测 + 敏感字段门禁 | 单测断言输出键不含 password；门禁扫描 0 违例；`check:openapi-types` 通过 |
| 次批（P1） | `DATA-P1-01`：四组关系计数带软删除过滤；`AUTH-P1-01`：登录开关/策略下沉到统一登录入口，覆盖账密/验证码/OAuth/MFA/注册五路径 | 软删除用例三路径一致；五路径「关闭登录后均拒绝签发」回归通过 |
| 第三批（P2） | 候选池按根因合并：受控解密助手（AUTH-AES-DECRYPT-500）+ 注册通道复核；再处理 `SYSMSG-MISSING-ROW`、`DEPT-AUTH-SILENT-DROP`、`REPORT-NO-VISIBILITY` 等 | 每条候选落地时补对应守护用例；不引入新门禁违例 |
| 持续 | 前端产物预算（代码 +11.3KB/15KB、i18n +7.1KB/15KB）与 `check:menu-permissions` 13 条通配动作告警作为观测项；运行期 C 级事项按发版窗口验证 | 预算余量不低于 20%；通配告警逐条确认意图 |

## 8. 一次性整改 / 破坏性变更

- **响应形状变更**：`GET /api/identity/search/user` 不再返回 `password` 字段（P0 修复）。
  前端全仓未消费该字段（`SearchPicker` 仅用昵称/用户名/头像），OpenAPI 类型需同步；
  外部二开若曾读取该字段属于非法依赖，不提供兼容开关。
- **数值口径变更**：角色/部门/岗位/数据权限的「用户数」在存在软删除用户时变小（恢复为
  与成员列表一致的口径）。无需数据迁移；如页面有导出/断言依赖旧值需同步更新。
- **登录入口行为变更**：`SECURITY_LOGIN_ACCESS_ENABLED=false` 后，OAuth 回调、登录 MFA
  验证与注册自动登录同样拒绝签发新 token（原先仅账密/验证码登录拒绝）。这是修复而非
  功能回退；管理员停用登录时应预期所有新会话入口一致关闭。
- **种子清理**：`identity/seeds/fieldpermission.json` 删除 `SearchUser` 菜单的 `password`
  白名单项，需 `load_init_json` 复灌；存量库按 `sync_menu_permissions` 常规流程更新，
  不涉及表结构变更。
- **回滚**：以上均为代码 + 种子变更，无迁移；回滚即回退对应提交并复灌种子。P0 的回滚
  会重新暴露密码哈希输出，不建议在未评估暴露窗口前回滚。

## 9. 行动建议、未验证事项、自检

### 9.1 当前断点（可续跑）

- 页面层：已审 84 / 84（清单见 part2 §2.2；状态为 `已审` 者四闭环均有结论）。
- 后端层：14 个业务 app + `common` 内核 + `integrations` + `demo`/`demo_seed`/`devtools`
  的非测试源码已全量补读，本轮不再保留「未读核心文件」。
- 前端层：84/84 页面沿用 part2；剩余运行时源文件（api/router/store/hooks/utils/layout/
  components/views 子目录）已补读，测试/生成物/样式按验证产物排除。
- 本报告所有 P0/P1 判断均有 A 级静态证据（路径 + 行号 + 推断链），P2 只进候选池；
  运行期未验证项集中在 §9.2。
- 全量补读台账与新增发现见 [part4](code-review-fullstack-2026-10-29-part4.md)
  （新增 P0×1 / P1×3 / P2 候选×24）；后续动作是整改（§7）与发版前运行期验证（§9.2）。

### 9.2 未验证事项（C 级）

| 编号 | 事项 | 原因 / 验证路径 |
|---|---|---|
| C-01 | E2E / 视觉 / 窄屏 / 暗色 / 无权限态 | 本轮未启动浏览器；需 `pnpm test:e2e:*` 与人工目视 |
| C-02 | 媒体 `/media/` 仅登录态鉴权，行级数据权限不生效 | 设计声明见 `packages/xadmin-common/common/utils/media.py:7-16`；需运行时用跨用户 URL 验证影响面 |
| C-03 | AI SSE / MCP `tools/call` / LLM 真实链路 | 需真实模型与流式环境；静态只证明门禁与白名单 |
| C-04 | 设置页「测试连接」（SMTP/LDAP/IM/Webhook/OIDC） | 需真实外部服务；OAuth/Webhook 已静态确认出站守卫 |
| C-05 | 任务中心取消/重跑、WS 推送、Celery 状态流转 | 需运行栈（Redis/Celery）实测 |
| C-06 | 大屏/报表/动态表单执行链路 | 代码与前端页面已静态审查；渲染/调度/查询执行需运行栈实测 |
| C-07 | 迁移漂移命令在真实 DB 下的结果 | 本环境 DB 主机不可达；`makemigrations --check` 已输出 No changes detected |
| C-08 | 登录开关/策略五路径回归 | P1 修复后需在真实栈逐一验证账密/验证码/OAuth/MFA/注册在 `SECURITY_LOGIN_ACCESS_ENABLED=false` 下均拒绝 |
| C-09 | 坏密文 500 修复后的错误码契约 | 需在该环境打开 v1 兼容后逐端点提交坏 base64/短密文，确认返回可读业务码而非 500 |

### 9.3 自检

已执行 `/tmp/xadmin-review/self_check.py`：编号唯一 10 条、P0=2/P1=8、后端 18 行、
part2 已审 84/未审 0、证据路径可达、§5/§7/§8 无空段，全部 PASS；主篇 40.8KB、
part2 37.2KB、part3 14.4KB、part4 23.3KB；`check_doc_size` / `check_doc_index` /
`check_doc_paths` 均通过。

## 附录 A. 发现登记簿

| 编号 | 级别 | 域 | 根因键 | 标题 | 证据 | 影响面 | 同类实例 | 方案 | 验证/防复发 | 状态 |
|---|---|---|---|---|---|---|---|---|---|---|
| DATA-P1-01 | P1 | 身份/关联计数 | RELCOUNT-SOFTDEL | 角色/部门/岗位的关联人数把软删除用户计入 | `xadmin-server/identity/serializers/role.py:62` `Count("userinfo")`、`identity/serializers/department.py:86` `Count("dept_query")`、`identity/serializers/post.py:75` `Count("post_query")`；`packages/xadmin-common/common/core/modelset/relation_count.py:40-50` 仅 `annotate` 不附加 `deleted_at` 过滤（Django 注解不经过软删除管理器）；同一序列化器的回退路径 `get_user_count` 用 `obj.userinfo_set.count()`（`department.py:151-156`）/ `obj.users.count()`（`post.py:78-81`）走默认管理器**排除**已删除用户；`identity/views/admin/dept.py:129-136` 的 `managed` 端点显式 `dept_query__deleted_at__isnull=True`，证明期望口径是排除。**第四处实例**：`system/views/admin/permission.py:59-63` 数据权限列表 `Count("userinfo", distinct=True)` 同样未滤软删除，回退 `DataPermissionSerializer.get_user_count`（`system/serializers/permission.py:120-121`）走 `obj.userinfo_set.count()` 排除软删除。推断链：列表/导出/retrieve 走注解（含软删除）↔ 单对象回退/成员列表走管理器（排除软删除）→ 同页数字不一致。 | 角色列表「用户数」、部门列表/部门树「人数」、岗位列表「成员数」、数据权限列表「用户数」在存在软删除用户时偏大；部门人数点击跳转到用户列表后数量对不上；通讯录左栏部门/岗位计数同样偏大（`SearchDeptViewSet`/`SearchPostViewSet` 复用同一注解）。不影响权限判定。 | 同类扫描：`grep -rn "relation_count_fields" xadmin-server --include='*.py'` 命中 role/department/post 三处；再加 `system/views/admin/permission.py` 手写注解一处；其余 `Count(...)` 注解（数据集/审批等）未使用软删除模型，不属同因。 | 根治：三处 `relation_count_fields` 改为带过滤的 `Count(..., filter=Q(<related>__deleted_at__isnull=True))`，并补 `Q(<related>__is_active=...)` 口径说明（是否统计停用用户按产品口径确定，默认保持现状=含停用）；`RelationCountMixin` 文档补充「软删除模型必须带 filter」。防复发：`tests/unit/system/test_dept_user_count.py` 增加软删除用户用例（列表/retrieve/回退三路径一致），role/post 各补一例。 | 待整改 |
| AUTH-P1-01 | P1 | 身份/登录 | LOGIN-SWITCH-PARTIAL | 全局登录开关与登录策略未覆盖全部登录入口 | `SECURITY_LOGIN_ACCESS_ENABLED` 仅在账密登录（`identity/views/auth/login.py:74-78`）与验证码登录（`login.py:178-180`）检查；OAuth 回调（`identity/views/auth/oauth.py:247-311`）无检查，也未调用 `evaluate_login_policy_for_request`（对照文档站登录 `packages/xadmin-common/common/swagger/views.py:97` 会调用），直接 `complete_login` + `issue_session_token`；登录 MFA 验证（`identity/views/auth/mfa.py:146-162`）无检查，直接签发；文档站登录（`packages/xadmin-common/common/swagger/views.py:70-114`）有策略求值但同样无开关检查；注册自动登录（`identity/views/auth/register.py:60-111`）只检查 `SECURITY_REGISTER_ACCESS_ENABLED`，且**未调用** `complete_login`，直接 `issue_session_token`（:108）+ `save_login_log`（:110），未执行 `evaluate_login_policy_for_request`。`identity/services/auth_login.py:171-197` 明确声明「任何新增登录路径都必须经 complete_login 收口」。 | 管理员将「登录启用」置 false（停服/应急）后：① 已启用 provider 的 OAuth 回调仍可换 token；② 持有未过期 mfa_token 的用户仍可完成 MFA 登录；③ 注册开启时新用户仍自动获得会话；④ OAuth 登录绕过登录访问策略（IP/时段/角色拒绝或强制 MFA）；⑤ 文档站会话登录不受开关约束。另：登录访问策略（按 IP/时间/目标拒绝或强制 MFA）对注册自动登录不生效。 | 同类扫描：`grep -rn "SECURITY_LOGIN_ACCESS_ENABLED" xadmin-server --include='*.py'` 仅命中 login×2、verify_code 配置投影、capabilities、settings 序列化器；oauth/mfa/register 均无。`grep -rn "complete_login("` 仅 login（账密/验证码）与 oauth 回调使用；mfa 验证与 register 未使用。 | 根治：把「登录开关 + 登录策略」下沉到唯一登录收口（`complete_login` 及/或 `issue_session_token` 入口守卫），各登录视图只做因子认证；注册自动登录改为 `evaluate_login_policy_for_request` → `complete_login` → `issue_session_token`，策略拒绝时账号保留但不签发 token；MFA 验证在签发前复核开关。防复发：新增参数化用例覆盖账密/验证码/OAuth/MFA/注册五路径，统一断言「开关关闭 → 不签发 token」。 | 待整改 |
| SEC-P0-01 | P0 | 身份/序列化 | SERIALIZER-SENSITIVE-FIELD | 用户搜索接口返回密码哈希 | `xadmin-server/identity/views/search/user.py:26-58`：`SearchUserSerializer.Meta.fields` 含 `"password"`（:38），`read_only_fields = [x.name for x in UserInfo._meta.fields]`（:45）使 password 变成只读 CharField；子类覆盖 `Meta` 后不继承父类 `extra_kwargs`（`UserSerializer` 的 `password: write_only` 在 :160 失效）。运行时字段内省：`password field: CharField read_only=True write_only=False`，`to_representation` 输出 `pbkdf2_sha256$...`。种子白名单同样含 password：`xadmin-server/identity/seeds/fieldpermission.json:15`（SearchUser 菜单 `6fc056af...` 的三个角色白名单，行 222/774/1278 起）；授权角色见 `identity/seeds/grants.json:47,104,137`（admin/demo/test 均含 `list:SearchUser`）。推断链：`/api/identity/search/user` → `SearchUserViewSet`（OnlyListModelSet）→ `SearchUserSerializer` → 输出含 password 哈希。 | 任意持有 `list:SearchUser` 的账号（种子含 admin/demo/test 三个角色）可批量拉取用户密码哈希；demo/test 为低权限演示/测试角色。前端 `SearchPicker`（`xadmin-client/src/components/SearchPicker/index.vue:58`）在通知收件人、审批转交、登录策略目标等场景调用该接口。哈希可离线破解，且可能跨系统复用。 | 全仓扫描 `read_only_fields = [x.name`：9 处（role/search post/search role/search dept/field/menu/login log/message read），均不含 password；`otp_secret_key`/`token_hash`/`client_secret_hash` 未出现在任何序列化器输出（`grep` 证据见批次日志）。当前唯一实例为 SearchUserSerializer。 | 根治：① 从 `SearchUserSerializer.Meta.fields` 移除 `password`，并按显式安全字段清单重写该 Meta；② 在 `BaseModelSerializer` 增加敏感字段拒绝表（`password`/`otp_secret_key`/`token_hash`/`client_secret_hash`/`secret`/`private_key`），默认强制 `write_only` 或从输出剔除（显式 opt-in 才可输出）；③ 清理 `identity/seeds/fieldpermission.json` 中 SearchUser 菜单的 password 白名单项并复灌种子。数据处置：无需迁移；前端契约无变化（该字段从未被合法消费）。 | 防复发：新增单测 `tests/unit/identity/test_search_user_serializer.py` 断言 `password` 不在 `fields` 或 `write_only=True`，并断言序列化输出键不含 password；新增门禁 `scripts/check_sensitive_serializer_fields.py`（扫描所有 `BaseModelSerializer` 子类的输出字段 vs 敏感字段表，违例即红）并登记 `docs/ci-gates.md`；`check:openapi-types` 复核 schema 无 password 输出。 | 待整改 |

## 附录 B. 批次日志

> 续审批次 17–23 的阅读清单、逐 app 覆盖更新与新增发现见
> [part3](code-review-fullstack-2026-10-29-part3.md#1-续审范围与阅读记录)。

| 批次 | 范围 | 文件数 | 行数 | 完成项 | 新增问题（P0/P1/P2） | 遗留/未验证 | 下次起点 |
|---|---|---|---|---|---|---|---|
| 0 | 启动检查 + 清单 | — | — | 基线、事实核对、三张清单、文档骨架 | 0/0/0 | 门禁后台执行中 | system 域第 1 批 |
| 1 | system/user（用户管理） | 后端 8 + 前端 20 | 约 4,200 | 页面全链路（模型/序列化器/视图/路由/种子/前端页/弹层/api）+ 权限内核与共享端点注册表 | 1/0/0 | 契约/元数据已静态核对；运行期视觉未验 | system 域第 2 批：role/dept/post/directory |
| 2 | system/role + dept + post + directory | 后端 14 + 前端 24 | 约 4,600 | 四页全链路；关联计数口径；部门循环校验；岗位成员双权限点；通讯录数据权限 | 0/1/0 | 运行期视觉/交互未验 | system 域第 3 批：menu/permission/field |
| 3 | system/menu + permission + field + dict | 后端 12 + 前端 16 | 约 5,200 | 菜单树/授权树/规则编辑器/字典两级/字段只读台账；种子动作逐项对账 | 0/1/0（沿用 DATA-P1-01） | 页面交互未验；字典 usage 3×3 Redis 读取为候选池 | system 域第 4 批：config/user + config/system |
| 4 | system/config×2 + credential + tag + module | 后端 12 + 前端 18 | 约 3,100 | 配置加密/掩码与类型校验；凭据只读总览+轮换；标签打标权限回落；模块覆盖锁 | 0/0/0 | 凭据轮换为全局行级动作，确认文案未点明「全部行」；运行期未验 | system 域第 5 批：logs + mask + online + account-risk |
| 5 | system/logs×2 + mask + online + account-risk | 后端 12 + 前端 16 | 约 3,400 | 审计只读/有界预览/脱敏；登录强退；脱敏预览超时；在线会话轮询；风险巡检异步轮询 | 0/0/0 | 运行期未验 | system 域第 6 批：celery×4 + export + file |
| 6 | system/celery×4 + export + file | 后端 18 + 前端 22 | 约 6,200 | 任务白名单双拦截；调度删除引用守护；产物按属主收敛；分片上传属主校验；媒体鉴权 | 0/0/0 | 运行期未验；媒体 `/media/` 登录态粒度（细粒度仅 download/preview）为已知设计 | system 域第 7 批：codegen/monitor/my-scope/notice |
| 7 | system/codegen + monitor + my-scope + notice×2 | 后端 14 + 前端 24 | 约 5,000 | 代码生成方案归属；监控阈值双权限点；管辖页自域；公告 Markdown 双端净化 | 0/0/0 | 页面交互未验 | settings 域第 1 批：basic/security/captcha |
| 8 | settings 全域 11 页 | 后端 22 + 前端 26 | 约 6,500 | 设置保存契约/字段分组/密文掩码；验证码风险阈值；OIDC/Webhook 出站守卫；SCIM 令牌；登录策略；模板净化 | 0/0/0 | 运行期连接测试未验；系统消息订阅缺行兜底为候选池 | analysis 域第 1 批：dataset/dashboard |
| 9 | analysis/dataset + dashboard | 后端 12 + 前端 16 | 约 4,300 | 数据集三层白名单（模型/字段/op）+ 数据/字段权限 + 卡片级授权；WS 按观察者聚合；仪表盘布局校验 | 0/0/0 | 运行期未验 | analysis 域第 2 批：report/screen |
| 10 | integration/ai + config + mcp | 后端 20 + 前端 18 | 约 6,800 | NL 查数四层防线；动作白名单/双权限门/审批/幂等；MCP 内置动作不代理外接；凭据加密与出站守卫 | 0/0/0 | SSE/LLM 运行期未验 | approval 域：定义/实例/动作 |
| 11（后端部分） | approval 后端核心（定义/实例/动作引擎） | 后端 10 | 约 3,400 | 定义版本与节点有效区间；实例可见域；任务归属校验（非本人拒绝/申请人自审禁止）；加签/减签/转交/退回/批量动作收口 | 0/0/0 | 前端 6 页未读；委托/规则/请假模块未读 | 下一批：approval 前端 6 页 + report/screen；其余按 §A7.2 |
| 12 | approval 前端 6 页 + 委托/规则/请假后端 | 后端 6 + 前端 14 | 约 4,800 | 审批定义/实例/转交/委托/规则/请假页面链路；委托授权边界；规则优先级与自审禁止；请假表单字段收敛 | 0/0/0 | 页面交互未验；委托「全部流程」不可达候选 | analysis/report + screen |
| 13 | analysis/report + screen 前后端 | 后端 8 + 前端 10 | 约 4,200 | 报表调度/收件人掩码/可见性；大屏布局校验与 WS 按观察者聚合；数据集权限在报表/大屏链路的复用 | 0/0/0 | 运行期渲染/调度未验；REPORT-NO-VISIBILITY 候选 | 动态表单 |
| 14 | form/designer + form/data + form/my + dataset dform 后端 | 后端 10 + 前端 12 | 约 4,500 | 表单设计器字段/公式解析；提交数据权限；我的表单归属；外部表单令牌与字段白名单 | 0/0/0 | 运行期提交/公式执行未验 | 集成域剩余 + 聊天室 + 个人域 |
| 15 | integration/api-app + delivery + knowledge + subscription；chat；account/user-notice/about/demo；框架/全屏 19 页 | 后端 16 + 前端 30 | 约 7,500 | API 应用凭证/PAT scope；Webhook 投递重试与出站守卫；知识库检索；订阅矩阵；聊天室 WS/附件/回执；个人中心/通知/登录/OAuth/邀请/错误页 | 0/0/0 | 运行期 WS/SSE/外部投递未验 | 后端安全剩余 |
| 16 | captcha 服务剩余 + mfa 全量 + identity 认证链路 + notifications 渠道 + common 内核剩余 | 后端 40 | 约 7,800 | 验证码票据/风险/处置/题型；MFA 后端/恢复码/确认级别；登录/重置/邀请/注册/OAuth/MFA/WebAuthn/模拟；通知渠道；认证/凭据/过滤/数据权限/出站/验证码/异常 | 0/1/2（AUTH-P1-01；AUTH-AES-DECRYPT-500、REGISTER-CHANNEL-RECHECK） | 运行期外部服务/SSE 未验；非核心文件未读清单见 §2.1 | 收尾整改与自检 |
