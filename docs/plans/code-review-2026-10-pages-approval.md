# 2026-10 全栈功能审查 · 分册：审批域（6 页）

> 主册：[code-review-2026-10.md](code-review-2026-10.md)。证据等级：A=代码直接确证，B=跨文件静态推断，C=待运行时验证。
> 域构成：审批中心（敏感操作审批，approval 模块·标配）+ 审批流引擎（flow/instance/leave/delegation，approval_flow 模块·可选）+ 规则。

## 1. 审批单（/approval/index）

- **标识**：approval · approval(标配) · 薄壳 44 行（页签 scope）· 元数据驱动 · 后端 `approval/views/approval.py ApprovalRequestViewSet`（只读 + batch-approve/batch-reject/pending-count）。
- **后端实现**：
  - `ApprovalScopeFilter` 三页签取值域：pending=待我审批（与角标共用 `pending_queryset_for`，**角标数与页签行数同源**）、mine=我发起、缺省=我发起∪我审批过∪我可审，超管不设限（`approval.py:55-85`）；不继承 DjangoFilterBackend 防重复过滤（:62-63）；
  - 批量通过/驳回：`ensure_approval_action_confirmed`（二次确认协议）+ 授权收口（全局审批人 or 在途单当前级候选人）+ **逐单隔离**（无权单进 failures 而非整体 403）+ 失效 pk 明细（:144-172）；
  - 列表轻量/详情带 steps（get_serializer_class 分口径 :108-112）；prefetch current_assignees 防 N+1。
- **引擎层状态机**（`approval/services/approval/lifecycle.py`）：approve_request **CAS 条件更新**（`filter(status=PENDING).update(...)`，并发窗口先到者赢，:176-198）；**引擎层自审防护**（`creator_id == user.pk` 拒绝，注释明确兜住批量/内部调用旁路，:191-195）；多级链逐级推进、末级才置 APPROVED。
- **闭环校验**：权限 ✅（scope 而非对象级 403，无单探测面——404/403 混淆不存在，B）；元数据 ✅；契约 ✅（batch 返回 succeeded/failures）；数据 ✅（状态机 CAS + 自审双防护）。
- **问题清单**：无。
- **质量速记**：可靠性 ✅（并发审批 CAS）；工作区新增守护测试 `tests/integration/approval/test_approval_request_scope.py` 与 `test_sensitive_approval_mounts.py` 覆盖本域（暂存未提交）。

## 2. 审批流定义（/approval/flow/index）

- **标识**：approval · approval_flow(可选) · 薄壳 23 行 · 元数据驱动 · 后端 `approval/views/approval_flow.py ApprovalFlowViewSet`（RelationalCountMixin + ImpactPreviewAction + versions/rollback/candidate-options）。
- **后端实现**：版本化（versions 动作）+ rollback 回滚（:98-101 段）；impact 预览（删改影响面）；candidate-options 供设计器选人。审批流（flow）与审批规则（rule）是两套正交机制：flow=多级链审批单，rule=敏感操作拦截规则（模块注释与路由表均可证）。
- **闭环校验**：权限 ✅（全局对账通过）；元数据 ✅；契约 ✅；数据 ✅（版本化 + 回滚）。
- **问题清单**：无。

## 3. 审批实例（/approval/instance/index）

- **标识**：approval · approval_flow(可选) · `instance/index.vue`（140 行，含 scope=ongoing 管理视角页签）· 元数据驱动 · 后端 `approval/views/approval_flow.py ApprovalInstanceViewSet`（add-sign/remove-sign/approve/reject/cancel/return/transfer/urge/comments 等动作）。
- **后端实现**：`ONGOING_PERMISSION_PATH` 功能开关经 `user_has_permission` 门控管理视角（`approval_flow.py:212`，主册 §3.1 功能开关证据）；批量动作在 `approval_instance_batch.py`（逐项隔离同款）；加签/减签/转办/回退均有独立动作端点。
- **前端实现**：ongoing 页签显式权限门控（`index.vue:52` hasAuth("ongoing:...")）。
- **闭环校验**：权限 ✅（功能开关显式登记）；元数据 ✅；契约 ✅；数据 ✅（C：实例状态机的非法流转全量覆盖属引擎深水区，本批核对了动作面与权限面，流转矩阵未逐条枚举——记入未验证事项）。
- **问题清单**：无。

## 4. 请假管理（/approval/leave/index）

- **标识**：approval · approval_flow(可选) · 薄壳 52 行 · 元数据驱动 · 后端 `approval/views/leave.py LeaveViewSet`（submit/cancel 动作）。
- **后端实现**：cancel_leave（`approval/utils/leave.py:180`）；leave 是审批流的演示/业务样例域。
- **闭环校验**：权限 ✅；元数据 ✅；契约 ✅；数据 ✅。
- **问题清单**：无。

## 5. 审批委托（/approval/delegation/index）

- **标识**：approval · approval_flow(可选) · 薄壳 19 行 · 元数据驱动 · 后端 `approval/views/approval_delegation.py`（impact 动作 + `all:SystemApprovalDelegation` 功能开关）。
- **后端实现**：`ALL_DELEGATIONS_PERMISSION_PATH` 经 `user_has_permission`（:65，功能开关证据）；委托 impact 预览。
- **闭环校验**：权限 ✅；元数据 ✅；契约 ✅；数据 ✅（C：委托生效的时间窗交集计算未逐行验证）。
- **问题清单**：无。

## 6. 审批规则（/approval/rule/index）

- **标识**：approval · approval(标配) · 薄壳 29 行 · 元数据驱动 · 后端 `approval/views/approval_rule.py ApprovalRuleViewSet`（190 行）。
- **后端实现**：规则匹配敏感操作拦截（APPROVAL_REQUIRED_PATHS 机制的管理面）；规则与 flow 正交（rule=拦截哪些 path，flow=审批链形态）。
- **闭环校验**：权限 ✅；元数据 ✅；契约 ✅；数据 ✅。
- **问题清单**：无。

## 批次 5 小结

- **发现数**：P0=0 / P1=0 / P2=0 / E=0。
- **正向**：审批引擎三层授权（视图快速拒绝 → 批量逐单隔离 → 引擎层兜底）+ CAS 并发防护 + 自审双防护；scope 页签与角标同源函数；功能开关权限点显式登记且被运行时消费。
- **未验证事项（移交主册 §7）**：审批实例多级链的完整状态流转矩阵（加签/转办/回退与终态组合）未逐条枚举，属静态审查的深度边界；建议由引擎守护测试覆盖（已有 test_sensitive_approval_mounts.py）。
- **已读文件**：后端 `approval/views/approval.py`（55-195）、`approval/services/approval/lifecycle.py`（approve 段）、`approval/views/approval_{flow,rule,delegation,leave,instance_actions,instance_batch,instance_comments}.py`（类/动作清单）；前端 6 页（wc + 红旗 grep 0 + instance 页门控行）。
