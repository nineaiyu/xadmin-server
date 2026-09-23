# ADR-051：JumpServer 对标批二——AI 深化与平台底座（能力画像 / 双轨 / 幂等 / 账本 / 标签 / 任务中心 / 命令面板 / 表格偏好 / SQL 基线 / 生成器）

- 日期：2026-09-22
- 状态：**已交付**（批二 11 项）
- 依据：[JumpServer 对标完善方案（2026.09）](../plans/JumpServer对标完善方案-2026.09.md) 批次二（§四「AI 深化与平台底座」），验收口径「探测 4 项能力有单测；双轨草稿成功率对照日志；`ai_tool_audit` 巡检 0 未确认缺口；标签试点 3 对象闭环 + E2E；任务取消 / 重试 / 统一列表 E2E；SQL 基线表 Top 10 入 CI」。
- 背景：批一补齐了「安全与决策信息完备」；批二解决三件事：① AI 链路**可观测可治理**（模型能力未知 → 换模型静默失败；重复提交可能重复写；成本不可见）；② 平台**轻量运营能力**（对象分类、长任务全生命周期）；③ 工程**回归防线**（N+1 无门禁、生成器产物缺平台能力）。

## 决策

### D1 AI-1 模型能力画像与探测

- `AiProfile` 新增 `capabilities`（JSON，探测结果，可人工 PATCH 修正）与 `probed_at`；新增 `purpose`（`chat` / `structured`）做**用途级档案分流**：结构化链路（NL 查数 / 动作草稿）优先取 `structured` 激活档案，未配置时回落 `chat` 激活档案 → 单档案场景零变化；激活互斥由「全局唯一」改为**用途级唯一**（约束 `uniq_ai_profile_purpose_active`，`set_active_profile` 清同用途旧行）。
- `POST /api/system/ai/profiles/{pk}/probe`（权限点 `probe:AiProfile`）：按序探测四项能力——结构化 JSON（要求固定对象并校验可解析）、`tool_calls`（携带内省工具定义，期望返回工具调用）、`reasoning`（观察 `reasoning_content`，无思考内容**不算失败**）、`vision`（按需，自带 8×8 色块 data URL 素材）。探测**不阻断**：单项失败只记录 `ok=false` + 可读原因。
- 判据收口 `capability_ok(profile, name)`（未探测/结构异常一律不通过，fail-closed），供 AI-2 准入与前端提示。

### D2 AI-2 原生 function calling 双轨

- SDK（`common/sdk/ai/chat.py`）新增 `chat_tools(messages, tools, tool_choice="auto") -> {content, tool_calls, usage, reasoning}`：`tool_calls` 规范化为 `[{id, name, arguments}]`；**`chat()` / `chat_stream()` 签名与行为零变化**（存量链路不受影响）。
- 工具定义转换层（`ai_tool_catalog.openai_tools`）：由既有 `tool_catalog` 推导（**同一份 schema 三种消费**：MCP `tools/list` / 助手页 `tools` / 原生 function calling），附加可选 `_summary` 参数用于确认卡片摘要；dform 的可用表单目录拼进工具描述。
- 双轨分支（`ai_draft_tools`）：`AI_NATIVE_TOOLS_ENABLED` 开关 **且** 结构化档案 `tool_calls` 探测通过 → 优先原生轨道（`drafts_from_tool_calls` 逐项复用 `_build_one_draft` 校验链，产出与 `parse_draft` 同构的 `drafts`）；否则回落 prompt-JSON 轨道（存量桩 / E2E / 弱模型零破坏）。两条轨道打**对照日志**（`ai action draft track: native|prompt`）便于按档案统计成功率。
- 双轨共用下游（草稿结构 + `execute_action` 唯一收口）：审批、权限双门、幂等、审计链路零改动。

### D3 AI-3 工具面巡检 + OpenAPI AI 元数据

