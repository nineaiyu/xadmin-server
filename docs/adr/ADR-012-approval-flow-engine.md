# ADR-012：全量审批流引擎一期（流程定义 / 实例 / 节点任务）

- 状态：已接受
- 日期：2026-09-11
- 关联：排期文档《剩余任务排期-2026.09-2027.02》F1-F4（10 月候选池①）；
  N3 二期轻量审批流（`system/utils/approval.py`，一次性通行令牌）；
  ADR-003（WebSocket 协议，通知推送复用）；ADR-004 复审（CSP 独立方案，
  本 ADR 不涉及）

## 背景

现有「审批中心」是**敏感操作审批**：`ApprovalRequired` 装饰器在业务执行前拦截，
按「method + path + 脱敏 body」指纹建 PENDING 单，审批通过后由原客户端携一次性
令牌重发同一请求。它解决的是「高危操作二次确认」，不是业务工作流：

- 无表单（只有请求快照，用于指纹比对）；
- 单节点、单审批人集合（先到先得），无多级、无会签/或签、无加签；
- 无「我的申请 / 待办 / 已办」的业务语义（虽有近似页签，但取值域围绕令牌）。

业务侧需要的是通用审批流（请假、报销、变更申请类）：多级节点、条件经过、
会签/或签、指定/角色/上级/表单字段审批人、超时提醒、加签与撤回。本期为其一期。

## 决策

### 1. 双轨制：令牌审批与表单审批并存，不互相改造

- **令牌审批**（既有）：拦截请求 → 批准后重发。保持现状，不动其状态机；
- **流程审批**（本期新增）：业务表单 → 实例 → 节点任务推进。两者共用
  「审批中心」的交互外壳与通知体系，但模型、服务、API、前端页面完全独立
  （`system/models/approval.py` 内并列两组模型；服务分文件）。

理由：令牌审批的核心假设（请求可重放、指纹一致）与业务表单（无请求可重放）
不兼容；强行合并会把两套语义搅在一起。共用的只有通知注册机制、字典状态
（`approval_status`）与页面骨架。

### 2. 数据模型（4 张表，均在 `system` app）

| 模型 | 职责 | 关键字段 |
|------|------|----------|
| `ApprovalFlow` | 流程定义 | `name/code(unique)/form_schema/is_active` |
| `ApprovalFlowNode` | 顺序节点 | `flow/order(flow 内唯一)/approve_type(OR\|AND)/assignee_type(role\|user\|leader\|field)/assignee_value/condition/timeout_hours` |
| `ApprovalInstance` | 一次申请 | `flow(PROTECT)/flow_name(快照)/title/form_data/status/current_node/finished_at/reason`；`creator` = 申请人 |
| `ApprovalNodeTask` | 节点任务（一行一候选） | `instance/node/node_name+node_order(快照)/assignee/actor/status/comment/acted_at/is_added` |

- `form_schema`：`[{key,label,type(text|textarea|number|date|select),required,options}]`，
  发起时按必填校验；
- `condition` 表达式：`{field, op, value}`，op 白名单
  `eq/ne/in/not_in/gt/gte/lt/lte/contains/is_empty/not_empty`，空 = 无条件；
- 快照字段（`flow_name`/`node_name`/`node_order`）：流程改名/改节点不影响历史展示。

### 3. 推进语义（一期边界，明示不做项见「后果」）

- **条件分支 = 节点级跳过**：不满足条件的节点直接不进入（单路径，节点内多路分支不做）；
- **或签 OR**：任一 `APPROVED` 即节点通过，同节点其余 PENDING 行置 `CANCELLED`；
- **会签 AND**：全部 `APPROVED` 才通过；任一行 `REJECTED` → 实例驳回；
- **驳回即终止**（终态 REJECTED，原因必填），不做「驳回到上一节点」；
- **撤回**：仅申请人、仅 PENDING，待办全部作废并通知当前节点审批人；
- **加签**：在当前节点追加候选（`is_added=True`）；会签语义下新候选必须通过；
  权限 = 当前节点参与人（被指派或已处理）或超管；
- **申请人不能审批自己的节点**：候选解析时剔除申请人；动作层再兜底校验。

### 4. 审批人解析（四类）与 fail-closed

| assignee_type | 解析 |
|---------------|------|
| `role` | 角色 code（逗号分隔）成员（`roles__is_active`） |
| `user` | 用户名（逗号分隔）/ 列表 |
| `leader` | 申请人所在部门的 `leader`（无部门/无 leader → 空） |
| `field` | 表单字段值（用户名或用户名列表） |

结果始终剔除申请人本人与停用用户；**发起时对全部条件命中节点做候选校验，
任一节点无人可审即拒绝发起**（fail-closed，避免在途卡死或静默放行）。
推进期若某节点候选因组织变更变空（理论罕见），写一行 `assignee=null` 的审计
任务（comment 注明自动通过）后跳过该节点，保证行为可追溯、不空转。

### 5. 权限与可见域

- 菜单权限点沿用「路径正则 + method」体系：流程定义 6 个（list/create/retrieve/
  partialUpdate/destroy/batchDestroy）、流程实例 11 个（list/create/retrieve/
  approve/reject/cancel/addSign/batchApprove/batchReject/pendingCount/stats）；
- 可见域：超管全部；其余「我发起 ∪ 待我审批 ∪ 我参与过（指派或处理）」；
  页签 scope：`pending`（待我审批，排除本人发起）/`mine`（我的申请）/`done`（已办）；
- 字段权限走既有白名单体系（字段权限页会自动同步新模型的字段树）。

### 6. 超时提醒与清理（复用 beat）

- 节点 `timeout_hours > 0` 且任务超时未处理 → `auto_remind_approval_flow_job`
  （每 30 分钟扫描）向指派人补发一次提醒，同一任务每日最多一次（cache 占位）；
- 实例保留 `APPROVAL_FLOW_KEEP_DAYS`（默认 365 天），`auto_clean_approval_flow_job`
  每日分批删除（级联节点任务）。

### 7. 并发与一致性

任务处理用「条件更新 + rowcount」原子占位（与轻量审批令牌消费同口径），
不使用 `select_for_update`（sqlite 单测/E2E 库不支持）。

## 后果

- 正面：业务侧获得可配置的多级审批能力；与既有审批中心复用通知/字典/页面骨架，
  增量成本可控；`form_schema` 让条件与字段审批人有数据可用；fail-closed 候选校验
  避免「无人可审」的静默卡死。
- 负面：一期无拖拽画布（列表式节点编辑）；无委托代理、无驳回回退、无重新提交；
  条件仅支持「节点级跳过」，不支持同层多分支；节点编辑在存在 PENDING 实例时被
  禁止（需要变更流程时的运营成本）。以上均登记为候选池二期。
- 中性：流程定义删除受历史实例保护（PROTECT + 可读错误），批量删除静默排除有
  实例的流程；`approval_status` 字典同时服务令牌审批与流程审批（状态码同集）。

## 测试与验收

- 引擎单测 13 例（条件/解析/或签/会签/驳回/撤回/加签/超时提醒/清理/fail-closed）；
- API 集成 11 例（CRUD 校验与删除保护、主链路、页签取值域、批量、加签、统计）；
- 越权矩阵 M30-M37（匿名/垂直/水平越权 8 例）入 CI；
- 前端 E2E 主链路（配置流程 → 发起 → 待办通过 → 我的申请/已办可见）。
