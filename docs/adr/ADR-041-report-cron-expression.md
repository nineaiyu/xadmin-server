# ADR-041：定时报表 cron 表达式（自定义调度）

| 项目 | 内容 |
|------|------|
| 状态 | 已交付 2026-09-15 |
| 日期 | 2026-09-15 |
| 关系 | 扩展 [ADR-021](ADR-021-dashboard-display-and-reports.md)（报表轻量版：一期 daily/weekly/monthly 三档）；候选池「报表 cron 表达式（引入 croniter 前置评审）」销项 |
| 依赖 | 新增 `croniter==6.0.0`（纯 Python、无传递依赖，见 §依赖评审） |

## 背景

报表一期只支持三档固定频次 + `send_time`（HH:MM）+ `weekday`，由**每小时**分发任务
（`crontab="5 * * * *"`）扫描判定。候选池长期登记「自定义调度（cron 表达式）」，前置为
「引入 croniter 评审」。

## 依赖评审（croniter）

- 纯 Python 实现、无 C 扩展、无额外传递依赖（约 60KB），MIT 许可；
- 提供 `is_valid` / `match(expr, dt)`（分钟级命中判定），无需自研 cron 解析；
- 与 celery beat 的 crontab 语法一致（五段），用户心智可迁移；
- 结论：**引入**，版本 pin `croniter==6.0.0`，随 `requirements.txt` 走既有依赖窗口审计。

## 决策

1. **模型**：`Report.cron_expression`（CharField 64，空 = 使用三档频次）。
   **优先语义**：非空时完全覆盖 `frequency/send_time/weekday`（避免两种调度叠加的歧义）。
2. **判定**：`report_due()` 优先走 `_cron_due()` —— `croniter.match(expr, now)` 判定「当前分钟」；
   非法表达式 fail-closed（不命中并留痕，不抛错中断扫描）。
3. **调度粒度**：新增**每分钟**分发任务 `dispatch_cron_reports`（`crontab="* * * * *"`），
   **只处理 `cron_expression` 非空的报表**；原每小时任务改为只处理三档报表
   （`cron_expression=""`）——两者职责互斥，存量行为零变化、无重复派发。
4. **校验**：序列化器对 `cron_expression` 做 `croniter.is_valid` 校验（非空时），
   前端表单同为提示（留空 = 用固定频次）。

## 影响与兼容

- 新字段默认空 → 存量报表行为逐字不变；
- 每分钟任务只查「有 cron 配置的报表」（通常个位数），负载可忽略；
- 派发契约不变（`run_scheduled_report.apply_async` + 预创建 ExportRecord.pk 契约）。

## 测试

`tests/integration/system/test_report_cron.py`：表达式校验（合法/非法拒绝）、
`report_due` 的 cron 命中/未命中/非法 fail-closed、三档与 cron 互斥优先级、
分发任务过滤（cron 报表不被每小时任务处理）。

## 边界（不做）

- 秒级精度（六段表达式）不做——派发粒度为分钟；
- cron 时区按服务端本地时区（`timezone.localtime()`），不做 per-report 时区；
- 存量三档不做迁移（用户按需自行改写为 cron）。
