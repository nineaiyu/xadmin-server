# ADR-040：审批流三期（动作 MFA 联动 + 委托代理）

| 项目 | 内容 |
|------|------|
| 状态 | 决策 1（MFA 联动）**已交付** 2026-09-15；决策 2（委托代理）设计已定，待实施 |
| 日期 | 2026-09-15 |
| 关系 | 续 [ADR-012](ADR-012-approval-flow-engine.md)（一期引擎）、[ADR-016](ADR-016-approval-flow-phase2.md)（二期画布/网关/版本/比例会签） |
| 依据 | 候选池「审批流三期余量」（[plans/README.md](../plans/README.md)）；MFA 设计见 [mfa.md](../architecture/mfa.md) |

## 背景

一期交付引擎主链路（模板/实例/任务/加签/催办/触发器），二期交付节点出口路由、版本快照与回滚、
RATIO 比例会签、@vue-flow 画布；两期均**明示不做**「委托代理」与「与 MFA 敏感操作联动」
（ADR-016「明示不做」清单）。本期按候选池顺位补齐这两项余量。

## 决策 1：审批动作 MFA 二次确认（已交付）

- **配置**：`APPROVAL_MFA_REQUIRED_ACTIONS`（SysConfig，默认空 = 不启用，逐动作灰度）；
  取值 `approve / reject / cancel / add_sign / batch_approve / batch_reject`。
- **协议边界**：复用 MFA 的 412（`user_confirm_required`）——与「敏感操作审批令牌」
  （`common/core/approval.py`，code=1002 一次性通行令牌）是**两套独立协议**，不互相替代：
  - 审批令牌 = 高危操作**需先走审批流程**；
  - MFA 确认 = 已授权用户操作前**需二次身份验证**（短时免重复，JWT 无 session 走缓存）。
- **实现**：视图层 `_ensure_approval_action_confirmed(request, action)` 在业务变更前校验
  （`mfa.services.ensure_user_confirmed`，跨 app 仅经 services 契约层）；
  前端**零改动**（412 拦截 → 弹验证窗 → 自动重发链路已就绪）。
- **顺序保证**：配置 → 动作匹配 → 确认状态；未通过时**不得推进业务状态**（守护测试断言实例仍 PENDING）。
- **测试**：`tests/integration/system/test_approval_mfa.py`（关闭直通 / 命中 412 / 验证后放行 / 逐动作粒度）。

**增量（2026-09-18）**：动作清单新增 `rollback`（流程定义回滚到历史版本），并把实现从
审批流视图内的私有函数收敛为共享门控 `approval/utils/approval_mfa.py`——审批中心
（`approve / reject / cancel / batch_approve / batch_reject`）与审批流引擎
（`approve / reject / cancel / add_sign / batch_* / rollback`）**双入口统一收口**；
配置项 `APPROVAL_MFA_REQUIRED_ACTIONS` 帮助文案与系统配置描述同步更新（取值集合 6 → 7）。

## 决策 2：委托代理（设计已定，待实施）

目标：审批人可指定代理人在**指定时段 / 指定流程范围**内代审，审计上区分「代审」。

**数据模型（拟）**：

- `ApprovalDelegation`（DbAuditModel + DbUuidModel）：
  `delegator`（委托人 FK）/ `delegate`（代理人 FK）/ `start_time` / `end_time` /
  `flow_codes`（JSON，空 = 全部流程）/ `is_active` / `remark`；
- 唯一性：同一委托人**同时段仅一条有效委托**（业务校验，避免解析歧义）。

**解析语义（改 `resolve_assignees` 出口，不改节点定义）**：

- 待办归属**给代理人**（原审批人不产生待办）——与「加签」区分（加签是增加参与人）；
- 代理期外 / 停用 → 回落原审批人；解析为空仍 **fail-closed 拒发起**（既有语义不变）；
- 申请人本人恒剔除（既有语义不变）；**代理链不递归**（代理人再委托不生效，防环）。

**接口与前端**：

- CRUD + 启停：`/api/system/approval-delegations`（6 权限点，挂审批中心菜单）；
- 前端：审批中心「我的委托」页；待办/实例上标注「由 X 代理」。

**审计与通知**：

- 任务 `actor` 即实际操作人（代理人）；审计 module 标注代审；
- 通知发给代理人（待办人）；委托人抄送列为后续可选项（本期不做）。

## 影响与兼容

- 配置默认空 / 无委托记录 → **存量行为零变化**；
- MFA 总开关（`SECURITY_MFA_CONFIRM_ENABLED`）关闭时，命中动作同样直通（总开关语义不变）。

## 待办（实施顺序，随本 ADR 推进）

1. 委托模型 + 迁移 + `resolve_assignees` 解析改造 + 测试矩阵
   （代理链不递归 / 期外回落 / 申请人剔除 / 空解析 fail-closed）；
2. CRUD 接口 + 权限点种子 + 前端「我的委托」页；
3. 通知与审计口径 + E2E 主链路（委托人建委托 → 代理人收待办并审批 → 审计可见代审）。
