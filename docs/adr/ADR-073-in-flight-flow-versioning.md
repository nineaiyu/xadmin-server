# ADR-073：审批流在途实例绑版本（节点有效区间）

- 日期：2026-09-29
- 状态：**已交付**（第二轮重构规划 §7.1-1 / P2 第一项）
- 背景：全量审批流引擎此前采用「锁死改版」策略——只要有 PENDING 实例，流程节点与回滚即被禁止（`approval/serializers/approval_flow.py::_assert_nodes_mutable`）。`ApprovalInstance.flow_version` 只是追溯字段（「推进仍读活定义」，`approval/models/approval.py:234-235`），节点行在每次保存时**物理删除重建**（`serializers/approval_flow.py:250,313`）。后果：连续流量下流程永远改不动（旧单存在即锁），流程迭代被在途单卡死；且 `ApprovalFlowVersion` 的全量快照（审计 + 回滚）与推进读的活定义之间存在语义裂缝——回滚只改「未来的定义」，在途单仍在旧节点上走到被删节点时报「节点已删除」。

## 现状证据（改造前的能力基线）

| 事实 | 位置 |
|---|---|
| 节点物理删除重建：`flow.nodes.all().delete()` + `_replace_nodes` | `approval/serializers/approval_flow.py:248-251,313-315,318-333` |
| PENDING 实例锁：改节点 / 回滚一律拒绝 | `serializers/approval_flow.py:232-234,249,307` |
| 推进读活定义：`next_node(instance.flow, node.order, ...)` 每次从 `flow.nodes` 现查 | `approval/utils/approval_flow/engine.py:270`、`conditions.py:169-193` |
| 任务节点 FK 兜底 SET_NULL + 名称快照 | `approval/models/approval.py:288-298`；`engine.py:320-322`（「节点被删除」fail-closed 提示） |
| 版本快照已具备（审计 + 回滚），明确声明「不做在途实例绑版本」 | `models/approval.py:347-352` |
| 唯一约束 `(flow, order)` 无条件 | `models/approval.py:196-198` |

## 方案对比

| 方案 | 形态 | 成本 | 风险 |
|---|---|---|---|
| **A 推进读定义快照** | 实例存完整定义快照（或按 version 冻结节点表），推进完全读快照 | 高：迁移存量实例、快照与活定义双写、表单/路由/委托人解析全部改读快照 | 快照体积、与 `form_schema` 校验路径的耦合、二开契约面扩大 |
| **B 节点软删除 + flow_version 有效区间（采用）** | 节点行不物理删除，标 `version_from/version_to` 有效区间；实例记住 `flow_version`，推进/发起按版本过滤节点集 | 中：模型加两列 + 约束换形 + 引擎 3 个查询点加 version 参数 + 写入路径换「收口 + 新版本落行」 | 存量行只能标「自 v1 起生效」（历史版本集不可重建，见边界）；监听面（种子/演示命令）需同步适配 |
| **C 改版生成新 code 新流程** | 旧流程只读归档，新流程新 code | 低 | 管理面出现「两个流程」；引用 flow.code 的委托/接入方全部要迁移，产品语义混乱 |

## 决策

采用 **方案 B**。语义定义：

1. **节点行 = 定义事实，不随改版消失**。每个节点行携带有效区间：
   - `version_from`：自该流程版本起生效（含）；
   - `version_to`：到该版本为止失效（不含，NULL = 仍生效）；
   - 「在版本 V 生效」= `version_from <= V AND (version_to IS NULL OR version_to > V)`。
