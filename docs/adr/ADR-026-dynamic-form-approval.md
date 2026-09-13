# ADR-026：动态表单二期（提交挂接审批流）

- 状态：已接受
- 日期：2026-09-13
- 关联：年度开发计划 2026.10-2027.09 §四 W10'（G5b）；[ADR-025](ADR-025-dynamic-form-phase1.md)（动态表单一期）；
  [ADR-012](ADR-012-approval-flow-engine.md) / [ADR-016](ADR-016-approval-flow-phase2.md)（审批流引擎）；
  `system/utils/approval.py`（敏感操作审批协议）；`docs/architecture/notification-channels.md`（审批通知沿用）

## 背景

一期（ADR-025）把「采集登记」收敛为「8 种控件 JSON Schema + 通用 JSON 存储 + creator 隔离」，并明确把
**提交审批流挂接**列为二期（G5b）边界。业务场景：设备/资产/入网申请等登记数据，需要「填报人提交 →
主管审批 → 通过才落库」，否则任何人都能直接往登记表里写数据。

审批基础设施已存在（敏感操作审批：`@ApprovalRequired()` + 412 协议 + 一次性令牌 + 审批中心），
本期的核心决策是**如何把表单提交接到既有协议上**，而不是新建一套审批链路。

## 决策

### 1. 开关在表单定义上：`DynamicForm.approval_required`

- 布尔字段（默认 `False`，迁移 `0020`）：开启 = 该表单的提交需审批；关闭 = 与一期一致直接落库；
- 开关只影响**新提交**，不改变存量提交（与「schema 变更不回填」同口径）；
- 与审批中心/审批流引擎的关系：表单只声明「要不要审批」，**审批人从哪来沿既有全局口径**
  （`APPROVAL_APPROVER_ROLES` / `APPROVAL_APPROVER_PERMS`，皆空 = 全部在用超管），
  不引入「每张表单配一个审批人」的表结构。

### 2. 提交链路复用敏感操作审批协议（412 + code 1002 + `X-Approval-Id` 一次性令牌）

```
POST /api/system/dynamic-form-submissions
  ├─ 序列化器先校验（schema 同源规则：未知键/required/选项/边界/停用表单）
  ├─ approval_required 且非超管：
  │    ├─ 无令牌 → 建 PENDING 审批单（params = 已校验请求体快照）→ 412 + type=approval_required
  │    └─ 有令牌 → 校验「属主 + APPROVED + 未消费 + 未过期 + 指纹一致」→ 消费后 `perform_create`
  └─ 直接落库（= 一期行为）
```

- **校验在前、审批在后**：写进审批单快照的是**已通过 schema 校验的数据**，审批人核对的内容与最终落库内容
  逐字节一致；审批通过后重放时不会因数据格式问题在审批之后才失败；
- **同指纹在途单不重复建单**（`find_active_pending`）：用户连续点击/刷新不刷屏；
- **指纹不一致一律 403 + 审批单置 FAILED**：防「批 A 提交 B」（令牌不能跨请求内容复用）；
- **令牌一次性**：消费成功才落库，重放同一令牌第二次 403，不会重复落库；
- 拒绝/过期/已消费的令牌按 `system/utils/approval.py` 既有分支出 403，前端 http 层清除暂存令牌。

### 3. 超管直提（显式豁免，理由：单管理员部署下的死锁）

`approval_required` 表单对**超管**直通落库，且这一点由集成用例钉死
（`TestFormApproval::test_superuser_bypasses_approval`）。理由：

- 默认审批人集合 = 全部在用超管，且**申请人不能自审**（`resolve_approvers` 排除本人）——
  单超管部署下若不给豁免，超管提交该表单会因「无可用审批人」永久失败（`create_approval` 直接报错）；
- 豁免范围仅限「表单提交」这一条链路，**不影响审批中心/审批流引擎**（那里超管仍按各自状态机流转）。

### 4. 前端：设计器开关 + 填报侧标记 + 复用 http 层令牌暂存

- 设计器：表单弹窗新增「提交审批」开关（`data-testid="form-approval-switch"`），列表新增「需审批/直接提交」列；
- 填报侧：需审批的表单卡片带「需审批」标记，填报表单顶部提示「审批通过后请再次提交本表单」；
- 审批中的交互**不新增前端逻辑**：http 层已按 `412 + type=approval_required` 暂存令牌（`pendingApproval.ts`），
  审批通过后用户重发同一提交时自动携带 `X-Approval-Id` 完成落库（与敏感操作审批同款体验）。

### 5. 明示不做（二期边界）

- 逐表单/逐字段指定审批人或审批链（沿用全局审批人口径；需要流程编排请用审批流引擎 ADR-016）；
- 审批页对表单数据的结构化渲染（审批单 `params` 原样 JSON 展示，字段语义由审批人对照表单定义）；
- 审批通过后的自动落库（须申请人重放，令牌一次性语义不变）；
- 提交数据的编辑/撤回（一期 creator 隔离口径不变；已落库数据的撤回走通用删除/回收站）。

## 测试与验收

- 集成（`tests/integration/system/test_dynamic_form.py`）：
  - `approval_required` 经定义接口读写（设计器契约面）；
  - 提交流程：412 待审批 + 审批单 params = 提交数据 + 事务未落库；超管直提；同指纹不重复建单；
  - 令牌链路：通过后携 `X-Approval-Id` 重放落库、二次重放 403 不重复落库、篡改数据重放 403 + 审批单 FAILED；
- E2E（`e2e/dform.e2e.ts`）：设计器开关往返（设计器列表「需审批」标记 + 填报卡片标记）；
- 门禁：pytest / ruff（check + format）/ i18n po / 前端 typecheck / eslint / locale-keys / prettier。
