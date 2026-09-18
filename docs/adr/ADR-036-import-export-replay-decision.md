# ADR-036：导入导出执行链（WSGIRequest 重放）——保留现状，登记重构方向

- 状态：已接受（决策：**本轮不重构**；以装配点注释 + 契约测试管理风险；登记重开条件与重构方向）
- 日期：2026-09-14
- 关联：代码审查报告 2026-09-14（A4）；
  `common/tasks.py::background_task_view_set_job`、`system/tasks.py::async_import_data_task`、
  `common/core/modelset/import_export.py`

## 背景

异步导入 / 异步导出 / 批量删除 / 表头读取四类入口在 celery 内**手工重建 WSGIRequest**
（`system/tasks.py` 还额外装配 DRF `Request` 与 `view.request / action / kwargs / format_kwarg`），
再调用 ViewSet 的 action 函数重放同步链路。

审查报告指出的风险成立：绕过 middleware 链、对 DRF 内部字段依赖强、框架升级易碎（A4）。

事实补充（现存实现是「有意为之」的部分）：

1. 该链路的目标是**与同步路径 100% 同源**：serializer 校验、字段权限、creator 赋值、进度上报、
   失败行报告、审计 request_uuid 全部复用同一套 action 逻辑——在当前架构下「另写一套 service」
   就是第二实现，漂移风险更高；
2. 隐式契约至少 5 处，且有历史踩坑记录：`set_current_request`（creator/审计）、
   `view.request.user`（权限与字段权限）、`view.action`（serializer 行为分支）、
   `view.format_kwarg`（`get_serializer_context` 依赖）、`wsgi.input` / `CONTENT_LENGTH`（DRF Request 解析）。

## 决策：不重构（本轮）

| 维度 | 评估 |
|---|---|
| 回归面 | 四类入口共用该装配；重构 = 将 action 核心逻辑提取为 service + 显式上下文对象，涉及导入导出与批量删除全链，工作量 ≥ 2 个窗口 |
| 收益时点 | 收益是「对 DRF 大版本升级的韧性」；当前 DRF 3.x 在支持周期内，无迫近的升级计划 |
| 风险 | 提取过程需在 service 层重建上述 5 个隐式契约，任一遗漏即**静默降级**（creator 丢失 / 审计缺失 / 字段权限失效），可能比现状更危险 |
| 测试保障 | 现有集成测试覆盖结果语义（导入成功/失败报告/导出内容/批量删除），但**不覆盖装配细节**——重构前必须先补装配契约测试 |

## 加固（本轮完成）

1. `system/tasks.py` 装配点补齐「5 个隐式契约」注释清单，避免后续误删或改动失配；
2. 行为锁继续有效：导入导出集成用例 + 报表派发契约（`run_scheduled_report` 的 task_id == ExportRecord.pk）
   覆盖重放链的行为语义；
3. `background_task_view_set_job` 的入参（view 路径 / action_map / 分片 meta）保持显式，
   不在装配层做隐式兜底。

## 重构方向（重开时直接落地）

- 目标形态：`service 函数 + 显式上下文`（提交者、字段权限、menu 上下文、审计上下文、进度回调）；
- 步骤：
  1. 先补装配契约测试（断言 5 个隐式契约在任务内均可观测）；
  2. 按 action 逐个提取 `*_service`，同步路径改为委托同一 service；
  3. 最后删除 WSGIRequest 重放；
- 重开条件：DRF 大版本升级评估启动 / 需要第三种执行通道（独立 worker 服务）/
  导入导出接入非 ViewSet 数据源。

## 后果

- 现状保留，风险以「注释 + 契约测试」管理，避免无准备的大重构；
- 本 ADR 作为重开时的起点（含步骤与契约清单）。

## 增量（2026-09-18）：重构落地（契约测试 + 去 WSGIRequest 重放）

维护者决策提前实施（长期优化方案 §4.3 导入/导出行 P2 评估出口「两步都做」）。

### 1. 步骤 1 —— 装配契约测试（先补后改）

`tests/unit/system/test_import_export_execution_context.py`（7 例，spy 视图集子类化真实视图集、
行为不变只记录）在**真实执行链**内断言五个契约：异步导入（action/format_kwarg/kwargs/提交者身份/
thread-local 可见且出口清理 + creator 落库）、异步导出（action/format_kwarg/提交者/**查询参数确实
进入 filterset**——按 code 过滤两行数据只导出一行）、分片任务（`meta["user_pk"]` 携带身份、
thread-local 可见、通知对象 = 显式身份、creator 落库）+ 请求构造/绑定的单元形态断言。

### 2. 步骤 2 —— 显式请求上下文替换重放层

- 新增 `common/core/task_request.py`：`build_task_request`（显式 method/path/查询串/body/
  content-type/提交者，构造最小 `HttpRequest`，不再拼 WSGI environ）+
  `bind_view_task_context`（`view.request/action/kwargs/format_kwarg` 绑定，五契约集中一处）；
  提交者经 DRF `_force_auth_user`（ForcedAuthentication）直通，任务排队超过 access token 寿命
  也不会认证失败；每分片构造独立请求对象（字段权限关联 memo 按分片隔离的既有语义保持）；
- 三处 WSGIRequest 重放全部替换：`common/tasks.py::background_task_view_set_job`、
  `system/tasks/_import.py::run_async_import`、`system/tasks/_export.py`（`build_export_request` 改为
  工厂薄封装）；分片任务由 `meta["user_pk"]` 显式携带提交者（`run_view_by_celery_task` 写入），
  不再依赖 META 里复制的 cookie/令牌完成认证；
- **顺带修复**：分片任务汇总通知原先读重放请求的 `request.user`（裸 `WSGIRequest` 无该属性，
  生产路径在最后一个分片聚合时 AttributeError）；现改为显式身份，缺身份时告警跳过。

### 3. 未做（保留登记）

ADR「重构方向」中的 **service 化**（按 action 提取 `*_service`、同步路径委托同一 service）本轮
不做：重放层已消除，且 action 作为唯一实现避免第二实现漂移；service 化的剩余动机主要是
「第三种执行通道（独立 worker 服务）」与「接入非 ViewSet 数据源」，触发条件不变。

### 4. 验证

- pytest 全量（含本批新增 7 例契约测试）exit 0；ruff check/format 全绿；
- 行为锁继续有效：`test_import_record` / `test_export_record` / `test_import_template` /
  `test_tasks`（分片任务）/ `test_modelset_base` 全部保持通过（72 例）；
- E2E 导入导出相关 spec 复跑（async-import / async-export / import-export / import-mapping）。
