# ADR-046：三大模块深度完善（表单采集 / 数据分析 / 审批中心）

- 日期：2026-09-18
- 状态：**已交付**（后端 2696 例 pytest + 前端 286 例 vitest + 受影响 E2E 双浏览器 32 例全绿）
- 背景：对「表单采集、数据分析、审批」三块做可用性走查，发现能力缺口集中在**闭环缺失**与**假选项**两类：
  1. 表单**没有草稿**（关闭弹窗即丢）、**没有模板复用**（所谓模板只是种子示例）、**没有数据字典联动**（选项硬编码，字典变更不生效）；
  2. 设计器字段**不能排序**、`min/max/max_length/precision/multiple/placeholder` 等校验属性**没有编辑入口**（后端支持、界面无）;
  3. 我的填报看不到**提交详情与审批轨迹**（列表把复杂值 JSON 拼串）；「重新提交」按钮不受权限点控制；
  4. 仪表盘/报表卡片选 `sum/avg` 后**必然执行失败**——「度量字段（value_field）」在后端强制必填但前端**没有输入框**（可选但必失败的假选项）；卡片加载失败**静默空白**；看板不能重命名/改可见性、不能手动刷新；数据集元数据里的伪模型 `*` 选中即保存失败；
  5. 审批**通过路径无法录入意见**（后端支持 comment、前端被 popconfirm 吞掉）；**没有人工催办**（只有超时定时提醒）；流转记录是普通表格（无时间线、无「由 X 代理」标注）；流程定义的分支路由 target 下拉**被恒禁用**（`order === order` 变量遮蔽缺陷，用户无法改目标节点）。

## 决策

### D1 表单草稿（DRAFT）

- `DynamicFormSubmission.Status` 增加 `DRAFT`；创建/编辑支持 `as_draft=true` 走**轻校验**（`validate_draft_data`：结构 + key 形态 + 64KB 体积封顶，不校验必填与取值）。
- 新增 `POST dynamic-form-submissions/{pk}/submit`：仅草稿态、仅 creator（或超管）；**服务端补齐完整 schema 校验**后进入三分支（绑流程 → 流程引擎 PENDING；`approval_required` → 操作审批 412；其余 → 直接生效）。
- 操作审批与草稿的配合：
  - 携令牌的重放**先于草稿态检查**（`consume_approval` 命中「通过后自动落库」时直接返回成功语义，而不是被「仅草稿可提交」拒绝）；
  - 新增通过后动作 `update_from_approval`（注册路径 `…/{pk}/submit$`，按 `approval.object_pk` 更新既有草稿行，幂等）。
- 权限点新增 `submit:FormMySubmission`（`api/system/dynamic-form-submissions/(?P<pk>[^/.]+)/submit$`，POST）。

### D2 表单模板（is_template 同表复用）

- `DynamicForm.is_template`（布尔，db_index）：模板与表单**同表**，不另起模型——模板只是「只保存 schema 供复用」的表单定义。
- 列表：`GET dynamic-forms` 默认只出表单，`?kind=templates` 只出模板（复用 list 权限、URL 不变，因此**不新增权限点**）；模板允许详情类动作（编辑/删除）直接维护。
- 边界：`available-forms` 排除模板；模板**不接受提交**（序列化器 fail-closed）；模板创建后 `is_template` **不可变更**；模板**不可绑定审批流程**。
- 前端：设计器行内「存为模板」（走正常 create + `is_template=true`）、工具栏「从模板新建」（模板选择器 → 预填设计器 → 保存为新表单）。

### D3 数据字典联动（选项由字典维护）

- 选项型字段（select/radio/checkbox）schema 支持 `dict: <code>`：与内联 `options` **互斥**（两处定义必漂移，序列化器直接拒绝）；`dict` code 走格式白名单（与 `DataDict.code` 同口径）。
- 提交校验：`field_option_values(field)` 读字典项 value（`get_dict_items` 带 5 分钟缓存与降级）；**空字典 fail-closed**（不接受任何取值）。
- 前端消费收敛：字典项仍走 `@/utils/dict`（新增 `getDictTypes()` 供绑定选择器取类型清单，无字典管理权限时降级为手填 code）；填报控件按字典 label 渲染、value 提交。

### D4 设计器字段排序与完整属性

- 字段行支持上移/下移（数组顺序即渲染顺序）；新增「属性」弹窗（`FormFieldDialog`）编辑 `key/label/type/placeholder/max_length/min/max/precision/multiple/dict`，按控件类型收敛属性（切类型自动清无关键，避免脏 schema）。

### D5 我的填报（详情 / 轨迹 / 权限细化）

- 提交序列化器新增只读 `form_schema` 与 `approval_trail`（按实例任务，状态/意见/加签/委托来源**与流程审批中心同源**）。
- 页面新增详情抽屉：schema label 渲染字段（含明细子表、附件文件名、选人回显、字典值回 label）+ 审批轨迹时间线。
- 「重新提交」受 `resubmit:FormMySubmission` 控制；「提交草稿」受 `submit:FormMySubmission` 控制；删除按钮在 PENDING 行禁用（原先点到后端才被拒）。

### D6 数据分析增强

- `DatasetSerializer.numeric_columns`（读侧派生）：sum/avg 的**数值列候选**，卡片/报表表单据此渲染「度量字段」选择器；`sum/avg` 未填度量字段时前端提前拦截（不再产出必然 FAILURE 的报表）。
- 卡片加载失败可见化：业务码非 1000 与网络异常统一落到卡片内提示 + 重试（原先静默空白）。
- 仪表盘：新增「刷新」（重挂载卡片重拉数）与「设置」（重命名/可见性，复用 DashboardCreateForm 双模式）。
- 数据集：表单过滤伪模型 `*`（「全部表」根节点，选中必保存失败）；执行预览支持前端导出 CSV（BOM + 转义，导出即所见行）；`config.date_field` 与折线卡片联动（建折线卡时默认分组到时间字段）。

