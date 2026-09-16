# 可观测性与 SLO（observability）

> 建立：2026-09-16（年度计划 A1 追踪 + A2 SLO 交付物）。
> 相关：[metrics.md](../metrics.md)（KPI 与基线）、[release-checklist.md](release-checklist.md)（发布观察）、
> [runbook.md](runbook.md)（故障处置）、`server/monitoring.py`（Sentry 初始化）、
> `common/metrics.py` + `common/celery/metrics.py`（指标定义与任务信号）。

## 一、三支柱现状

| 支柱 | 实现 | 启用方式 |
|------|------|----------|
| 日志 | 结构化（text / json 可选）+ 按天轮转归档（`server/settings/logging.py`） | 默认开启 |
| 指标 | Prometheus（HTTP 请求/耗时 + Celery 任务/耗时） | `METRICS_ENABLED=true` |
| 错误 | Sentry 聚合（DSN 配置后生效；`send_default_pii=False`） | `SENTRY_DSN` 非空 |

## 二、追踪（Tracing）

### 启用（config.yml 两项，重启生效）

```yaml
SENTRY_DSN: "https://<key>@<host>/<project>"
SENTRY_TRACES_SAMPLE_RATE: 0.1   # 0.0 = 仅错误上报（默认）；建议生产 0.05 ~ 0.1 采样
```

- DSN 为空 = **完全零开销**（不初始化 SDK，不产生任何运行时负担）；
- 隐私口径：`send_default_pii=False`（事件仅携带 request_uuid，不上报用户 PII）；
- 错误事件不受采样率影响（`traces_sample_rate` 只控性能数据采集）。

### 覆盖范围（自动，无需业务埋点）

| 集成 | 产生 |
|------|------|
| DjangoIntegration | 每个 HTTP 请求 transaction（含 DB/缓存 span 与耗时瀑布） |
| CeleryIntegration | 每个 celery 任务 transaction（含子任务链路） |

核心链路（登录 / 列表 / 导出 / 审批 / NL 查数 / 报表任务）在启用后**自动可见**；
如需业务级细分（如「报表内聚合 vs 渲染」）再按需 `sentry_sdk.start_span`——
未初始化时 API 为 no-op，可安全埋点。

### OTel 评估（结论：暂不引入）

| 维度 | 结论 |
|------|------|
| 需求匹配 | 单服务形态 + 无外部 collector / 多语言链路；sentry performance 已覆盖请求/任务/DB/缓存追踪 |
| 引入成本 | `opentelemetry-sdk` + exporter 新增依赖与配置面，与 Sentry 能力重叠 |
| 重开条件 | 出现多服务链路追踪需求（如拆分服务），或需接入组织级 OTel 后端时，按重依赖红线走 ADR 评估 |

## 三、指标与 SLO

### 指标清单

| 指标 | 类型 | 标签 |
|------|------|------|
| `xadmin_http_requests_total` | Counter | method, view, status |
| `xadmin_http_request_duration_seconds` | Histogram | method, view |
| `xadmin_celery_tasks_total` | Counter | task, status（SUCCESS / FAILURE / REVOKED …） |
| `xadmin_celery_task_duration_seconds` | Histogram | task |

### SLO（初始口径，按实际基线校准并回填 metrics.md）

| SLO | 计算 | 目标 | 告警阈值（建议） |
|-----|------|------|------------------|
| HTTP 可用性 | 1 - 5xx 率（按 `status` 标签聚合） | ≥ 99.5%（月） | 5xx 率 > 1% 持续 5 分钟 |
| API P95 延迟 | `histogram_quantile(0.95, ...)` | < 500 ms（核心读接口） | P95 > 1 s 持续 10 分钟 |
| 任务成功率 | SUCCESS / total（`xadmin_celery_tasks_total`） | ≥ 99%（日） | 连续 5 个任务失败（`failure_handler` 已接告警通道） |
| 队列积压 | broker 深度（flower / 健康检查口径） | < 100（常态） | > 500 持续 10 分钟（含 beat 停摆排查项） |

### 告警分级（以「可行动」为准，先收敛后扩充）

- **P1（立即处置）**：可用性、队列积压、WAL 归档失败（既有 `check_wal_archive`）、备份失败告警；
- **P2（当班处置）**：API 延迟、任务失败率；
- **P3（周检处置）**：容量趋势（监控面板 + audit 周检）。

## 四、故障演练（A4）

演练清单与记录见 §六（首轮执行后回填）。

## 五、验证与操作

- **指标验证**：`METRICS_ENABLED=true` 时访问指标端点返回 `text/plain` 指标文本；缺失依赖时 503 不阻断服务；
- **追踪验证**：配置 DSN + 采样率并重启后，访问核心页面，于 Sentry 后台查看 transaction / waterfall；
- **关闭**：DSN 置空重启（追踪与错误上报一并停止，零开销）；采样率置 0 则保留错误上报、停性能数据。

## 六、演练记录（逐次追加）

（A4 窗口首轮执行后回填）
