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
