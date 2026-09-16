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
