# ADR-015：参考项目借鉴决策（vue-pure-admin 7.0 / jumpserver）

- 状态：已接受
- 日期：2026-09-12
- 关联：排期文档《参考项目借鉴实施计划-2026.09》（P0-P2 全批次执行记录）；
  ADR-012（审批流引擎，P2-5 可视化评估对象）；ADR-008（PAT，P1-3 元数据化对象）；
  docs/architecture/notification-channels.md（P0-4 渠道开发规范）

## 背景

xadmin-client 与 vue-pure-admin（7.0.0）同源分叉（vue 3.5 / vite 8 / element-plus
2.14 / @pureadmin/table 3.3 完全同代），xadmin-server 与 jumpserver 同属 RBAC +
审计 + 任务调度类系统。两者都有大量"已解决过的问题"，但直接搬运会与本仓的
元数据驱动、权限模型（`hasAuth("动作:组件名")`）、统一 `ApiResponse`/`page/size`
分页体系冲突。本 ADR 登记 2026-09 批次借鉴执行中的**边界决策**，供后续迭代
（含 P2 未立项项）复核时引用，避免"抄一半"或"重复造轮子"。

## 决策

### 1. 上游前端：点状移植，禁止整体升级与覆盖

- `layout/**`、`store/**`、`utils/http/**`、`router/utils.ts` 已深度分叉且更厚
  （412 审批挂起/MFA、路由取消、无感刷新），禁止上游整文件覆盖；
- 上游 `permissions:["*:*:*"]` 体系（RePerms/v-perms）不移植：本仓按钮权限是
  `hasAuth("动作:组件名")` + `<Auth>` 组件，两套模型不可调和；
- 可移植组件按"逐文件拷贝 + 最小适配 + 至少 1 处真实接入 + 门禁全绿"执行
  （详见 client CONTRIBUTING §4.2）。已移植：ReTreeLine（接入菜单树）；
  评估后不移植：ReCropperPreview（与 RePictureUpload 能力重复）、print.ts
  （无打印需求，候选池按需启用）。

### 2. jumpserver 后端：借鉴"精细化机制"，不引入其体系性约定

jumpserver 的无统一响应包装、`limit/offset` 分页、`OrgModelMixin` 多租户体系
**不引入**——本仓 `ApiResponse` + `page/size` + 「部门 + 数据权限 Q 编译」更贴合
前端与单租户现实。已落地的机制借鉴：

| 机制                     | 落点                                         | 批次 |
| ------------------------ | -------------------------------------------- | ---- |
| 通知渠道约定式加载       | `notifications/backends/`（SMS 入枚举）      | P0-4 |
| 密码安全套件             | `settings/utils/password.py` + migration 0009 | P1-4 |
| 按权限反查审批人         | `system/services.py::get_users_by_perm(s)`   | P1-5 |
| 内置角色 + post_migrate  | `system/builtin.py` + `UserRole.builtin`     | P2-1 |
| 分布式锁（可重入/续期/事务后释放） | `common/cache/lock.py::ReentrantLock` | P2-2 |
| 内置对象删除保护         | `RoleViewSet`（同内置字典 is_locked 口径）   | P2-1 |

借鉴执行中的**前提纠偏**（核实先于动手的价值记录）：

- 异地登录提醒：jumpserver 的 `check_different_city_login_if_need` 本仓**已有**
  （且另有新设备/新 IP 维度），P1-4 该子项零开发；
- 级联删除审计噪声：jumpserver 的 `CASCADE_SIGNAL_SKIP` 解决的是信号驱动审计的
  噪声；本仓审计是请求级中间件模型（一条 DELETE 请求一条 OperationLog），ORM
  级联不产生审计行，**噪声前提不成立**，P2-3 仅落地 M2M diff 纳入
  （`crud.py` 快照对比，与标量字段同形态落 `changes`）。

### 3. P2-5 审批流可视化评估：不引入（维持列表式编辑）

评估对象：上游 `ReFlowChart`（@logicflow/core 2.2.5 + extension 2.3.1）、
`@vue-flow`（core 1.48.2 + background 1.3.2），替代 `FlowConfigDrawer` 列表式
节点编辑。结论：**不引入**，理由：

1. **引擎形态不匹配**：ADR-012 一期流程模型是线性节点序列（order 顺序推进，
   节点条件在发起时匹配流程，无并行分支/网关/回退边）。画布的图模型（节点+边）
   映射线性列表是过度设计，画布只剩装饰价值；
2. **维护性已改善**：FlowConfigDrawer 已拆分（主组件 141 行 + 字段/节点编辑器
   子组件 + `flowConfig.ts` 纯逻辑），≤400 行红线达标；
3. **重依赖成本**：两库 gzip 均为百 KB 级，命中「重依赖不引入」红线。

**复评触发条件**（登记，满足其一再立项）：审批引擎升级支持并行分支/条件路由
图模型；或节点数常态化超过一屏导致列表编辑可用性下降。届时优先评估
`@vue-flow`（Vue3 原生、节点自定义更轻；上游 ReFlowChart 基于 LogicFlow 的
Turbo 适配层较重）。

### 4. 后果

- 前端与上游保持「同代依赖、点状移植」关系，不做整体升级；
- 后端借鉴项均带测试与默认关闭的灰度开关（密码安全三项、
  `APPROVAL_APPROVER_PERMS`），按「先灰度观察再默认开启」推进；
- P2 未立项项（内置角色的菜单同步策略细化、审计存储后端抽象、任务健康度
  阈值调优）沿用本 ADR 的边界原则，另立任务时不再重复论证。
