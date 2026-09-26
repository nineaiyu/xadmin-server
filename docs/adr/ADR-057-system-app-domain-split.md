# ADR-057：system 巨型 app 按域拆分（approval / ai / dataset）

- 日期：2026-09-26
- 状态：**已交付**（5 批次逐批 commit；每批次全量 pytest + doctor 9/9 + 权限点 dry-run 缺口 0 + 真实库迁移验证）
- 背景：system app 膨胀至 5.2 万行，审批流 / AI 平台与知识库 / 数据分析与动态表单三类业务与 RBAC 内核混杂（优化计划 R1/R2/3.1）。前置条件（1.1 依赖方向治理 + 3.2 权限点元数据化 + 契约缝台账 28 条）就绪后，按「由独立到耦合」5 批次执行：
  1. **批次 0**：跨域无状态工具下沉 common（`ai_parse`——LLM 输出解析为 AI 域与数据分析域共同消费的唯一实现）；
  2. **批次 2**：`approval` app（审批流引擎 + 操作审批 + 请假演示 + 13 模型）；
  3. **批次 3**：`ai` app（AI 平台 / 知识库 / MCP / NL 查数入口 + 5 模型）；
  4. **批次 4**：`dataset` app（数据分析与动态表单 + 6 模型；工作台 dashboard 视图属 RBAC 内核，留 system）；
  5. **批次 5**：契约层重组 + 文档同步 + 收尾验证。

## 决策

### D1 四道迁移硬约束（每批次验收）

1. **表名不变**：迁出模型显式 `Meta.db_table = "system_<原表名>"`，PG 存量表零 DDL；
2. **权限点路径不变**：新 app 路由以 `path("", include("<app>.urls"))` 从 `system/urls.py` 同前缀挂载（`/api/system/...`），不设 app_name——Menu.path 权限点、前端路由、ModuleSpec 路由正则、`system:` 命名空间视图名全部不变，前端零改动；
3. **以 app_label 为键的持久化状态平移**：数据迁移（system.0014/0016/0019）原地改写 `django_content_type.app_label`（auth_permission 绑定随之保留、无 stale CT）、`ModelLabelField` 模型节点 name、`DataPermission.rules` 内 `table` 目标（JSON 递归重写）；`loadjson/` 种子 fixture 的 model 键与标签串同步；
4. **运行期注册各 app 自持**：周期任务迁 `<app>/tasks.py` + `apps.ready()` 显式 import（celery 启动对账自动重建 beat 条目，实测旧任务名自动清理）；WS 通道迁 `<app>/routing.py`（1.2 自动收集，零改 asgi）；审批落库 handler 注册随域迁 `dataset.apps.ready()`。

### D2 迁移结构

新 app 0001 与 system 侧删除迁移全部用 `SeparateDatabaseAndState` 包成**纯状态操作**（无 DDL）；依赖链 `新app.0001 → system.状态删除 → system.数据迁移`；全新库 scratch 重放与存量库升级同链路验证。历史迁移（demo/0001 等）中的跨模型 FK 串同步改指新 app_label（对已应用库无影响，仅修全新库重放）。

### D3 契约门面（拆分后的跨 app 消费面）

- `approval.services`（process_approval）、`ai.services`（API_ACTION_SPECS/api_action_specs）、`dataset.services`（数据集执行面 + 表单校验面 + 模型惰性导出）自持契约门面；common 的契约缝在 `CONTRACT_SEAMS` 改挂新 app（28 条台账）；`system.services` 收敛为 RBAC 内核契约并补 `DisplayRelatedField` / `TaggedObjectSerializerMixin` 惰性导出；
- 业务 app 之间保留函数级惰性 import 逃生门（准则 §1.5.1 同口径）。

### D4 拆分中发现的存量缺陷（顺带修复）

`check_cross_app_imports.py` 横向扫描的 `src_app` 取自绝对路径 `parts[0]`（恒为 `/`），**该扫描自引入起从未命中任何违例**（空转）。修复为取相对路径首段后，暴露并收口 11 条真实存量跨 app 模块级 import（ai/serializers、nl_query、assistant 对 settings 视图的消费、种子命令等）；种子命令按 `load_init_json` 同口径入 ALLOWLIST（现 5 项）。

## 后果

- system 由 5.2 万行收敛至约 3.0 万行（RBAC 内核：用户/角色/菜单/部门/字典/权限/审计/任务中心/导入导出/凭据/安全域）；
- 新业务 app 的接入形态（URL 同前缀挂载 + services 门面 + TASK_ROUTES/routing 自持）成为二开范式，`generate_crud --bootstrap` 教程链路不受影响；
- 每批次一个 commit，牵动四类持久化状态的改动永不与其它变更混批。

## 事故记录（批次 4）

容器内一次 migrate 因 Docker Desktop 文件同步滞后读到改名前的旧版自动迁移（真实 DeleteModel DDL），drop 并重建 6 张 dataset 域表——表中均为演示种子数据，已通过「清理 post_migrate 重建的重复 content_type + 状态 fake 对齐 + 0019 数据迁移 + load_init_json 重灌」完整恢复并逐表验证。教训：**容器内迁移操作前先 ls 校验文件同步状态**；涉及状态删除的迁移一律先包 SeparateDatabaseAndState 再提交。
