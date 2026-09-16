# ADR-044：动态表单与审批流集成 + 审批闭环增强（走查五项）

- 日期：2026-09-16
- 状态：**已交付**（2026-09-17 五项全部落地并验证）
- 背景：对「表单采集 + 审批」做真实场景走查（报销场景全链路实跑），发现三个体系断层与若干能力缺口：
  1. **表单与流程引擎未打通**——表单的“需审批”走全局操作审批（单节点、审批人=角色清单/超管），无法绑定具体审批流程；流程引擎另有独立发起表单（5 种字段），同一业务要在两处各建一份定义；
  2. **审批通过后需申请人手动重提**——服务端不重放请求（一期设计取舍），用户以为提交成功实际未落库；
  3. **驳回后无法重提**——只能重填全部数据发起新实例；
  4. 表单控件仅 8 种，无附件 / 日期范围 / 明细子表，真实业务表单做不出来；
  5. 权限四层（菜单授权 / 权限点 / 字段权限 / 数据权限）任缺一层即不可用，且报错不可自诊断；部门 PATCH 携带 rules/roles 被静默丢弃。

## 决策

### D1 动态表单绑定审批流程（表单 ↔ 流程引擎打通）

- `DynamicForm.approval_flow`（FK，可空）：表单可绑定一个审批流程；**绑定流程时提交进入流程引擎**（不再走操作审批），`approval_required` 仅在未绑定流程时生效。
- `DynamicFormSubmission.status`（PENDING/APPROVED/REJECTED/CANCELLED，空=无需审批）与 `instance`（FK 可空）。
- 提交（绑流程）：创建 submission（PENDING）+ `create_instance(biz_type="dform_submission", biz_id=submission.pk, form_data=data)`；实例终态经 `approval_instance_finished` 信号回写（与请假业务同机制，signal_handler 追加分支）。
- **驳回重提**：`POST dynamic-form-submissions/{pk}/resubmit`——仅 creator、仅 REJECTED、数据可改，重新走一遍提交（编辑数据 → 校验 → 新实例 → PENDING）。请假业务同步放开 REJECTED 可重新提交（前端露出按钮）。
- 兼容：未绑定流程且 `approval_required=False` 的表单行为不变（一次落库）；`approval_required=True` 的操作审批链路保持不变。

### D2 审批通过自动完成提交（去掉手动重试）

- `ApprovalRequest.payload`（JSON，可空）：`create_approval` 时保存**JSON 请求体快照**（仅 `application/json` 且 ≤64KB；multipart/超大 body 存空，仍走客户端携令牌重试）。
- `system/utils/approval.py` 增加**通过后动作注册表**：`register_on_approved(path_pattern, handler)`；`approve_request` 在 CAS 置 APPROVED 后，若命中注册的路径且有 payload，则**在同一事务内自动执行** handler（业务落库），并写 `consume_time` 防重复消费（客户端后续重放同令牌直接返回“已消费”语义）。
- 首个注册方：动态表单提交（`/api/system/dynamic-form-submissions` 且 payload 存在时自动落库）。handler 失败只告警不阻断审批（审批状态已生效，申请人可携令牌手动重试兜底）。
- 前端文案同步：“审批通过后请重试” → “审批通过后将自动完成”（自动完成场景）；操作审批流仍保留手动重试路径。

### D3 表单控件扩展（upload / daterange / table）

- `ALLOWED_TYPES` 增加：
  - `upload`：值 = 文件 pk 字符串数组（走既有文件上传接口），数量上限 20；
  - `daterange`：值 = `[YYYY-MM-DD, YYYY-MM-DD]`，start ≤ end；
  - `table`（明细子表）：schema 增 `columns`（`[{key,label,type}]`，type 限基础类型，禁止嵌套 table；≤12 列），值 = 行 dict 数组（≤100 行），逐行按列校验。
- 导出：非标量值由既有 `_excel_safe` 统一 JSON 序列化，无需特判。

### D4 权限自诊断

- **静默丢弃改显式报错**：`DeptSerializer` 对 PATCH 携带的 `rules`/`roles` 返回明确错误，引导使用专用授权接口（`POST dept/{pk}/empower`）；不再静默丢弃。
- **数据权限 fail-closed 文案**：关联字段解析失败（对象不存在）时提示“对象不存在，或当前角色未配置该数据的行级权限”。

### D5 开箱模板（seed_demo_org）

- 新增命令 `python manage.py seed_demo_org [--reset]`：一次创建「示例组织（部门+主管+员工）+ 员工/主管预置角色（菜单/权限点/字段权限/数据权限四件套）+ 三个场景模板（请假 / 报销 / 入职登记：审批流程 + 绑定表单）」。
- 定位：opt-in 命令（不自动进正式种子），可重复执行（幂等），用于新装系统 10 分钟内跑通第一单。

## 兼容性与风险

- 现有表单（未绑定流程）与操作审批链路零行为变化；新增字段均可空。
- 自动完成提交限定“JSON 小体积 + 注册路径”，不改变通用重放协议；执行失败有告警与手动重试兜底。
- `table` 控件禁嵌套、限行数/列数，防超深 JSON；`upload` 仅存文件 pk，不落文件本体。

## 验证方式（随交付）

- 单测：控件校验（upload/daterange/table 合法/非法/边界）、绑定流程提交+终态回写、驳回重提、自动完成（含 multipart 不自动）、部门 PATCH 报错文案、模板命令幂等。
- E2E：填报绑定流程 → 提交 → 审批通过 → 列表状态 APPROVED（双浏览器）；驳回 → 重提。

## 交付与验证记录（2026-09-17）

正式环境（docker 栈）实测：

- 绑定流程：`demo_staff` 填报「示例-入职登记表」（含附件 / 日期范围 / 明细子表）→ 提交进入流程引擎（PENDING + 实例）→ `demo_fin` 审批 → 提交状态回写「已通过」；驳回 → 「已驳回」→ 一键重新提交 → 「待审批」。
- 自动完成：`demo_staff` 提交操作审批表单 → 412 → 超管通过 → **未手动重放即落库**（原需申请人再次提交）。
- 部门授权：PATCH dept 携带 rules 已落库（原静默丢弃，UI 上改了不生效）。
- 权限点：`availableForms:FormMySubmission`、`resubmit:FormMySubmission` 已写入 `loadjson/menu.json` + `menumeta.json`（新装库随 `load_init_json` 灌入；存量库经 loaddata 补齐）。
- **升级兼容**：`available-forms` 在权限解析上与对应 list 权限同口径（同 `search-columns` / `suggestions` 的既有特例模式）——存量角色未重新授权新权限点时填报仍可用，实测验证（旧角色直接调用返回 200）。
- 门禁：pytest 全量通过；ruff check/format 通过；前端 typecheck / typecheck:strict / eslint / prettier / stylelint / vitest（250）/ check:contract / check:bundle-size 通过；E2E（dform、dform-flow、approval、approval-flow、leave-apply）双浏览器 16 passed。

后续观察项（未纳入本次）：

- 移动端填报/审批（真实 OA 场景大头，成本另计）；
- 控件继续扩展（金额 / 选人 / 级联）按需再做。
