# ADR-032：审批接入业务系统（通用业务绑定 + 请假业务）

- 状态：已接受
- 日期：2026-09-14
- 关联：[ADR-012](ADR-012-approval-flow-engine.md) / [ADR-016](ADR-016-approval-flow-phase2.md)（审批流引擎一/二期）；
  [ADR-026](ADR-026-dynamic-form-approval.md)（表单挂接敏感操作审批）；`system/utils/approval.py`（令牌审批协议）；
  `approval/models/leave.py`、`approval/utils/leave.py`（本文交付的业务接入）

## 背景

审批能力到 ADR-016 为止只完成了「引擎侧」：流程定义（含排他网关/版本快照）、实例、节点任务、
加签/撤回/超时催办、比例会签，以及一套与之独立的「敏感操作令牌审批」。

但系统里没有任何业务真正用它，具体表现为：

1. `ApprovalInstance` 只挂 `ApprovalFlow`，业务数据是自带的 `form_data` JSON，**没有任何通用业务绑定字段**
   （全仓无 `GenericForeignKey` / `content_type` / `biz_id`），流程实例无法指向系统里任何一张业务表；
2. 除 `views/admin/approval*.py` 与 `views/dform.py` 外，**没有任何业务模块调用引擎**——用户/角色/部门/字典/
   导出/导入/文件/通知等全部未接入；
3. 唯一的挂载点（`@ApprovalRequired()` on `user.destroy`）默认关闭：`APPROVAL_REQUIRED_PATHS` 为 `[]`，
   `path_intercepted` 直接返回 False。

结果是「引擎建完了、接线没做」：流程审批页只能审用户手工填的表单，与系统数据没有关系。
本 ADR 解决的就是这条接线，并以「请假」作为第一个真实业务接入方（真实业务形态：申请人 → 直属主管审批 →
天数超过阈值追加复核节点），把引擎从演示态推进到可用态。

## 决策

### 1. 通用业务绑定：`ApprovalInstance.biz_type` + `biz_id`（不引入 GenericForeignKey）

- `biz_type`：业务标识（本项目取模型语义名，如 `"leave"`）；`biz_id`：业务行主键的字符串形式；
  两者联合索引 `idx_appr_inst_biz`；空值 = 引擎自带表单的独立申请（历史行为完全不变）。
- **不引入 `content_type + object_id`**：本项目全仓无 ContentType 用法，引入会让实例状态、迁移、
  权限可见域都多一层间接；`biz_type` 是受控枚举（回调分发键），`biz_id` 用字符串而非 FK，
  是为了兼容 UUID / 自增 / 复合键等不同业务主键形态——代价是失去 DB 级引用完整性，由业务侧
  「实例终态 → 业务行状态」的回写保证一致（见 §2），业务行被物理删除时仅记日志跳过。

### 2. 终态回调：`approval_instance_finished` 信号

- 回调点是 `system/utils/approval_flow.py:_finish_instance`（APPROVED / REJECTED / CANCELLED 三个终态
  的唯一收敛点），发送 `system/signal.py` 的 `approval_instance_finished`（kwargs：instance/status/reason）；
- **为什么用自定义信号而不是 `post_save`**：终态写库走 `queryset.update()`（条件更新 + 并发占位语义），
  根本不会触发 `post_save`；信号是显式发送的，语义更清楚；
- **只在 `biz_type` 非空时发送**：未绑业务的实例不产生任何回调，引擎与历史用例零影响；
- 接收方在 `system/signal_handler.py` 按 `biz_type` 分发（当前仅 `leave`；新增业务加一个分支即可，
  引擎侧不需要再改），异常只记日志——业务回写失败不得反向阻断审批状态机。

### 3. 第一个业务：请假（`Leave`）

- 字段只存业务事实 + 状态 + 实例外键：`leave_type / start_date / end_date / days / reason / status /
  instance`；**当前节点、审批轨迹、驳回原因一律读实例**（序列化器的只读派生字段），不在业务单上冗余，
  避免两处状态不一致；
- 状态机：`DRAFT → PENDING → APPROVED / REJECTED`，`CANCELLED` 为撤回；状态由引擎终态信号回写，
  业务视图**不提供审批动作**（审批统一在「流程审批」中心处理，杜绝第二套审批入口）；
- 校验（`approval/utils/leave.py:validate_leave_payload`，接口与提交前各校验一次）：结束日期不得早于开始日期、
  天数 ≤ 起止跨度（允许半天 0.5）、同一申请人不得存在区间重叠的未结束申请；
- 流程解析（`resolve_leave_flow`）：配置 `LEAVE_APPROVAL_FLOW_CODE`（默认 `leave`）→ `leave_<类型>` →
  `leave` 前缀的启用流程；**找不到流程即拒绝提交**并提示管理员，而不是静默直通。

### 4. 新增即提交；提交失败退化为草稿（不静默丢数据）

请假接口 `POST /api/system/leaves` 保存后立即走 `submit_leave`：成功 = `PENDING` + 绑定实例；
失败（无流程/节点无候选）**保留草稿**并把原因作为提示返回，用户可在列表里修复后重新提交——
既不静默直通（绕过审批），也不把用户填的数据丢掉。`create_instance` 的 fail-closed 校验
（可达节点必须有人可审）原样生效：任一节点无人可审即拒绝发起。