- `manage.py ai_tool_audit`：路由面（`build_route_index`，与权限点巡检同源）× 声明面（`API_ACTION_SPECS`）比对，输出「未注册候选清单」（按 domain 分组、标注读/写/危险方法）+「失效声明」（声明了但路由不存在）；豁免清单 `EXEMPT_PREFIXES`（AI 自身端点 / 认证 / 文档 / 内部观测等）；默认只报告，`--fail-on-gap` 可选入 CI（避免强迫注册无意义动作），`--json` 供自动化消费。
- OpenAPI `x-ai-*` 元数据（`common/swagger/ai_meta.py` + `CustomAutoSchema.get_operation`）：声明式动作按 (method, path) 命中即注入 `x-ai-action` / `x-ai-guidance` / `x-ai-required-permissions` / `x-ai-requires-approval` / `x-ai-params`；视图可声明 `ai_meta` 覆盖或补充（如 `visible: false`）。未涉及端点零变化。

### D4 AI-4 动作执行幂等

- `draft_id` = 用户 + 动作 + 规范化参数的服务端稳定哈希（不信任前端回传）；TTL 10 分钟（与 412 审批令牌口径一致）落缓存（JSON 序列化，失败不阻断）。
- 幂等只作用于**成功**结果（失败允许立即重试）；命中返回首次结果 + `deduplicated: true`（execute 响应体可见，前端提示「10 分钟内已执行」）；`force=true` 为用户确认后的「仍要执行」显式通道；Web 与 MCP 通道同口径；审计 `changes` 补 `draft_id` / `deduplicated`（同一意图的所有请求可追溯）。

### D5 AI-5 用量账本与配额

- 新模型 `AiUsageRecord`（档案 / 用户 / 链路 / 模型 / tokens_in·out·total / 耗时 / 成败 / 时间），保留期随 `MONITOR_RETENTION_DAYS` 由周期任务 `auto_clean_ai_usage_job`（每日 03:12）分批清理。
- **写入口收敛**：`tracked_chat` / `tracked_chat_tools` / `tracked_chat_stream` 三个包装（保持 SDK 返回契约）覆盖文档问答、聊天室、NL 查数、动作草稿全部 LLM 调用点；流式在生成器结束时记账。
- 三级配额（SysConfig 可配，0 = 不限，默认宽松）：单用户日调用次数、单用户日 token（读账本当日汇总 + 60s 短缓存）、全局并发流式上限（缓存计数信号量，`stream_slot` 上下文保证异常/中断也释放）。超限统一返回可读 1001；写类动作在审批前置 fail-closed，不产生半执行。
- 端点 `GET /api/system/ai/assistant/usage`（按天 / 链路 / Top 用户 + 配额配置 + 并发占用；权限点零新增：并入 `status:AiAssistant` 的路径正则）。

### D6 P-1 通用标签中心

- 模型 `Tag`（名称唯一 + 颜色 + 内置标记）+ `TaggedItem`（`content_type + object_id + tag`，唯一约束 + 目标索引）；**白名单准入** `TAGGABLE_MODELS`（首批 3 个对象：系统用户 / 文件 / 审批实例，含打标权限回落路径），目标模型侧声明 `GenericRelation` 支持预取（`TaggedPrefetchMixin` 仅在逐行序列化的 action 预取，写操作零额外查询）。
- API：标签 CRUD（4 权限点）+ `resources` / `objects` / `assign`（全量替换）/ `batch-assign`（`replace|add|remove`，逐对象回落业务对象 update 权限点校验，返回逐项失败明细）；删除保护（被引用拒绝并提示先解绑）；`?tag=<名称>` 多值 AND 过滤（`TagFilterBackend` + `TagFilterMixin`，与数据权限叠加）；元数据下发 `tag` 搜索字段（下拉 choices 动态取自标签表 + 60s 缓存 + 变更失效）。
- 前端：标签管理页（RePlusPage + ReDialog）、用户页「打标」行操作（多选弹窗）+ tags 列渲染（自定义色值补文字色与去边框）。
- **边界**：不做全模型铺开；标签治理（重名 / 清理）由使用计数 + 删除保护兜底。

### D7 P-2 任务中心升级