2. **默认查询面 = 当前生效定义**。`ApprovalFlowNode.objects`（默认管理器）只返回 `version_to IS NULL` 的行——`flow.nodes.all()`/`prefetch_related("nodes")`/序列化读取天然只见当前定义，管理面不会显示历史行；版本历史查询显式走 `ApprovalFlowNode.all_objects` + `effective_at(version)`。
3. **改版 = 收口当前生效行 + 按新版本落新行**（事务内）：`version_to = flow.version + 1`、新行 `version_from = 新版本`，Flow.version +1 并落快照。定义无实质变化（快照 JSON 相同）时不落版本、不动节点行。
4. **实例推进按自身 `flow_version` 过滤节点集**：`next_node` / `simulate_path` / 路由 target 解析都限定在实例版本的节点集合内；`flow_version` 为空（历史脏数据）时回退「当前生效定义」，与改造前一致。发起时在流程行锁内读取版本并钉住（与并发改版串行化）。
5. **解锁改版限制**：删除 `_assert_nodes_mutable`（改节点与回滚都不再因 PENDING 实例被拒）。在途单按钉住版本走完，新单按新版本走。
6. **唯一约束换形**：`(flow, order)` 无条件唯一 → **条件唯一**（`version_to IS NULL` 时唯一），允许「历史行 + 当前行」同 order 共存，仍禁止两个当前行同 order。
7. 快照（`ApprovalFlowVersion`）职责不变：审计追溯 + 回滚写回；回滚 = 以历史快照为内容的一次改版（收口 + 新版本落行）。

## 验证

- **核心演练（旧单走旧版 / 新单走新版）**：v1 两节点流程发起在途单 → 改版为「改名 + 删末节点」（v2）→ 旧单按 v1 走完并通过（末节点仍存在）；新单按 v2 只经首节点即通过。
- **回滚演练**：在途单存在时回滚 v1 → 允许；旧单不受影响，新单按「回滚后的 v1 定义」（v3 版本行）推进。
- **约束与查询语义**：同 order 的「历史行 + 当前行」共存、两个当前行同 order 被约束拒绝；默认管理器只返回当前生效行；`effective_at(V)` 对 v1/v2/v3 三段区间各自命中正确行集。
- **无变化保存**：节点内容与快照一致时不落版本、节点行主键不变。
- **存量兼容**：迁移把存量行标为「自 v1 起生效、至今有效」，存量在途实例（任意 `flow_version`）在**下次改版前**看到的节点集与改造前一致；`flow.version < 1` 回填为 1。
- 服务端全量 pytest + ruff/mypy/行数/跨 app/缓存键/文档四件套/`makemigrations --check`；前端 typecheck/lint/单测/e2e（审批流 spec 增补改版解锁守护）。

## 边界

- **历史版本集不可重建**：存量节点行统一标 `version_from=1`——改造前的历史版本（v2 删过的节点等）无法从快照回放成行，故存量实例在**部署后首次改版前**仍读「当前行」；首次改版后即按各自版本冻结（与改造前「读活定义」的行为相比只会更稳，不会更差）。
- **部分索引仅 PostgreSQL/SQLite 落库**：条件唯一约束（`version_to IS NULL`）在 MySQL/MariaDB 上因 `supports_partial_indexes=False` 被 Django 跳过（不报错、不生效）——那些引擎上「同一时刻只有一个当前行」由写入路径保证（流程行锁 + 先收口再落行，全走 `apply_definition` 单一入口）；默认部署形态（PostgreSQL）有 DB 级约束兜底。
- **不做节点级 diff 复用**：改版按整组收口重建（不逐行比对复用），行数随改版次数线性增长；单流程节点量级小（默认 ≤ 10），管理层不受影响（默认只读当前行）。如未来出现超高频改版流程，再评估按内容复用行。
- **不改驳回语义**：驳回仍是整单终止；「退回指定节点」（§7.1-3）后续立项时随版本化方案一起设计（退回 = 新开节点任务，需明确钉住版本）。
- **不引入 BPMN 在途多实例/子流程**：本次只解决「在途单与定义变更的一致性」；并行网关、子流程、超时自动动作（§7.1-2）等仍按各自立项推进。
- **任务级展示字段**（`node_name/node_order`）继续用快照冗余，历史轨迹可读性不依赖节点行是否存在。