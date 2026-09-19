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
| 队列积压 | redis `llen` / health 探测 | ✅ |

**说明**：任务耗时直方图（`xadmin_celery_task_duration_seconds`）已按同一模式**跨进程聚合**
（2026-09-16 交付：worker 写 redis 累积桶 + sum/count，端点渲染完整 histogram——**零值桶输出**，
`histogram_quantile` 可直接算任务 P95；SLO 暂不依赖，作为诊断指标使用）。
**校准方法**：观察 ≥3 个月后按实际数据校准目标值与告警阈值（现维持下方初始口径）；初期形态
（2026-09-16）：周期任务全 SUCCESS、HTTP 指标待流量积累。

**采集机制（A2，2026-09-17 上线）**：`utils/slo_snapshot_cron.sh` 每日（宿主 cron / systemd timer）
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
| **容器 OOM** | `docker events` 的 `oom` 事件 | `utils/oom_alert.sh` → `/api/common/api/ops-alert` → 站内信 + 邮件 + Webhook `system.ops_alert` | ✅ 新增（A1，第十六轮验证） |
| HTTP 可用性 / P95 延迟 / 队列积压 | Prometheus 指标 + SLO 阈值 | 指标端点已暴露；自动投递需外部 Prometheus / Alertmanager | ⏳ 登记（部署形态就绪后按需） |

维护约定：新增告警必须经演练验证（本清单同步登记证据）；仅接已证实场景，避免告警噪音。

### 宿主侧 watcher（容器 OOM）

`utils/oom_alert.sh` 在 **Docker 宿主机** 常驻运行（需 docker socket 与服务端 HTTP 可达）：

