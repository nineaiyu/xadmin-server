# ADR-016：审批流引擎二期（条件分支 / 版本管理 / 比例会签 / 画布配置）

- 状态：已接受
- 日期：2026-09-12
- 关联：ADR-012（一期引擎，本 ADR 的演进对象）；ADR-015 §3（评审决议：
  条件分支可视化选型 @vue-flow）；下期规划 2027.03-08 §八 W2

## 背景

一期（ADR-012）流程模型是**线性节点序列**：节点按 `order` 推进，节点条件只在
"跳过自己"语义上起作用（发起与推进时按 `form_data` 求值），无节点出口的多路
分支；流程定义无版本概念（实例仅快照 `flow_name`，靠「有 PENDING 实例禁止改
节点」维持推进一致性）；审批策略只有 OR/AND 两种；配置界面为列表式编辑。

下期规划 W2 确认三项能力：条件分支（可视化）、流程版本管理与回滚、会签/或签
策略扩展。本 ADR 定二期方案。

## 决策

### 1. 条件分支 = 节点出口路由表（不引入网关表）

`ApprovalFlowNode` 增加 `routes` JSONField（default=list）：

```json
[{"condition": {"field": "amount", "op": "gte", "value": 1000}, "target": 3}]
```

- 语义为**排他网关**（逐条求值、首个命中即出口）：推进时节点有 routes 则按数组
  序求值，命中即跳转 `target`（同流程内节点 order）；全部未命中或无 routes 时
  回退一期线性语义（`order__gt` 的首个条件命中节点），维持兼容；
- 不引入独立 Edge/网关表：单节点出口清单即可表达审批流的排他分支，画布连线
  与 routes 一一对应，避免图模型过度设计（延续 ADR-015 §3 的克制原则）；
- 校验：condition 复用既有 11 种 op 白名单；`target` 必须是同流程有效 order 且
  ≠ 自身；发起时对**静态可达节点集**做 BFS（沿 routes + 线性边）并**环检测**
  （一期无循环，二期校验拒绝成环），fail-closed 预校验覆盖可达集全部节点；
- 兼容：routes 为空 = 一期行为，存量流程零迁移成本（migration 仅加字段）。

### 2. 版本管理 = 快照表 + 回滚动作（不在途实例绑版本）

新表 `ApprovalFlowVersion`（flow FK / 自增 version / snapshot JSONField /
remark / creator）。`ApprovalFlow` 增加 `version` 计数字段：

- 每次保存流程（create/update 且节点或表单有变化）自动落一条全量快照
  （nodes + form_schema）；
- 回滚 = `POST /approval-flows/{pk}/rollback {version}`：校验无 PENDING 实例 →
  快照写入活定义（复用替换式节点重建）→ 落一条新版本（remark 标记回滚来源）；
- 在途实例推进**继续读活定义**（PENDING 锁不变）：实例级版本绑定会把推进语义
  复杂化（current_node FK 与快照节点的映射），一期收益不抵复杂度，登记边界；
- 版本用途 = 变更审计追溯 + 一键回滚，非"在途实例冻结"。

### 3. 审批策略扩展 = 比例会签 RATIO

`approve_type` 增加 `RATIO`，节点新增 `approve_ratio` SmallInteger（1-100，
RATIO 时必填，默认 100）：

- 通过：`APPROVED 数 / 候选总数 ≥ ratio/100` 即节点通过；
- 驳回：`APPROVED 数 + PENDING 数 < 所需最小人数`（不可能达标）时提前驳回；
  全员拒绝必然落入此条件；
- OR/AND 语义不变（OR = 任一通过即过，AND = 全部通过才过、任一拒即驳）。

### 4. 配置界面 = 列表编辑与画布并存

引入 `@vue-flow/core` + `@vue-flow/background`（评审决议）：

- 新组件 `FlowCanvas.vue`：节点卡（名称/类型/审批人摘要，坐标存
  `ApprovalFlowNode.layout` JSONField）+ 条件边（一条边对应一条 route，边上
  标注条件摘要）；线性默认推进不画边（节点 order 表达），画布只呈现分支；
- `FlowConfigDrawer` 增加「列表 / 画布」双模式切换，两模式共享同一
  nodes/routes 数据（画布连线编辑即编辑 routes），保存载荷统一；
- 一期列表编辑器保留：无分支的简单流程列表更快，画布服务分支场景。

## 后果

- migration：ApprovalFlowNode +routes/+layout/+approve_ratio、ApprovalFlow
  +version、新表 ApprovalFlowVersion、ApprovalInstance +flow_version（发起时
  记录当时版本号，纯追溯字段）；
- 一期全部行为保持（routes 空 = 原语义），存量流程零改动；
- 明示不做（登记）：并行网关/汇聚节点、委托代理、驳回回退到指定节点、
  在途实例版本冻结；循环审批（环检测拒绝）；
- 测试：路由推进/回退兼容/环检测/RATIO 三态/版本快照与回滚均带单测；E2E 补
  分支主链路（金额条件走不同节点）与回滚用例；画布 E2E 断言节点与连线渲染。
