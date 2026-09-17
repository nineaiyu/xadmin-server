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