```bash
OPS_ALERT_URL=https://<xadmin-host>/api/common/api/ops-alert \
OPS_ALERT_TOKEN=<与系统设置 OPS_ALERT_TOKEN 一致> \
bash utils/oom_alert.sh
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

## 六、演练记录（逐次追加）

### 首轮（2029-01，A4 窗口）：beat / worker / Redis / 归档

| # | 演练 | 操作 | 结果 |
|---|------|------|------|
| 1 | beat 停摆 | 停 `xadmin-celery-beat` 46s 后恢复 | 调度暂停、恢复即续（周期任务无丢失）|
| 2 | worker 堆积 | 停 `xadmin-celery-worker`，投递任务后观察 | 队列堆积 3 → health `celery_status:false`（判活正确）→ 恢复后消化为 0 |
| 3 | Redis 冻结 | 停 `xadmin-redis` | **暴露缺陷**：health 端点被挂起（>10s，超容器 healthcheck 5s 超时）→ 当轮修复（探测并行化 + 共享预算）**未闭环**（见下）|
| 4 | 归档失败 | `chmod 000` 归档目录 + 切段 | `last_failed_wal` 可判（`failed_count=3`）→ 恢复权限后自动补归档 ✓ |

### 第二轮（2029-10，运营基线窗口）：Redis 冻结闭环复测

首轮修复（探测并行化）**未闭环**：复测仍 10.1s（TimeoutError 错误页）。五轮迭代定位与修复：

| 轮次 | 变更 | 耗时 | 表现 |
|------|------|------|------|
| 0（复测）| — | 10.1s | TimeoutError 错误页（worker timeout）|
| 1 | redis socket 超时（connect/read 1s）+ `retry_on_timeout=False` | 7.1s | 500（TimeoutError 穿透 DRF 限流器）|
| 2 | `IGNORE_EXCEPTIONS=True`（django_redis 标准降级）| 10.1s | **200 + 正确降级**（`redis_status:false`）|
| 3 | connect 超时 1s→0.2s、read 0.5s | 5.7s | 200（仍超 healthcheck 5s）|
| 4 | health 豁免 DRF 限流 + 探测预算 2s→1s | **1.85s** | 200 + 降级正确（远低于 5s）✓ |

**根因链（traceback 实证）**：Redis 冻结（容器 stop，连接挂起而非拒绝）时无 socket 超时 →
请求在**中间件/配置读取**（ConfigCache 走 redis）与 **DRF 限流器**（限流计数走 redis）阶段挂死 →
超时异常（redis `TimeoutError` 与 `ConnectionError` 在 redis-py 中平级，django_redis 会转为
`ConnectionInterrupted` 后继续上抛）穿透框架层 → 500。

**修复清单**：
1. `server/settings/base.py`：redis 连接池 `socket_connect_timeout=0.2` / `socket_timeout=0.5` / `retry_on_timeout=False`；
2. 同处 `IGNORE_EXCEPTIONS=True`：缓存不可用时读返回 None、写静默（fail-open 标准降级语义）；
3. `common/core/config.py`：ConfigCache 读/写异常兜底 → 回落读库（配置通路不被缓存故障阻断）；
4. `common/api/common.py`：health 视图豁免 DRF 限流（基础设施端点不吃业务限流）；
5. `common/utils/health.py`：探测预算 2s→1s。

**验收**：Redis 冻结时 health **1.85s** 返回 `status:false` + `redis_status:false`（判活正确、远低于
healthcheck 5s 超时）；恢复后无人工干预自动回正。单测 2378 全绿 + E2E smoke 9 passed。

### 第三轮（2030-03，交付工程窗口）：DB 主库重启

| 阶段 | 表现 |
|------|------|
| 重启中（PG 停止） | health **1.05s** 快速失败：`db_status:false` + `db_time:"terminating connection..."`（DB 探测无挂死）|
| 修复前恢复期 | **永不恢复**：连续 6 次探测全 false（"the connection is closed"），仅进程重启可恢复 |
| 根因 | psycopg_pool 默认 `check_connection` 以**空查询**判活，检测不到「PG 重启后服务端已断开、客户端未读到终止报文」的**半开连接**——坏连接被反复取出复用 |
| 修复 | 配置期替换 `ConnectionPool.check_connection` 为**真实 SELECT 1** 判活（`common/db.py` + 测试 3 例）——坏连接在取用阶段被识别淘汰；Django 硬编码 check 参数无法从 OPTIONS 覆盖（实测 duplicate keyword 启动失败），故采用配置期静态方法替换 |
| 修复后恢复期 | **自动恢复**：数次探测内收敛为 true（uvicorn 多 worker 各自检出坏连接，存在几秒波动窗口——可接受边界）；全量 2387 passed |

### 第五轮（2030-12，SLO 校准窗口）：网络分区（PG 断网）——含一次环境事故与恢复

**演练设计**：`docker network disconnect xadmin-server_net xadmin-postgresql`（模拟 PG 网络分区）→ 观察 → 恢复。

**发现（修复前）**：PG 断网后 health **3 次 20s+ 完全无响应**——根因：DB 连接**无 `connect_timeout`**
（TCP 挂到系统默认超时）。**修复**：`DB_OPTIONS["connect_timeout"] = 3`（局域网建连 <10ms，3s 充裕；
池/非池模式均透传 psycopg）。

**事故与恢复（重要教训）**：`docker network disconnect/connect` 触发了 **Docker embedded DNS
记录损坏**——全网络容器均解析不了 `postgresql`（且 `getent` 可能"假成功"（仅主机名无 IP），
libpq/Python `getaddrinfo` 真失败）；期间 server 陷入 migrate 失败的重启循环。
**恢复动作**：`docker compose up -d --force-recreate --no-deps postgresql`（重建容器即重注册 DNS）→
重启各服务 → 全线 healthy（**数据无损**，PG 数据在卷）。

**结论与规范**：
1. `connect_timeout` 修复保留（本轮暴露的真缺陷）；
2. **演练规范新增**：网络层操作（`network disconnect`）**仅限可重建的环境**，执行前确认恢复预案
   （重建容器的 compose 命令与数据卷就绪）；**首选"容器重启"类场景**（第三轮 DB 重启即为正例）；
3. DNS 快速失败场景表现（解析即失败）为本次观察；`connect_timeout` 在 **TCP 挂起**（非 DNS 失败）
   场景的验证待后续演练设计（可用 iptables 丢包模拟）。

### 第六轮（2031-03，交付工程窗口）：备份失败（单次模式）

- **方式**：`BACKUP_ONCE=1` 单次模式（演练 / 外部 cron 用法；容器入口为常驻循环）；
- **失败态**（`chmod 000 /backups`）：**EXIT=1** + `WARN: backup FAILED, remove partial file` +
  `WARN: 本轮数据库备份失败`——失败可判（调度侧可感知），临时文件不落正式名；
- **恢复后**：**EXIT=0**（媒体备份 1.9M、WAL 归档检查齐全）；
- **规范补充**：`db_backup.sh` 为常驻循环脚本（轮间 `sleep BACKUP_INTERVAL`），
  **手动执行必须使用 `BACKUP_ONCE=1`**——否则命令挂起在轮间休眠（2026-09-16 实测踩中，
  误执行进程已清理、备份产物无损、多出的一份备份由 KEEP_DAYS 自然回收）。

### 第七轮（2031-12，SLO 窗口）：TCP 挂起（connect_timeout 边界验证）

- **背景**：第五轮登记的未覆盖边界——`connect_timeout` 在 **TCP 挂起**（非 DNS 快速失败）场景的表现；
- **环境约束**：xadmin-server 容器无 `iptables`/`tc`（无 NET_ADMIN）→ 改用**用户态黑洞模拟**
  （本地 TCPServer accept 后只吞不回包，等价覆盖「TCP 建连成功、PG 协议无响应」）；
- **结果**：`connect_timeout=3` → **3.03s 快速失败**（`ConnectionTimeout: connection timeout expired`，
  libpq 超时覆盖整个连接建立过程含启动包交换）；对照组（无 timeout，SIGALRM 6s 兜底）**6.00s 仍挂起**
  ——场景有效性成立，排除"快速拒绝"假象；
- **生产核对**：容器实际 `OPTIONS={'connect_timeout': 3, 'pool': {min 2 / max 8}}` 已生效；
- **边界说明**：SYN 不可达（真丢包）与协议无响应共享同一 libpq timeout 逻辑，本模拟覆盖后者
  （更严格：TCP 已建立仍必须超时）；真丢包场景的部署级观察待具备 NET_ADMIN 的演练环境。

### 第八轮（2032-03，交付工程窗口）：PG 只读降级（存储层自我保护）

- **场景**：`default_transaction_read_only=on`（磁盘不足等触发 PG 自我保护进入的只读态）；
- **方式**：`ALTER SYSTEM SET` + `pg_reload_conf()`（秒级生效、无需重启）——**注意须单语句执行**：
  多语句 `psql -c "A; B"` 会被包进事务块而报 `ALTER SYSTEM cannot run inside a transaction block`
  （2026-09-16 首次执行即踩中，属操作姿势问题，非系统缺陷）；
- **读路径**：health 四指标全 true（无 degraded）、psql SELECT 正常——**读侧零降级**；
- **写路径**：应用写入明确失败 `InternalError: cannot execute INSERT in a read-only transaction`
  ——错误语义清晰可诊断（非静默、非挂起）；
- **恢复**：`ALTER SYSTEM RESET` + reload → `off`，写立即可用（无需重启）；
- **结论**：存储层只读态下「读可用、写明确失败、恢复秒级」，符合预期，无缺陷。

### 第九轮（2032-06，审计与安全窗口）：坏配置注入（fail-fast 验证）

- **场景**：向 `config.yml` 注入类型错误配置（`LOG_BACKUP_COUNT: "abc"`）后重启；
- **机制**：`server/conf.py` 的 `convert_type` 对转换失败**静默保留原值**（宽容解析），
  但强类型消费点（`int(CONFIG.LOG_BACKUP_COUNT or 0)`）**响亮失败**：
  `ValueError: invalid literal for int() with base 10: 'abc'`（日志直接指向问题配置行）；
- **行为**：容器进入 `Restarting (1)` 崩溃循环（restart policy 反复拉起）——**不静默降级**
  （未用默认值 30 继续跑）；
- **恢复**：还原 `config.yml` + 重启 → `Up (healthy)`、health 四指标全 true；
- **评估登记**：`convert_type` 的「宽容解析 + 严格消费」组合当前总能暴露坏值（消费点均有类型转换），
  但**弱类型消费点**（直接按 str 使用）存在静默接受面——登记评估出口
  （不急切修改：动全局配置解析影响面大）。

### 第十轮（2032-12，SLO 窗口）：SECRET_KEY 轮换（JWT 签名失效验证）

- **场景**：服务端密钥轮换（安全运维常规动作）——验证轮换的会话影响面与闭环恢复；
- **方式**：替换 `SECRET_KEY`（config.yml）→ 重启 server；用旧 key 签发的 JWT 对受保护端点
  （`/api/system/search/user`）做三态验证；
- **结果**：无 token **401**（端点需认证，预检成立）→ 旧 key token **200** →（轮换）→ 同 token
  **401**（签名失效，符合预期）→（恢复 key）→ 同 token **200**（闭环）；
- **伴随观察**：server 重启后 ~15s 内 health `celery_status` 短暂 false（worker 心跳未续），
  ~8s 后自愈 true——探针语义正确（过期判定 + 自愈），无缺陷；
- **操作登记**：受保护端点 URL 为 `SimpleRouter` 形态（**无尾斜杠**）——首轮因尾斜杠 404 未能
  触达（姿势修正）；轮换 = **全端重登**影响面确认（本环境无活跃会话残留）。

### 第十一轮（2033-03，交付工程窗口）：敏感信息泄漏扫描

- **范围**：生产日志（`server.log`）+ 仓库侧配置跟踪状态；
- **值级扫描**：SECRET_KEY **0** / METRICS token **0** / JWT（`Bearer eyJ`）**0** —— 无值级泄漏；
- **config.yml**：已被 `.gitignore` 忽略（`git check-ignore` 通过；`git ls-files` 命中的
  1 个为示例文件）✓；
- **`password` 字面 286 处**：均为 DEBUG 级请求/响应正文中的**字段名或掩码值**（`*` 串）——
  **脱敏机制在工作**（password → 掩码；username 走 `v2:` AES 密文），且 DEBUG 级不进生产日志；
  **唯一遗留**：登录中间态 `tmp_token` 完整落入操作日志 `body`（短时效中间态，低风险）——
  登记评估出口；DEBUG 正文裁剪同步登记。

### 第十二轮（2033-06，审计与安全窗口）：敏感文件权限审计（含加固）

- **审计发现**：host / 容器 `config.yml` 为 **644**（含密钥文件全局可读）；`data/`、`data/logs/` 755；
- **加固执行**：`chmod 600 config.yml`（host 与容器共享挂载，一处生效；服务读取正常无影响）；
- **登记**：installer 侧 config 生成权限随发布节奏核对；`data` 目录 750 收紧列为可选加固项；
- **结论**：权限面第一轮收敛（644→600），审计与加固机制建立。

### 第十三轮（2033-12，SLO 窗口）：磁盘压力（tmpfs 小盘）——备份链路 fail-safe

- **环境**：隔离一次性容器（生产同镜像 + `--tmpfs /drill:size=8m`，daemon 挂载免容器内权限）+ 生产 PG 只读 `pg_dump` + `BACKUP_ONCE=1` 单次模式；不触碰生产备份目录与容器，`--rm` 自动回收；
- **基线（空间充足）**：备份 **920K** 成功、sha256 sidecar 落盘、`.latest_backup` 更新、`exit=0`；
- **盘满（7.5M/8M，剩 524K）**：`gzip: stdout: No space left on device` 自然暴露 → 脚本清理临时文件 → **不产出损坏正式包**（无 `.sql.gz` / `.tmp` 残留）→ `exit=1`（单次模式退出码可供调度侧感知）→ WAL 归档巡检不中断（failing=f / pending=0）；
- **结论**：备份链路磁盘耗尽 fail-safe（临时文件机制有效，首轮归档失败演练之后的写入失败面补测）；告警投递未配置（按设计静默跳过）；归档目录满（WAL 堆积型）与媒体包盘满为同构链路的未覆盖边界，随场景池滚动。

### 第十四轮（2034-03，交付工程窗口）：DNS 恢复流程复演 + installer config 权限核对

- **DNS 复演（隔离网络，零风险设计）**：独立 `drill_dns_net` + 3 个临时容器（client / target / bystander）：
  基线解析均 OK → 对 target 执行 `disconnect` → `connect`（第五轮事故的触发操作序列）后**未复现**全网络解析失效
  （当前 Docker 版本行为已变化，历史触发条件不复现）→ **恢复动作彩排**：同名重建容器
  （`docker compose up -d --force-recreate` 等效）后解析正常，恢复流程有效；演练后容器/网络全清理，生产零接触；
- **installer config 权限核对（第十二轮登记的跟进项）**：`prepare_config` 生成的 `config.txt`
  （含 DB/Redis 密码与 SECRET_KEY）为 **umask 默认权限**（目录 755 / 文件 644，全局可读）、无任何 chmod；
  **加固**：`chmod 700 ${CONFIG_DIR}` + `chmod 600 ${CONFIG_FILE}`（幂等，覆盖安装/升级/配置变更三处调用路径）；
  沙盒双场景验证（全新安装 700/600、存量 755/644 幂等收敛）；
- **结论**：DNS 恢复规范保留（重建容器动作经彩排）；installer 权限面与 host 侧口径对齐（第十二轮 644→600 的延伸）。

### 第十五轮（2034-06，审计与安全窗口）：容器内存限制（OOM 边界）

- **环境**：隔离容器（生产同镜像 + `--memory`/`--memory-swap` 限制 + `BACKUP_ONCE=1`），零生产接触；
- **宽限对照（128m）**：备份成功（928K、`exit=0`）；
- **紧限（16m）**：**备份仍成功**——`pg_dump | gzip` 流式架构峰值内存 **< 16MB**（低内存实证；将来设置 mem_limit 的保守下限参考 ≥32m）；
- **OOM 构造对照**：内存超限进程 → 容器 `ExitCode=137` + `OOMKilled=true`；**`docker events` 暴露 `container oom` 事件**（可监听接入告警；当前未接入 → 登记观察项）；
- **与第十三轮互补**：磁盘满 = 可错误处理（fail-safe 清理）；OOM = 进程被动被杀（无优雅路径，写入型任务可能留残件——当前备份流程内存占用低未构造到主流程 OOM 面）；
- **结论**：内存面边界数据建立；OOM 事件可用但未接入告警（观察项）。

### 第十六轮（2034-10，运营基线窗口）：容器 OOM 告警链路端到端验证（A1）

- **背景**：第十五轮登记「`docker events` 暴露 `container oom` 事件，可监听接入告警；当前未接入」——
  本轮完成接入并端到端验证；
- **接入**：宿主侧 `utils/oom_alert.sh`（`docker events --filter event=oom`，纳秒游标去重 + 断线重连）
  → `POST /api/common/api/ops-alert`（独立令牌 `X-Ops-Token` = 系统设置 `OPS_ALERT_TOKEN`）
  → `OpsAlertMessage` 站内信 + 邮件（超管订阅自愈）+ 出站 Webhook `system.ops_alert`；
  60s 同来源同事件节流（与备份告警同范式）；
- **验证（真实事件）**：隔离容器 `--memory=64m --memory-swap=64m` + 1GiB 分配
  → `OOMKilled=true / ExitCode=137`；watcher 捕获 `container oom` 并投递（无失败 WARN）
  → 站内信落库「运维告警：container oom」（danger、超管未读 = 1）
  → 订阅自愈（`OpsAlertMessage`，`site_msg + email`，receivers = 1）；重复投递由 60s 节流合并（单测覆盖）；
- **结论**：OOM 告警链路端到端闭环（事件 → 投递 → 落库 → 收件人），监控覆盖清单见 §三；
  HTTP 可用性 / 延迟 / 队列积压的自动投递保持登记（依赖外部 Prometheus / Alertmanager，部署形态就绪后按需）。

### 第十七轮（2034-10，运营基线窗口）：真丢包部署级观察（第七轮登记项收口）

**背景**：第七轮（TCP 挂起模拟）登记「真丢包（SYN 不可达）的部署级观察待具备 NET_ADMIN 的演练环境」——
本轮在 192.168.0.200 测试服（ARM64 VM 的 xadmin 全栈部署）具备注入能力后执行。

**注入方式（两次姿势修正，均为真实踩坑）**：
1. 第一版在 VM 宿主 `iptables DOCKER-USER` 链注入 DROP —— **规则 0 命中**：VM 未加载 `br_netfilter`
   （bridge-nf-call-iptables 不可用），容器间 bridge 流量**不经过 FORWARD 链**；
2. 改用 `nsenter -t <容器PID> -n iptables` 在**容器 netns 的 OUTPUT 链**注入——精确作用于
   「server/worker → PG:5432」且不影响宿主网络：`-d <pg> -p tcp --dport 5432 -j DROP`
   （注入后 `/dev/tcp` 探针 6s 挂起验证命中；规则计数器随重传增长）。

**观察（注入约 6 分钟，随后删规则恢复）**：
- **health 全链路无响应**：容器内直连与经 nginx 均 `000 / 15s+`（非慢响应，是**无响应**）；
- **线程取证（内核栈）**：每个 gunicorn worker 的同步处理线程阻塞在
  `tcp_recvmsg → sk_wait_data → wait_woken`（**读 PG 响应的 socket 等待**）；
  psycopg pool 线程处于 `futex`（池锁串行等待）；
- **连接状态**：容器 netns 中 7 条 → PG:5432 连接全部 **ESTABLISHED（半开）** + TCP 重传定时器运行
  （`tm->when` 非零）——SYN 与数据的 DROP 对 TCP 栈不可见；
- **恢复**：删除规则后 **~1 分钟自愈**（TCP 重传送达，无需进程重启；与第三轮「PG 重启后半开连接仅在
  进程重启后恢复」形成对照——差异在于本轮对端连接仍存活，重传最终成功）。

**结论（韧性缺口，登记评估出口）**：
1. `connect_timeout=3` 覆盖「建连」阶段（第七轮已验证）；**已建立连接上的读（recv）无 socket 级超时**，
   半开连接要等 TCP 重传耗竭（Linux 默认 ~15 分钟）才返回错误——期间该 worker 的同步请求处理被逐个
   占死（thread_sensitive 串行），health 亦排队（探测 1s 预算约束「探测等待」，无法救「请求未被调度」）；
2. **加固候选（登记评估，涉及全局 DB 连接配置，不急切修改）**：libpq `tcp_user_timeout`（Linux）
   + TCP keepalives（`keepalives_idle/interval/count`）——目标：半开连接在池层 30s 级识别淘汰；
3. 本轮为「部署级观察」定位（不改代码）；行为与风险窗口已量化记录。

**演练姿势沉淀**：容器间流量注入优先 `nsenter` 容器 netns（精确、免 br_netfilter 依赖）；
演练脚本输出写文件时注意 stdio 块缓冲（用变量捕获 + shell echo，勿依赖 `curl -w` 直接重定向）；
规则必须 trap 兜底清理。

**修复与复演（2026-09-18 当日闭环）**：本轮暴露的韧性缺口四层修复——①**基础设施端点事务豁免**
（`common/urls.py`，**根因修复**）：`ATOMIC_REQUESTS=True` 使每个请求进入视图前
`ensure_connection`，DB 故障时 health 在请求入口直接 500 → health/metrics/csp-report 用官方
`non_atomic_requests` 包装 URLconf callback；②**DB 半开快速失败**：`tcp_user_timeout=30s`
（内核对未确认数据超时强制断开）+ keepalives 三件套 + 池 `timeout=5s`（取用等待上限）
+ 池 `reconnect_timeout=10s`（失败重连调度）；③**字典读取降级**（`system/utils/dict.py`）：
DB 故障返回空列表不阻断请求（读取点在 serializer 字段绑定/请求路径上，曾是"每请求 500"
直接来源；失败不写缓存、恢复即重试）；④**日志格式器兜底**（`server/logging.py`）：无
`user` 属性的请求不再崩溃（500 traceback 曾被 Logging error 吞掉）。配套：启动自检快速失败
（`hands.py` 对 SystemCheckError 不重试 60 次）、CI 补 `check --database` 门禁、覆盖率门禁
82 → 85（实测 87.21%）。

**复演对比（同场景 v8 实测）**：注入期 health `000 / 挂 12s+`（修复前）→
**`200 / 1.0s / db=False`**（修复后：探测预算内快速降级、无 500）；半开连接清理从"等 TCP
重传（分钟级）"→ `tcp_user_timeout` 30s 内核断开 + 池淘汰；恢复经 `reconnect_timeout=10s`
快速回正（复演终态四指标全 true）。登记观察：① `django.request` 的 500 日志在 ASGI
`_get_response` 层异常路径未落应用日志；② `backup-alert` / `ops-alert` 上报端点自身写库
（DB 故障时告警通路依赖 DB，边界登记）。

## 七、运营基线快照（2029-10 窗口）

**指标端点启用（2026-09-16）**：`METRICS_ENABLED=true` + `METRICS_TOKEN`（config.yml，Bearer 保护，
未启用时 404）——启用过程修复一个**死开关**：`METRICS_ENABLED/TOKEN` 此前只用于条件挂中间件、
**未导出到 django settings**（读方 `getattr(settings, ...)` 永远落 False → 端点永远 404）；
已无条件导出 + 守护测试（与 `SECURITY_AES_V1_DECRYPT_ENABLED` 同类缺陷的**第 2 次踩中**，
转发对账纪律继续适用）。

**快照（2026-09-16，自当次重启起）**：

| 项 | 值 | 备注 |
|----|----|------|
| 指标端点 | ✅ `/api/common/api/metrics`（Bearer）| 4 指标族：http_requests/duration + celery_tasks/task_duration |
| HTTP 请求形态 | health 7 次全 200 | 打点验证；正式基线待运行累积 |
| 队列积压 | 0（redis `llen celery`）| 即时 |
| 今日 WARN | 102,447 → **已降噪**（96% = 缓存失效日志）| MagicCache/MagicCacheResponse 4 处 warning→debug |
| 今日 ERROR | 415 | 含 404/权限类请求噪声 |
| `unexpected_exception` | 累计 9,175 行（未按月切分）| 观察项 |
| SLO 校准 | 按计划 2029-12（需运行期数据积累）| 本次登记端点启用与快照方法 |

**发现与处置**：① 缓存失效日志高频 WARN → 降 debug；② decrypt（v1 观察）WARN 切换后归零；
③ **Redis 冻结韧性缺陷五轮修复**（§六）；④ metrics 死开关（修复 + 守护测试）。