### 5. 业务侧可见域与权限

- 请假列表取值域：超管全部；其余「我提交 ∪ 我审批过（待办/已办）」（`leave_conflict_queryset`），
  与流程审批中心的页签口径一致；显式覆盖 `filter_backends` 去掉默认的 `BaseDataPermissionFilter`
  （默认拒绝语义会把普通用户的列表变成空集）；
- 序列化器 `ignore_field_permission = True`：业务单由普通用户自助提交，无 `FieldPermission` 配置时
  非超管会被裁成「空字段集」（dform/dataset 同款处理，历史坑）；
- 菜单与权限点随 `loadjson/menu.json` 下发（页面 `SystemLeave` + `list/create/retrieve/partialUpdate/
  destroy/batchDestroy/submit/cancel/stats` 九个权限码），字典 `leave_type` / `leave_status` 进
  `loadjson/datadict.json`（`is_locked=true`），流程定义 `code=leave` 进 `loadjson/approvalflow*.json`；
- 全局搜索登记 `leave` 分组（ADR-028 口径，页面权限 + 行级收敛）。

### 6. 敏感操作审批挂载点扩展（默认仍休眠）

- 在「删除用户」之外，补挂 `@ApprovalRequired()`：角色删除/批量删除、部门删除/批量删除
  （`system/views/admin/role.py`、`dept.py`）；
- **默认 `APPROVAL_REQUIRED_PATHS` 仍为 `[]`（不改变任何既有行为）**，理由有两条：
  ① 令牌审批是「拦截 → 审批 → 携令牌重发」的交互，开启后会改变对应操作的调用契约，应由运维按
  治理需要显式开启；② E2E 与既有自动化会在这些路径上做删除，默认开启等于让所有部署的
  自动化失效。开启方式（示例）：
  `APPROVAL_REQUIRED_PATHS = ["^/api/system/user/.*", "^/api/system/role/.*", "^/api/system/dept/.*"]`，
  配套 `APPROVAL_APPROVER_ROLES / APPROVAL_APPROVER_PERMS` 指定审批人（否则回退「全部在用超管」，
  单管理员部署会因申请人不能自审而无人可审）。挂载点的行为由集成用例钉死（默认休眠直通 / 配置后 412 + 1002）。

### 7. 演示与种子

- 定义类（流程定义/字典/菜单/配置）随 `load_init_json` 灌入，实例类（请假单）由
  `python manage.py seed_demo_leave` 用**真实引擎**推进（提交 → 通过/驳回/停在待办 + 一条草稿），
  并把内置「演示部门」的负责人设为演示审批人、申请人归属该部门——请假首节点是 `leader` 类型，
  没有部门负责人时提交会被 fail-closed 拒绝。

### 8. 明示不做（边界）

- 不做通用「任意模型一键挂审批」的框架级抽象（`biz_type` 分发 + 业务侧同步函数已够用；真正的通用化需要
  业务模型自描述审批语义，收益不明）；
- 不做审批通过后的业务自动重放（对比 ADR-026 的令牌审批：请假是「先落库再审批」，不存在请求重放问题）；
- 不做请假与考勤/假期余额的联动（无考勤模块；余额扣减属于另一个业务域）；
- 不做业务单上的「审批人直选」（审批人由流程定义解析，与 ADR-012 的四类审批人口径一致）；
- 不把 `ApprovalRequired` 默认打开（见 §6）。

## 测试与验收

- 单测（`tests/unit/system/test_leave_approval.py`）：日期/天数/区间冲突校验；提交流程解析与回退；
  提交绑定 `biz_type/biz_id` 与 `form_data` 透传；通过/驳回/撤回的业务状态回写；驳回后可重提（换实例）；
  无流程 / 节点无候选时 fail-closed 且保留草稿；未绑定业务的实例不触发业务回调（引擎行为不变）。
- 集成（`tests/integration/system/test_leave_api.py`）：权限（无菜单权限 403 / 授权后可读写）；
  新增即提交并绑定实例；无流程时退化为草稿；审批中心通过/驳回后业务单状态与驳回原因可见；
  撤回 → 重新提交换新实例；区间冲突被拒；审批中不可删除；取值域（本人可见、他人不可见、超管全量）。
- 集成（`tests/integration/system/test_sensitive_approval_mounts.py`）：默认配置直通；配置路径后
  角色/部门删除返回 412 + code 1002 + 业务未执行 + 落 PENDING 审批单。
- 种子守护（`tests/unit/system/test_builtin_seed.py`）：新增流程定义与 v1 快照一致、节点审批人配置合法；
  此处修正了一处过严断言（原写法 `assignee_type != "leader" and assignee_value` 实际禁用了 `leader` 节点，
  与「非 leader 节点必须有审批人」的本意相反）。
- 门禁：pytest 全量 / ruff（check + format）/ zh po 覆盖率（`test_i18n_po.py` + `compilemessages`）/
  前端 vitest（含 `locale-keys` 与 RePlusPage 注册表）/ typecheck / eslint / prettier。
