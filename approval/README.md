# approval（审批域：敏感操作审批 + 流程引擎）

> **定位：双审批引擎同域并存**。两套引擎功能各自完整、互不依赖、可随模块裁剪独立关闭，
> 但服务的是两个不同的问题——**接错引擎是二开最常见的返工原因**。本文是"该接哪套"的
> 唯一裁决入口：先看决策树，再看对照表，最后抄对应示例。

## 一、决策树：该接哪套

```text
你的需求是什么？
│
├─「某个高危操作（删除用户 / 导出全量数据 / 批量清理…）执行前必须有人批准」
│   → 敏感操作审批（approvals）
│   挂 @ApprovalRequired() 装饰器 + 配置拦截路径即可，业务代码零改动；
│   请求被 412 拦下 → 审批通过 → 前端携一次性令牌自动重放。
│
├─「一份业务申请（请假 / 上架 / 报销…）要按编排好的流程逐级流转」
│   → 流程引擎（approval-flows）
│   提交时 create_instance(biz_type, biz_id) 挂业务单，终态经信号回写业务状态。
│
└─ 分不清时的两个快速判据
    1. 审批的对象是「一次 API 调用」还是「一条业务数据」？
       调用 → approvals；数据 → approval-flows。
    2. 流程是否需要按数据内容动态走不同分支（条件网关 / 按表单字段定审批人 /
       委托代理 / 加签减签）？需要 → approval-flows（approvals 无这些能力）。
    两者都要（如：请假走流程，删除单据走拦截）→ 各接各的，互不冲突。
```

## 二、两套能力对照表

| 维度 | approvals（敏感操作审批） | approval-flows（流程引擎） |
|------|--------------------------|---------------------------|
| 回答的问题 | 这次操作能不能执行 | 这份申请怎么流转 |
| 触发方式 | 请求命中路径正则**自动拦截**（`APPROVAL_REQUIRED_PATHS` + `@ApprovalRequired()`） | 业务代码提交时**显式发起** `create_instance` |
| 协议形态 | HTTP 412 + 业务码 1002 + 一次性令牌重放 | 业务单状态机 + 实例绑定 |
| 审批人来源 | 全局审批人（`APPROVAL_APPROVER_ROLES` ∪ `APPROVAL_APPROVER_PERMS`，皆空回退超管）；命中规则时按多级审批链（`approval-rules`：路径正则 + 方法限定〔空 = 全部方法，HEAD 按 GET 匹配〕 → 有序级次快照） | 节点编排：role / user / leader（申请人主管）/ field（表单字段指定）/ post（岗位） |
| 会签/或签 | 级次内 OR / AND | 节点内 OR / AND / 比例会签（RATIO） |
| 业务绑定 | 无（请求指纹 + 参数快照） | `biz_type` + `biz_id`（通用业务绑定） |
| 终态回写 | `register_on_approved`（通过后自动执行落库动作） | `approval_instance_finished` 信号 → `biz_sync.py` 注册表（业务 app 在自身 `config.py` 声明 `APPROVAL_BIZ_SYNCERS`） |
| 高级能力 | — | 条件网关 / 版本快照 / 委托代理 / 加签减签 / 转交 / 退回重审 / 催办 / 抄送 / 超时自动动作（通过、驳回、升级） |
| 通知消息类 | `ApprovalRequestMessage`（提交/通过/驳回/超时提醒） | `ApprovalFlowMessage`（全量引擎事件面） |
| API 前缀 | `/api/approval/approvals`、`/api/approval/approval-rules` | `/api/approval/approval-flows`、`approval-instances`、`approval-delegations`、`/api/approval/leaves` |
| 菜单 | 审批中心（SystemApprovalRequest）、审批规则（SystemApprovalRule） | 流程定义/实例/委托/请假（SystemApprovalFlow 等） |
| 模块裁剪 | `ModuleSpec "approval"`（standard） | `ModuleSpec "approval_flow"`（optional） |
| 周期任务 | 过期清理 / 待办提醒 | 节点超时处理 / 提醒 / 清理 |

> 两套引擎共用 `approval` app 与 `/api/approval` 前缀（路由注册见 `approval/urls.py`），
> 但模型、视图、通知、超时清理完全独立；关闭 `approval` 模块拦截整体失效，关闭
> `approval_flow` 模块流程引擎整体失效，互不影响。

## 三、接入示例（抄作业地图）

| 你要做的 | 抄哪里 | 说明 |
|---|---|---|
| 高危操作挂审批（拦截式） | `demo/views.py` 的 `destroy` / `batch_destroy` + `@ApprovalRequired()` | 412 协议、令牌重放前端自动处理 |
| 审批通过后自动落库 | `approval/utils/approval/approved_actions.py` 的 `register_on_approved` | 按请求路径正则注册 handler |
| 多级审批链（按路径配级次） | `approval/utils/approval/chains.py` + 审批规则页 | 命中规则的单按级次快照逐级推进 |
| 业务单挂流程（全量接入） | `approval/utils/leave.py`（请假，注释版四步） | 校验 → 提交 → 绑定 → 回写 |
| 最小流程接入示例 | `demo/services.py` + `views.py::submit` | `create_instance(biz_type="demo_book")` + 回写 |
| 表单驱动的流程接入 | `dataset/utils/dform_flow.py` | 动态表单提交走流程引擎 + 条件收敛 |
| 终态回写注册 | 业务 app 自身 `config.py` 声明 `APPROVAL_BIZ_SYNCERS`（见 `dataset/config.py` / `demo/config.py`） | 新增回写业务零核心文件改动 |
| 通知接入 | `approval/notifications.py`（两套消息类同文件） | `@register_message` 装饰即注册 |

文档与决策记录：

- 二开速查：`docs/guide/recipes.md` R17（业务接入审批流）
- 引擎设计：`docs/adr/ADR-012` / `ADR-016`（流程引擎一、二期）
- 业务绑定与回写协议：`docs/adr/ADR-032`（biz_type/biz_id + 终态信号）
- 动态表单联动：`docs/adr/ADR-044`
- 模块裁剪：`docs/architecture/模块化与功能裁剪.md`