- 「不建新表」只读聚合三类记录（`TaskExecution` / `ExportRecord` / `ImportRecord`）：`GET /api/system/tasks/unified`（类型 / 状态 / 关键字 / 时间范围 + 分页；数据域与下载中心一致，超管全量）；分页正确性由「每类型取数窗口 = 已翻页数 × 页大小」保证，总数为三类真实计数之和。
- **取消（协作式）**：`POST .../unified/cancel`（权限点 `cancel:SystemTaskCenter`）；PENDING 立即置 `REVOKED`（并 `app.control.revoke`），RUNNING 只下 revoke + 落取消标记，任务在**安全点**收敛（导出里程碑检查、导入逐行检查 → `TaskCancelled` → 落 `REVOKED` + 同步执行历史行，不 re-raise）；`ExportRecord` / `ImportRecord` 增加 `REVOKED` 状态与失败区分。
- **重跑（白名单）**：`POST .../unified/rerun`（权限点 `rerun:SystemTaskCenter`）仅导出 / 导入 / 报表；新记录归属操作者并以操作者身份重放（视图路径由原记录 `path` 反解，报表经 `params.report_id` 重派发 —— 为此 `_precreate_record` 补记 `report_id`）；执行历史不支持重跑（走周期任务入口）。
- 前端新页面「任务中心」（`/system/task/index`，菜单 + 3 权限点）：过滤 + 进度 + 取消 / 重跑 / 日志 / 下载；顶栏聚合抽屉保留为快捷入口。

### D8 U-2 命令面板（Cmd+K）

- 与顶栏搜索**合流**（不新建重复组件）：`Cmd/Ctrl+K` 唤起；无关键字时展示「快捷动作」（打开 AI 助手 / 任务中心 / 标签管理 / 切换主题 / 返回首页，按权限点过滤）；导航指针统一（命令 / 菜单结果 / 全局搜索分组 / 历史一条列表，↑↓ 跨区移动、Enter 按类型分派、鼠标点击保持原行为）；全局搜索结果首次纳入键盘导航（高亮可见）。
- 导航与动作注册表拆到 `useCommandPalette.ts`（主组件行数门禁 + 可单测）。

### D9 U-3 表格偏好持久化

- `useTablePrefs`：列显隐 / 列顺序 / 密度以**页面路由 path** 为键持久化（`localStorage` 即时层 + `WEB_SITE_CONFIG.TablePrefs` 跨设备层，本地即写、远端 600ms 防抖单键 PATCH、卸载补发）；列用 **label（i18n key）** 作稳定标识（切语言不改偏好、列删除静默忽略）；未持久化过的页面行为完全不变。
- 表头排序沿用**既有 ordering 元数据**（`search-fields` 的 `ordering` 选项），不新增契约字段（`sortable` 字段评估为「收益不足 + 破坏 `additionalProperties: false` 契约」，登记为后续可选）。**本条仅交付偏好持久化**，表头排序登记为遗留项（见下）。

### D10 E-2 性能门禁（SQL 计数基线）

- `tests/unit/common/test_sql_baseline.py`：10 个高频列表端点（用户 / 角色 / 部门 / 操作日志 / 登录日志 / 导出 / 导入 / 执行历史 / 文件 / 审批）在固定夹具下的 SQL 计数上限（实测基线 + 余量），超出即失败；**只拦硬上限**（数据量波动不误报），首请求常数开销先预热（与既有 N+1 差值用例同手法）。
- 2026-09-22 实测基线：用户 7（含 P-1 标签预取）、角色 4、部门 6、其余 3~4。

### D11 E-3 生成器「生成即接入」

- `generate_crud` 默认产物新增 `<app>/ai_declarations.py`：与 `system/utils/ai_api_registry.py` 同格式的**只读动作声明骨架**（list / detail，可直接注册进统一工具层），写动作以注释给出（启用前确认审批与字段权限）。
- `--with-tags` 附带 P-1 白名单声明（`TAGGABLE_MODEL_KEYS`）；`--with-tests` 生成 pytest 骨架（鉴权 + 列表契约两条最小断言）；产物过 `ruff check / format` 与 `python -m compileall` 门禁（生成器测试守护）。
- `doctor` 新增「AI 声明（生成物）」检查：`<app>/ai_declarations.py` 的声明路径必须能对上路由面（漂移即失败），无声明文件的模块跳过。

