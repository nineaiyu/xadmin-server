# 可观测性与 SLO（observability）

> 建立：2026-09-16（年度计划 A1 追踪 + A2 SLO 交付物）。
> 相关：[metrics.md](../metrics.md)（KPI 与基线）、[release-checklist.md](release-checklist.md)（发布观察）、
> [runbook.md](runbook.md)（故障处置）、`server/monitoring.py`（Sentry 初始化）、
> `packages/xadmin-common/common/metrics.py` + `packages/xadmin-common/common/celery/metrics.py`（指标定义与任务信号）。

> §六 演练记录（逐次追加）已拆分至 [observability-drills.md](observability-drills.md)。

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

### OTel 评估（结论：默认不做全量常开，条件做）

| 维度 | 结论 |
|------|------|
| 需求匹配 | 单服务形态 + 无外部 collector / 多语言链路；sentry performance 已覆盖请求/任务/DB/缓存追踪 |
| 引入成本 | `opentelemetry-sdk` + exporter 新增依赖与配置面，与 Sentry 能力重叠 |
| 重开条件 | 出现多服务链路追踪需求（如拆分服务），或需接入组织级 OTel 后端时，按重依赖红线走 ADR 评估 |

> 完整评估（采集面 / 开销与传播 / 落地形态 / trace_id 注入改动面 / 最小埋点集与开关 / 回退 / 触发条件）见 [§九](#九跨层追踪opentelemetry评估2026-10-09)。

## 三、指标与 SLO

### 指标清单

| 指标 | 类型 | 标签 |
|------|------|------|
| `xadmin_http_requests_total` | Counter | method, view, status |
| `xadmin_http_request_duration_seconds` | Histogram | method, view |
| `xadmin_celery_tasks_total` | Counter | task, status（SUCCESS / FAILURE / REVOKED …） |
| `xadmin_celery_task_duration_seconds` | Histogram | task |
| `xadmin_celery_queue_length` | Gauge | queue（celery / heavy；broker 直读 LLEN，2026-09-29 起进端点——队列积压 SLO 的数据源） |
| `xadmin_authz_grants_cache_keys` | Gauge | 无（授权池缓存存活键数，SCAN 计数；TTL 300s 兜底，见 cache-keys-audit.md 观察项） |
| `xadmin_cache_requests_total` | Counter | cache（缓存名，取 `{View}_{method}` / 被装饰函数名，非缓存键）, result（hit / miss）；命中率 PromQL 见 [cache.md](../architecture/cache.md) |

> **消费端**：[monitoring-stack.md](monitoring-stack.md) 提供 Prometheus + Grafana + blackbox 参考编排、
> SLO 告警规则与「告警 → 站内信/邮件/Webhook」桥接脚本（宿主侧 systemd 单元样例同处）。

### 系统监控面板（SystemMonitor，2026-09-19 增强）

页面：系统管理 → 系统监控（`/system/monitor/index`）。数据源全部复用既有基建
（`common.Monitor` 心跳表 + psutil 快照 + 健康探测），不引入外部组件。

| 能力 | 接口 | 说明 |
|------|------|------|
| 实时指标 | `GET api/system/monitor/overview` | CPU/内存/磁盘/负载 + 网卡速率（累计计数器差分，重启归零记缺失）+ 健康总览（分项状态 / 健康分 / 未恢复告警数） |
| 服务健康 | `.../services`、`.../redis-info`、`.../celery` | DB/Redis/Celery 探测（与 healthz 同源）、Redis INFO、worker/队列 |
| 历史趋势 | `.../history` | 时间范围 1h/6h/24h/7d/30d（或 start/end）、聚合粒度 auto/1m/5m/15m/1h/1d、多指标叠加（百分比与数值分双轴）、环比上一等长窗口；查询参数多，**不套 10s 短缓存**（避免不同窗口互相污染） |
| 告警阈值 | `GET/PUT .../thresholds` | 读写 `SECURITY_MONITOR_*`（Setting 表 `category=security_monitor`，与系统设置 → 安全设置同源）；PUT 后同步本进程 settings，其余进程由 pubsub 回写 |
| 告警记录 | `.../events?kind=alert` | `MonitorAlert` 状态跃迁流水：超标建 firing、持续仅续写 count/last_time、回落置 resolved（60s 检查周期不刷重复流水） |
| 事件查询 | `.../events?kind=error\|task` | 异常请求（业务码非 1000）/ 任务失败（FAILURE/REVOKED）明细 |
| 报表导出 | `.../export` | `kind=history\|alerts` × `type=csv\|xlsx`（趋势导出含「趋势数据 + 汇总/环比」双 sheet，CSV 带 BOM） |
| 分享视图 | 前端复制链接 | 趋势筛选（range/interval/metrics）写回地址栏，链接打开即复现同一视图 |

- 权限点（挂 SystemMonitor 菜单）：`history` / `thresholds`(GET) / `updateThresholds`(PUT) /
  `events` / `export`；升级后需重灌 `load_init_json`（或 `python manage.py sync_menu_permissions`）再重启，
  否则非超管角色看不到/调不通新功能（PUT 端点不进 `sync_menu_permissions` 的自动生成面，权限点在种子中维护）；
- 心跳落盘周期 30s（2026-09-19 修复启动线程双 sleep 导致的实际 60s；告警检查的「最近 3 次心跳均值」窗口随之收紧）；
- 已恢复的告警记录随 `MONITOR_RETENTION_DAYS` 一并清理，未恢复记录保留至指标回落。

### SLO 数据源与校准（2029-12 窗口）

| SLO | 数据源 | 就绪性（2026-09-16）|
|-----|--------|--------------------|
| HTTP 可用性 | `xadmin_http_requests_total{status}`（web 进程） | ✅ 端点启用 |
| API P95 延迟 | `xadmin_http_request_duration_seconds`（web 进程） | ✅ 端点启用 |
| 任务成功率 | `xadmin_celery_tasks_total{task,status}` —— **跨进程聚合** | ✅ 本窗口补齐：worker 写 redis（`xadmin:metrics:celery_tasks`），端点在渲染时附加（进程内计数器不导出，避免口径重复）；实测 worker 容器 → server 端点跨进程链路 ✓ |
| 队列积压 | `xadmin_celery_queue_length`（broker 直读 LLEN，celery / heavy 两队列） | ✅ 端点启用（2026-09-29） |

**说明**：任务耗时直方图（`xadmin_celery_task_duration_seconds`）已按同一模式**跨进程聚合**
（2026-09-16 交付：worker 写 redis 累积桶 + sum/count，端点渲染完整 histogram——**零值桶输出**，
`histogram_quantile` 可直接算任务 P95；SLO 暂不依赖，作为诊断指标使用）。
**校准方法**：观察 ≥3 个月后按实际数据校准目标值与告警阈值（现维持下方初始口径）；初期形态
（2026-09-16）：周期任务全 SUCCESS、HTTP 指标待流量积累。

**采集机制（A2，2026-09-17 上线）**：`ops/slo_snapshot_cron.sh` 每日（宿主 cron / systemd timer）
调用 `scripts/slo_snapshot.py --append`，把快照追加进 JSONL（`SLO_SNAPSHOT_FILE`，默认
`tmp/slo_snapshots.jsonl`）——HTTP/任务为进程累计口径，**跨重启的趋势**才有意义。
接线有端到端测试守护（`tests/unit/common/test_slo_snapshot.py::TestCronScriptWiring`：
stub 指标端点验证令牌透传与 JSONL 追加）。

- **首次采集（2026-09-17）**：可用性 100.000%（23 请求）、P95 0.05s、任务成功率 99.89%（2831 任务）；
- **第二次（同日，经 cron 脚本真实链路）**：可用性 100.000%（461 请求）、P95 0.01s、任务成功率 99.90%（3010 任务）；
- **宿主调度已安装（2026-09-18）**：macOS 下 `crontab` 写入被 TCC 拦截（`Operation not permitted`），
  改用 LaunchAgent：`~/Library/LaunchAgents/com.xadmin.slo-snapshot.plist`（label `com.xadmin.slo-snapshot`，
  每日 06:17；launchd 会在机器唤醒后补跑错过的时点；token 以环境变量内联于 plist，权限 600）。
  运维命令：

```bash
launchctl list | grep xadmin                                    # 任务状态（第二列 = 最近退出码，0 为正常）
launchctl kickstart -k gui/$(id -u)/com.xadmin.slo-snapshot     # 立即手动触发一次（验证链路）
tail -5 <仓库>/tmp/slo_cron.log                                 # 执行日志
```

**校准触发**：采集跨度 ≥3 个月（约 ≥90 个数据点，2026-12 起季度巡检核对）→ 按实际数据
回填目标值到下方表格与 [metrics.md](../metrics.md)。

### SLO（初始口径，按实际基线校准并回填 metrics.md）

| SLO | 计算 | 目标 | 告警阈值（建议） |
|-----|------|------|------------------|
| HTTP 可用性 | 1 - 5xx 率（按 `status` 标签聚合） | ≥ 99.5%（月） | 5xx 率 > 1% 持续 5 分钟 |
| API P95 延迟 | `histogram_quantile(0.95, ...)` | < 500 ms（核心读接口） | P95 > 1 s 持续 10 分钟 |
| 任务成功率 | SUCCESS / total（`xadmin_celery_tasks_total`） | ≥ 99%（日） | 连续 5 个任务失败（`failure_handler` 已接告警通道） |
| 队列积压 | broker 深度（flower / 健康检查口径） | < 100（常态） | > 500 持续 10 分钟（含 beat 停摆排查项） |

### 告警分级（以「可行动」为准，先收敛后扩充）

- **P1（立即处置）**：可用性、队列积压、WAL 归档失败（既有 `check_wal_archive`）、备份失败告警、容器 OOM；
- **P2（当班处置）**：API 延迟、任务失败率；
- **P3（周检处置）**：容量趋势（监控面板 + audit 周检）。

### 告警覆盖清单（A1，滚动维护）

| 场景 | 触发信号 | 投递链路 | 状态 |
|------|----------|----------|------|
| 备份失败（DB / 媒体 / 异地同步） | `db_backup.sh` 失败点 | `/api/common/api/backup-alert` → 站内信 + 邮件 + Webhook `system.backup_failure` | ✅ 已接 |
| WAL 归档失败 / 积压 | `check_wal_archive` 双信号巡检 | 备份脚本日志（调度侧可感知） | ✅ 已接 |
| Celery 任务失败 | `failure_handler` 信号 | 站内信 + 邮件 | ✅ 已接 |
| 敏感操作 | 操作日志中间件 | 站内信 + 邮件 | ✅ 已接 |
| Webhook 投递耗尽 | 投递任务 | 站内信 | ✅ 已接 |
| API 应用配额软告警 | 开放平台 | 站内信 | ✅ 已接 |
| 主机资源阈值（CPU / 内存 / 磁盘） | 主机监控心跳 + 周期检查 | 站内信 / 邮件（`ServerPerformanceMessage`）；2026-09-19 起同时落 `MonitorAlert` 流水（监控页可查/可导出） | ✅ 已接 |
| **容器 OOM** | `docker events` 的 `oom` 事件 | `ops/oom_alert.sh` → `/api/common/api/ops-alert` → 站内信 + 邮件 + Webhook `system.ops_alert` | ✅ 新增（A1，第十六轮验证） |
| HTTP 可用性 / P95 延迟 / 任务成功率 / 队列积压 | Prometheus 指标 + SLO 阈值（`ops/monitoring/alerts.yml`） | 参考栈抓取判定 → 宿主侧 `scripts/prometheus_alert_bridge.py` → `ops-alert`（站内信 + 邮件 + Webhook `system.ops_alert`），与 OOM 告警同链路 | ✅ 已接（P1-36，见 [monitoring-stack.md](monitoring-stack.md)） |
| 端点级 P95（登录/路由/列表/元数据列·字段/导出/导入） | `xadmin_http_request_duration_seconds{view}` 的 `histogram_quantile`，阈值 = 基线 P95 × 3（`XadminCaseLatency*`） | 同上（Prometheus 判定 → 告警桥接 → `ops-alert`） | ✅ 已接（阈值口径见 [monitoring-stack.md](monitoring-stack.md) §三） |

维护约定：新增告警必须经演练验证（本清单同步登记证据）；仅接已证实场景，避免告警噪音。

### 宿主侧 watcher（容器 OOM）

`ops/oom_alert.sh` 在 **Docker 宿主机** 常驻运行（需 docker socket 与服务端 HTTP 可达）：

```bash
OPS_ALERT_URL=https://<xadmin-host>/api/common/api/ops-alert \
OPS_ALERT_TOKEN=<与系统设置 OPS_ALERT_TOKEN 一致> \
bash ops/oom_alert.sh
```

- 监听 `docker events --filter event=oom`；断线自动重连，游标（事件时间纳秒）落
  `OOM_ALERT_STATE`（默认 `/tmp/xadmin-oom-alert.cursor`）——重连按秒级游标回放、
  按纳秒去重，不重放已投递事件、不静默漏事件；
- `OOM_ALERT_ONCE=1` 投递首个事件后退出（验证 / 单次场景）；`OOM_ALERT_CONTAINER=<name>`
  只监听指定容器；
- 服务端侧 60s 节流（同来源同事件，OOM 风暴合并为一条）；watcher 未配置
  URL/TOKEN 时仅记日志不投递，投递失败只记 WARN、不阻塞监听；
- 建议以 systemd unit 或具备 docker socket 的运维容器常驻（重启自愈）。

## 四、故障演练（A4）

演练清单与记录见 §六（首轮执行后回填）。

## 五、验证与操作

- **指标验证**：`METRICS_ENABLED=true` 时访问指标端点返回 `text/plain` 指标文本；缺失依赖时 503 不阻断服务；
- **追踪验证**：配置 DSN + 采样率并重启后，访问核心页面，于 Sentry 后台查看 transaction / waterfall；
- **关闭**：DSN 置空重启（追踪与错误上报一并停止，零开销）；采样率置 0 则保留错误上报、停性能数据。


## 七、运营基线快照（2029-10 窗口）

**指标端点启用（2026-09-16）**：`METRICS_ENABLED=true` + `METRICS_TOKEN`（config.yml，Bearer 保护，
未启用时 404）——启用过程修复一个**死开关**：`METRICS_ENABLED/TOKEN` 此前只用于条件挂中间件、
**未导出到 django settings**（读方 `getattr(settings, ...)` 永远落 False → 端点永远 404）；
已无条件导出 + 守护测试（与 `SECURITY_AES_V1_DECRYPT_ENABLED` 同类缺陷的**第 2 次踩中**，
转发对账纪律继续适用）。

**快照（2026-09-16，自当次重启起）**：

| 项 | 值 | 备注 |
|----|----|------|
| 指标端点 | ✅ `/api/common/api/metrics`（Bearer）| 5 指标族：http_requests/duration + celery_tasks/task_duration + authz_grants_cache_keys |
| HTTP 请求形态 | health 7 次全 200 | 打点验证；正式基线待运行累积 |
| 队列积压 | 0（redis `llen celery`）| 即时 |
| 今日 WARN | 102,447 → **已降噪**（96% = 缓存失效日志）| MagicCache/MagicCacheResponse 4 处 warning→debug |
| 今日 ERROR | 415 | 含 404/权限类请求噪声 |
| `unexpected_exception` | 累计 9,175 行（未按月切分）| 观察项 |
| SLO 校准 | 按计划 2029-12（需运行期数据积累）| 本次登记端点启用与快照方法 |

**发现与处置**：① 缓存失效日志高频 WARN → 降 debug；② decrypt（v1 观察）WARN 切换后归零；
③ **Redis 冻结韧性缺陷五轮修复**（§六）；④ metrics 死开关（修复 + 守护测试）。

## 八、观察登记（滚动）

| 日期 | 项 | 证据与现状 | 处置 |
|------|----|-----------|------|
| 2026-09-27 | **安全设置页「路由匹配但组件为空」空白**（AES v1 开启/关闭无关；A6 浏览器验收过程发现） | 浏览器访问 `/#/settings/security/index`：面包屑与侧栏高亮正常，但 `#main-content` 仅剩 `<!---->`（Vue 条件渲染空态）、**无 DEV「动态路由组件未匹配」报错**（该分支会在 dev 显式 console.error）、无任何 settings 请求发出；同批的短信设置 / 基本设置页正常可访问。已核对：库内菜单 `component=settings/security/index` 正确、`src/views/settings/security/index.vue` 存在。初步判断为 `LayFrame` 侧 `Comp` 为空（路由节点 component 未落到 `router-view`），非接口/权限故障 | 待深挖（优先核对 `resolveComponentKey` 对 `settings/security/*` 的命中与多层 tab 页的 keep-alive 传递；复现：临时超管登录后直接 goto 该路径）。**观察项，不阻塞发布**（异常时回滚路径不受影响） |

## 九、跨层追踪（OpenTelemetry）评估（2026-10-09）

> 结论：**条件做**——默认不做全量常开追踪；若触发，只落地「最小埋点集 + 开关（默认关）+ runbook + 回退」。
> 本轮仅评估，不实施（不新增依赖、不改代码）。

### 9.1 现状差距

现有三支柱（§一）在「单请求 / 单任务」内的排障能力已足够，缺口集中在**跨层关联**：

- **日志 ↔ 指标 ↔ 追踪缺统一关联键**：日志按 `request_uuid` 串联（`server/logging.py` 的 `ServerFormatter` /
  `JsonFormatter`），指标只有 method/view/status 标签，两者无法按同一 id 互查；
- **跨进程链路无标准传播**：web（HTTP）→ celery（worker/heavy/beat）→ 出站（AI SDK / webhook / MCP）
  之间的调用关系只在 Sentry transaction 内可见，且依赖外部 DSN；标准 W3C `traceparent` 未生产/未透传；
- **WS 通道不可见**：channels consumer（`ws/chat/`、`ws/message/...`，见 `message/consumers.py`）不经
  Django 请求链路，无 span；
- **追踪能力绑在 Sentry 上**：`server/monitoring.py` 的 Django / Celery integration 已能在
  `SENTRY_TRACES_SAMPLE_RATE>0` 时产出请求/任务瀑布，但（a）需外部 DSN、（b）非 OTel 标准、
  （c）日志里没有 trace 关联。

即：**能力不缺「查看单条链路」，缺「把链路 id 打到日志与指标上、并在多进程 / 多语言间标准互操作」**。

### 9.2 采集面逐项评估

| 采集面 | 现状 | OTel 接入方式 | 收益 | 成本 / 复杂度 |
|--------|------|--------------|------|--------------|
| ASGI / HTTP | Sentry transaction；`request_uuid` 进日志 | `opentelemetry-instrumentation-asgi`（+`-django`） | 请求级 span + contextvars 传播 | 低（自动仪表，setup 期一行） |
| ORM / DB | Sentry DB span | `-psycopg`（DBAPI） | 慢查询 span 瀑布 | 低 |
| Redis / 缓存 | Sentry cache span | `-redis` | 缓存命中 / 耗时 span | 低；本项目缓存语义多（见 [cache-keys-audit.md](../cache-keys-audit.md)），标签需收敛 |
| Celery（四进程） | Sentry task transaction；`packages/xadmin-common/common/celery/metrics.py` 已挂 `task_prerun/postrun` | `-celery`（task header 传 traceparent） | web→worker→heavy 链路贯通 | 中：beat 只投递、worker/heavy 分别建 span；需确认线程池下 context 正确 |
| channels WS | 无 | 无官方仪表 → 手动 span（connect/receive/disconnect） | WS 上行 / 下行可归因 | 中高：消费方显式埋点，改动面在手写代码 |
| 外部调用 | `pinned_request`（`requests.Session`），无 trace 头 | `-requests` | AI / webhook / MCP 出站 span + 端到端 | 低；与出站白名单（`packages/xadmin-common/common/utils/outbound.py`）正交 |

依赖增量（`uv` 单源，见 `pyproject.toml`）：`opentelemetry-api` / `-sdk` / `-exporter-otlp-proto-http`
+ 上述 instrumentation 包（各数十 KB 级）。当前 `uv.lock` **无任何 opentelemetry 包**（新增面确认）。

### 9.3 采样率与开销

- **每请求开销量级**：span 创建 / 结束为微秒级，主要成本在导出（批量、后台线程）。采样开启后对 P95 的
  影响通常在**亚毫秒级**（远低于现有 `SLOW_REQUEST_THRESHOLD` 判据口径）；
- **contextvars 传播**：OTel 用自己的 context；ASGI 异步链 → `sync_to_async(thread_sensitive=True)` 会把
  当前 context 复制进同步视图线程，与既有 `packages/xadmin-common/common/local.py` 的 `Local()`（contextvars
  模式）同机制，**同线程可见性成立**；
- **Celery 跨进程**：靠 `-celery` 把 `traceparent` 注入 task header，worker 侧提取续链；beat 只入队不执行。
  broker 为 Redis，header 随消息走、不落库；
- **关闭态零开销**：沿用 Sentry「DSN 空即不 init」范式——`OTEL_ENABLED=false` 时不初始化 SDK，运行时纯 no-op。

### 9.4 数据落地形态对比

| 形态 | 说明 | 收益 | 成本 / 边界 |
|------|------|------|------------|
| A 本机 Tempo 容器 | OTLP → Tempo → 复用 Grafana 查询 | 全量本地留痕、可回溯 | +容器（内存 / 磁盘 / 保留策略）/ 备份 / 升级面；单人维护成本最高 |
| B 仅 OTLP 导出 | 外部后端（SaaS / 组织 collector）或本地文件 exporter | 无本地存储运维 | 外部后端 = 数据出域 + 供应商依赖；本地文件 = 无查询 UI，价值低 |
| C 仅排查窗口临时导出 | 默认 no-op，故障时开开关导出到临时 collector | 零常态运维、按需 | 依赖 runbook + 开关纪律；历史链路不可追溯 |

**取向**：默认走 **C**（与「默认不做全量常开」一致）；**不默认部署 A**（Tempo）。若长期触发（多服务互操作），
再评估 A / B（参考 [monitoring-stack.md](monitoring-stack.md) 的 Grafana 参考编排）。

### 9.5 trace_id 注入日志的改动面

现有基础已就位：`common/local.py` 的 contextvars + `server/logging.py` 两处 formatter 从当前请求读
`request_uuid` / `request_user`。若做，接入方式为：

1. 新增一个 logging `Filter`：从 OTel context 取 `trace.get_current_span().get_span_context()`，写入
   `record.trace_id`（关闭态写 `""`）；
2. `server/settings/logging.py`：formatter 的 `filters` 挂该 Filter，`main` / `verbose` 的 format 串加
   `%(trace_id)s`；`JsonFormatter` payload 增 `trace_id` 键；
3. 改动面 **2 个文件**（`server/logging.py` + `server/settings/logging.py`），无 DB / 接口改动，关闭态
   `trace_id` 为空串、格式不破。

> 备选（更省）：不引 OTel，直接用既有 `request_uuid` 充当关联 id——但 `request_uuid` 不进指标、不跨进程，
> **无法替代跨层关联**，故不选。

### 9.6 结论

**条件做。默认不做全量常开追踪。**

理由：

- **需求强度不足**：单服务形态、无外部 collector / 多语言链路；Sentry performance 已覆盖请求 / 任务 / DB /
  缓存瀑布，且默认零开销（DSN 空不初始化）；
- **运维成本反噬**：单人维护 + 自建栈，常开追踪 = 新增「collector / 存储 / 保留 / 告警 / 备份 / 升级」
  持续面，收益仅在少数跨层排障场景兑现；
- **现有能力兜底**：日志 `request_uuid` + Prometheus 指标（§三）+ SLO 快照 + Sentry 已覆盖绝大多数
  「哪条请求慢 / 哪个任务失败」；
- **一旦做则最小化**：只做「标准 SDK + 自动仪表 + trace_id 进日志」，不做全量业务 span、不落库、不建告警。

### 9.7 若触发：最小埋点集 + 开关 + runbook + 回退（本轮不实施）

**最小埋点集**（按性价比排序）：

1. ASGI / Django（HTTP span + contextvars）；
2. Celery（web ↔ worker ↔ heavy 跨进程）；
3. 出站 `requests`（AI SDK / webhook / MCP）；
4. psycopg（DB）+ redis（缓存）；
5. trace_id 进日志（§9.5）；
6. channels WS 手动 span（最低优先级，按需）。

**开关配置**（`config.yml`，默认关；沿用 `server/conf/defaults.py` 键声明范式）：

```yaml
OTEL_ENABLED: false             # false = 不初始化 SDK，零开销
OTEL_TRACES_SAMPLE_RATE: 0.0    # 排查窗口临时设 1.0
OTEL_EXPORTER_OTLP_ENDPOINT: "" # OTLP/HTTP 端点；空 = 不导出
OTEL_SERVICE_NAME: xadmin-server
```

**启用 runbook**（仅在排查窗口执行）：

1. 起临时 collector（如 Jaeger / Tempo all-in-one 容器，用完即删，不常驻）；
2. `config.yml` 设 `OTEL_ENABLED: true` + endpoint + `OTEL_TRACES_SAMPLE_RATE: 1.0`；
3. 重启四进程（web / worker / heavy / beat，配置非热加载）；
4. 复现问题 → 在 collector 按 trace_id 查询；日志中 `trace_id` 字段可直接 grep 定位；
5. 排查结束：开关还原 `false` + 重启 + 停 collector。

**回退路径**：开关置 `false` + 重启即回到零开销；SDK 未初始化时全部 API no-op，无数据面残留（不落库、
不写文件）。可选的依赖卸载 = 从 `pyproject.toml` 移除 OTel 包 + `uv lock` 重导产物，不影响运行。

### 9.8 触发条件（何时重新评估）

命中任一即立项（**先 ADR**，属重依赖红线）：

1. 出现**真实跨服务 / 跨进程链路排障痛点**（如引入外部组件、拆分服务）；
2. 需接入**组织级 OTel 后端**（统一采集 / 多语言 / 与既有链路互操作）；
3. **Sentry 能力不足**——需自建查询、采样受限、或需标准 `traceparent` 与外部系统互操作；
4. 出现**按请求 / 任务的细粒度性能归因**需求，且 Sentry waterfall 不足以支撑。
