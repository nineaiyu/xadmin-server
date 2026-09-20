# ADR-002: demo 应用保留但默认关闭

- 状态：已接受（2026-09-04）
- 关联：半年规划 T1.6 / TD-20

## 背景

`demo` app（Book 示例）为上游框架自带（上游提交 46caa47），用于演示 BaseModelSet / RePlusPage
的低代码开发范式。历史上其迁移文件状态反复（一度被删除后又补齐，fc09875）。当前生产路径 `config.yml` 的 `XADMIN_APPS` 为空，demo
未启用；前端 `src/views/demo/book/` 页面仍保留。仓库使用者（含本人）曾对"演示模式是否被引入"产生困惑。

## 决策

1. **保留** demo app 代码（后端 + 前端页面），它是框架开发范式的活文档与新功能集成测试的天然沙盒（tests 中 BaseModelSet
   冒烟测试依赖 demo 模型，`tests/settings_test.py` 显式启用）；
2. **默认关闭**：`config_example.yml` 与文档明确 `XADMIN_APPS` 需显式声明才会加载（如 `XADMIN_APPS: [demo]`），生产环境禁止启用；
3. demo 的 migrations 必须随仓库提交（现状已满足），不允许依赖运行时 makemigrations。

## 后果

- 正面：保住框架示例与测试沙盒，消除"迁移文件要不要提交"的反复；
- 负面：仓库保留少量非生产代码——通过"默认关闭 + 文档说明"控制噪音；若未来上游 demo 与核心层耦合加深，可重新评估移除（移除需同步改
  tests 的 demo 依赖）。

## 复审记录（2026-09-19，默认值语义调整：开发兜底默认启用）

**变更**：`config_example.yml` 的 `XADMIN_APPS` 默认值由空改为 `[demo]`。

**理由**：二次开发友好化（[plans/archive/二次开发友好化改造方案-2026.09.md](../plans/archive/二次开发友好化改造方案-2026.09.md) P1-3）——
新用户 clone 后直接 `bash utils/dev_up.sh` 即有「活的」示例（Book 四件套 + 前端页面）可抄，
替代「先读文档再理解框架」。demo 无菜单种子，教程（[guide/first-module-30min.md](../guide/first-module-30min.md)）
以「为 demo 添加菜单 → 页面可达」作为第一个实践步骤。

**边界不变**：

1. `config_example.yml` 仅在「未创建 config.yml」时被用作兜底（见 `server/conf/manager.py`）；
2. 生产路径不经过该默认值——生产镜像写入空 `config.yml`、installer 以环境变量注入、显式配置用户以其
   `config.yml` 为准，决策第 2 条「生产环境禁止启用」依然成立；
3. 关闭方式：`XADMIN_APPS:`（置空）或移除该行。

## 复审记录（2026-09-20，状态收口：升级为官方示例）

**变更**：demo 由「仅参照样板」**升级为官方示例**（与框架同步演进），补齐三条进阶链路的接入姿势：

1. **上架审批（审批流引擎）**：`Book.status` + `Book.instance`，提交经
   `create_instance(biz_type="demo_book")` 挂载流程，终态由 `approval_instance_finished`
   信号回写（通过 → 已上架并启用并落 `on_shelf_time`、驳回 → 已驳回）；见 `demo/services.py`
   与 `tests/integration/demo/test_book_flow.py`；
2. **二次确认（敏感操作审批）**：删除 / 批量删除挂载 `ApprovalRequired`，
   `seed_demo_book` 把 demo 路径写入 `APPROVAL_REQUIRED_PATHS`（批准后重发由前端自动携令牌）；
3. **回收站与变更历史**：Book 继承 `SoftDeleteModel`（MRO 首位）+ 混入 `RecycleBinAction`——
   删除进回收站、可恢复 / 物理清除（物理清除才走文件清理链）；前端 `recycleBin` 抽屉与
   行级「变更历史」（操作日志字段级 diff）按权限点显隐；
4. **菜单 / 权限点 / 示例流程**：`seed_demo_book` 命令灌入（并入 `seed_demo_all` 编排）；
   demo 路由不参与 loadjson 权限点扫描，新增 action 需同步 `PERMISSION_PLAN`
   （守护测试 `tests/unit/system/test_seed_demo_book.py`）；
5. **示例数据与定时任务（开箱即演示）**：`seed_demo_book` 幂等灌入 3 条示例书籍
   （库里无用户时自动跳过；软删过的自动复活）与默认停用的周期任务「示例-自动下架书籍」
   （`demo/tasks.py`，任务管理页可启停 / 立即运行）；--clean-only 一并移除。

**原决策条款修订**：

- 第 3 条（migrations 必须随仓库提交）**恢复生效**：`demo/migrations/0001_initial.py` 为
  **合并后的单文件**（含全部字段），建表回到标准迁移路径（不再依赖运行时 makemigrations /
  run-syncdb）；全新库直接 `migrate demo`；跑过旧版拆分迁移的库按 demo/README 的重置指引处理；
- 第 2 条「生产环境禁止启用」与默认值语义（开发兜底 `[demo]`）保持不变。

**说明**：`file` 字段放宽为可选（模型层 `null=True` 与接口层 `required=False` 同口径）；
`block` 为可写开关字段（`input_type="boolean"` 的渲染与提交示意）；视图集排序门禁仍豁免 demo。