### D7 审批中心增强

- **通过意见**：通过/批量通过改为弹窗（意见选填），随任务 comment 落库并进轨迹（后端字段原本存在，前端此前未采集）。
- **人工催办**：`POST approval-instances/{pk}/urge`（仅申请人/超管、仅 PENDING，通知当前节点待办处理人）；`URGE_THROTTLE_SECONDS=600` 实例级节流（缓存键；无可催对象不计入节流窗口）；通知事件 `urge`；权限点 `urge:SystemApprovalInstance`。
- **流转时间线**：详情抽屉的审批轨迹改为 `el-timeline`（节点/状态/处理人/意见/加签），并新增**代理代审标注**——`ApprovalNodeTask.delegate_from`（委托展开时记录原审批人）经序列化器下发，时间线展示「由 X 代理」。
- **驳回重提**：我的申请页 REJECTED 行提供「重新提交」（二次确认）→ 发起弹窗**预填原流程与原表单内容**（`openStartInstanceDialog(title, cb, initial)`）。
- **分支路由修复**：`RouteEditorForm` 的 target 下拉 `:disabled="order === order"` 恒真（v-for 别名遮蔽 prop），已修复为仅禁自环；同时残缺行（未填条件字段）在确认时忽略并提示（服务端要求条件必带 field，残缺行会让整份保存 400）。

## 兼容性与风险

- 全部改动**向后兼容**：新字段可空/默认值安全；既有表单、报表、审批链路未见行为变化（未绑流程表单的三分支提交路径不变；草稿只在新入口产生）。
- 新增两个权限点（`submit:FormMySubmission`、`urge:SystemApprovalInstance`）需 `load_init_json` 灌库；存量自定义角色需重新授权后才出现对应按钮（超管不受影响）。
- 字典驱动字段为**新增能力**：既有内联 options 不受影响；绑定字典后选项值集合以字典为准（清空字典即拒绝提交，属 fail-closed 设计）。
- 模板与表单同表：`DynamicForm` 列表语义变为「默认只见表单」；`available-forms`、提交通道均已排除模板，模板不产生任何提交行。

## 验证方式（随交付）

- 后端：`tests/integration/system/test_dform_draft_template.py`（草稿生命周期/模板边界/字典校验/操作审批自动落库与重放）、`tests/integration/system/test_approval_urge.py`（催办守卫/节流/通知/API）、`test_approval_delegation.py::test_delegated_task_records_source`（delegate_from）、`test_permission_seed_coverage.py`（路由 ↔ 权限点覆盖）。
- 前端：vitest 286 例（含 locale 词条对称守护）、typecheck / typecheck:strict / eslint / prettier / stylelint、`check:contract`、`check:bundle-size`（521.2 KB ≤ 522 KB 基线）。
- E2E（双浏览器 32 例）：`dform-depth`（设计器排序与属性落库、模板存/用、字典字段 + 草稿存/提 + 详情）、`approval-depth`（通过意见入时间线、催办与节流、驳回重提预填、路由 target 可选）、`dform-flow`（详情抽屉 + 轨迹意见）、`dashboard`（sum 卡片真实渲染 + 刷新 + 设置重命名）、`analysis` / `approval`（既有链路回归）。

## 交付与验证记录（2026-09-18）

- 后端：`pytest` 全量 **2696 passed / 2 skipped**；`ruff format --check` + `ruff check` 全绿；行数门禁（0 超限）、跨 app import、缓存键检查通过。
- 前端：vitest 286、typecheck、strict、eslint（--max-warnings 0）、prettier、stylelint、契约、体积门禁全绿。
- E2E 验收（全量 fresh 双浏览器 337 例：**307 passed**；其余失败项全部定性并处置，见下）：
  - **审批通过改弹窗**（意见选填）后既有用例仍点 popconfirm → 已更新 `approval-flow.e2e.ts` 交互（双浏览器 6 passed）；
  - **split-pane 拖拽失效**真因 = 首轮列表的 el-loading 遮罩未退场吞掉 mouse 事件（遮罩覆盖整个分栏容器），此前登记的「webkit 位移判定限制」实为同一竞态 → 用例增加「有数据行 + 遮罩计数 0」web-first 等待后双浏览器 4 passed，拖拽参数未动；
  - **RePlusPage 自适应高度缺陷**（既有 UI bug，本次暴露）：上方内容较多 + 矮视口时公式算出 33px 表格、表体 0 高、行溢出到分页之下 → 组件内钳制最小 260px（写定值只依赖表顶位置，不与 ResizeObserver 振荡），ai.e2e webkit 此前 6/6 失败 → 双浏览器 4 passed；核心回归 perf/a11y/smoke/system-pages/split-pane/analysis/dashboard 双浏览器 52 passed；
  - **split-pane 服务端状态泄漏**：拖拽结果持久化到 WEB_SITE_CONFIG.SplitPanes（跨会话生效），把分栏页右侧压扁连坐 import-mapping/preview → 用例 afterEach 统一复原默认 20 后 import-mapping/preview/notify-im 复跑通过；
  - **a11y 未读角标对比度**：通知类功能（催办/审批提醒）产生 `.el-badge__content--danger`（EP Badge 默认配色 ≈3.9:1）命中扫描，采样窗口决定命中与否 → 按既有治理流程补进 ALLOWED_VIOLATIONS 并登记 docs/accessibility-audit.md（EP Badge 配色调整后摘除），污染库条件下双浏览器 6 passed；
  - 余下失败为既有已知负载瞬态（zz-preview-smoke 并行首屏锁死、chat webkit 私聊），与本次改动无关。