## 影响与兼容

- **零破坏面**：`chat()` / `chat_stream()` 行为不变；prompt-JSON 轨道保留（`AI_NATIVE_TOOLS_ENABLED` 默认关，探测通过才建议开启）；标签白名单外模型零变化；任务中心只读聚合不改既有页面与接口；表格偏好未持久化的页面零变化；SQL 基线只在新测试文件内生效。
- **新增权限点 12 个**（已入 `loadjson/menu.json` + `menumeta.json`）：`probe:AiProfile`；`list/cancel/rerun:SystemTaskCenter`；`list/create/retrieve/partialUpdate/destroy/resources/objects/assign/batchAssign:Tag`；另有 1 处既有权限点路径正则扩项（`status:AiAssistant` 增 `usage`）与 2 个新页面菜单（标签管理 / 任务中心）。
- **迁移**：`system/0006_ai_profile_capabilities_and_usage`（AiProfile 字段 + 用途级唯一约束 + AiUsageRecord）、`0007_task_center_revoked_status`（导出/导入状态增 `REVOKED`）、`0008_tag_center`（Tag / TaggedItem + 目标模型 `GenericRelation`）。
- **新增配置键**（`server/conf/defaults.py` + `settings/serializers/ai.py`）：`AI_NATIVE_TOOLS_ENABLED`（默认关）、`AI_QUOTA_USER_DAILY_CALLS` / `AI_QUOTA_USER_DAILY_TOKENS` / `AI_QUOTA_MAX_CONCURRENT_STREAMS`（默认 0 = 不限）。

## 遗留与边界

- **U-3 表头排序**未交付：需在元数据契约中新增 `sortable` 字段（`contract/schema/search-columns.schema.json` 为 `additionalProperties: false`，属破坏性变更）；登记为后续独立小项（优先级低：`ordering` 下拉已覆盖排序诉求）。
- `ai_tool_audit` 默认只报告：未注册候选清单当前未固化白名单，`--fail-on-gap` 需先收敛豁免面后再入 CI。
- 能力探测的 `vision` 项按需触发（默认三项），未纳入前端默认按钮（避免无多模态模型上的无谓等待）。
- 标签首批 3 对象：新增对象需真实使用诉求（候选池确认制，避免白名单无序扩张）。
- AI 用量账本保留期跟 `MONITOR_RETENTION_DAYS`（监控口径），如需独立保留期按需拆配置。

## 验收证据

- 后端：`pytest` 全量通过 + `ruff check/format` + 行数 / 跨 app / 缓存键 / makemigrations / 文档六项门禁全绿；新增测试 8 个文件（AI 探测 14 / 双轨 11 / 幂等 8 / 账本配额 12 / 工具巡检 9 / 标签 12 + 14 / 任务中心 18 + 8 / SQL 基线 1 / 生成器 2 例）。
- 前端：`typecheck` / `eslint --max-warnings 0` / `prettier` / `stylelint` / `vitest 317` / 行数门禁全绿；新增 i18n 词条 60 条（zh/en 对称）。
- E2E：新增 2 个 spec（标签中心、任务中心）双浏览器通过；既有冒烟 / 系统页 / 全局搜索回归通过（见 `xadmin-client/e2e/README.md`）。

## 相关

- [ADR-047](ADR-047-ai-console-persistence-and-tools.md) / [ADR-048](ADR-048-ai-unified-tool-layer.md) / [ADR-049](ADR-049-approval-concurrency-and-ai-permission-parity.md)：AI 助手与统一工具层（本批在其上加能力画像、双轨、幂等、账本）。
- [ADR-050](ADR-050-jumpserver-batch1-security-and-decision.md)：批一（安全与决策信息完备）；本批接续方案 §三 的 AI 线与平台线。
- [ADR-009](ADR-009-data-mask-and-field-permission.md)：脱敏规则（输出脱敏复用口径，批一落地）。
- [ADR-015](ADR-015-reference-project-adoption.md)：借鉴边界（机制级借鉴、不搬体系）。
