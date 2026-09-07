# ADR-006: ASGI 形态下 DB 连接策略——psycopg3 server 端连接池（OPTIONS.pool），不引入 pgbouncer

- 状态：已接受（2026-09-07，TD-25 根因修复）
- 关联：半年规划 TD-25 / T3.1 性能基线 / ADR-004（Django 停留 5.2 LTS）
- 位置：`server/settings/base.py`（DATABASES）、`server/conf.py`（DB_POOL* 配置）、`requirements.txt`（psycopg2-binary → psycopg[binary,pool]）

## 背景

T3.1 性能基线测定发现（详见 `docs/ops/performance-baseline.md` §3.1）：

1. Django 的 `ASGIHandler` 为每个请求创建独立线程（`ThreadSensitiveContext`），线程随请求结束消亡，其 DB 连接随之关闭丢弃——`base.py` 的 `CONN_MAX_AGE=600` 持久连接机制在 ASGI 形态下**完全无效**，等效于**每请求新建 PG 连接**（TCP 三次握手 + 认证往返摊进每个请求的延迟）；
2. 实测持续 ~600rps 时宿主机临时端口耗尽（macOS 与 Linux 容器均复现 `EADDRNOTAVAIL: Cannot assign requested address`）→ **13-27% 请求 500**；生产同款部署（compose gunicorn + UvicornWorker ×4）同样暴露于此风险；
3. 首测复现数据（2026-09-07 复测，k6 routes 20VU ≈ 599rps，未加任何缓解）：请求失败率 **20.33%**（36,066 请求中 28,732 失败），1 分钟内 PG 侧 `pg_stat_activity` 连接数在 **3 ↔ 289** 之间剧烈震荡。

psycopg2 不支持 Django 5.1+ 的 server 端连接池（该特性仅 psycopg3 后端实现）。

## 备选方案

1. **psycopg3 + Django `OPTIONS.pool` server 端连接池**（进程内池，Django 5.1+ 原生支持）；
2. **pgbouncer 中间件**（compose 增加独立组件，应用侧无感）；
3. 维持 psycopg2 + 每请求连接（内核参数缓解 tcp_tw_reuse/扩大临时端口段）。

## 决策

**方案 1**，理由：

1. **根治而非缓解**：连接在池内复用，TCP+认证开销只在扩容时发生；方案 3 只是推迟端口耗尽的到来，且 13-27% 500 的故障模式依然存在；
2. **零新增部署组件**：pgbouncer 需要在所有部署形态（compose/裸机/国产化）中引入新组件、新配置面与新的故障排查路径，单人维护成本高；方案 1 只改依赖与 settings，部署形态零变化；
3. **语义风险更低**：pgbouncer transaction pooling 模式与 `ATOMIC_REQUESTS`、migrate 的 advisory lock、session 级状态（`SET`、prepared statements）存在已知兼容陷阱；Django 对 psycopg3 池已默认禁用 prepared statements 并在 `close()` 时归还连接（`ATOMIC_REQUESTS` 请求结束时连接正确归还池），语义由 Django 官方支持；
4. **与 ADR-004 对齐**：Django 停留 5.2 LTS（连接池为 5.1+ 特性），无需等 6.2 升级窗口即可落地；
5. 代价可控：psycopg2 → psycopg3 是 Django 官方推荐的现代驱动路线；本仓库无任何 psycopg2 直接 import（已 grep 验证），ORM 层完全透明。

## 实施（2026-09-07）

- `requirements.txt`：`psycopg2-binary==2.9.11` → `psycopg[binary,pool]==3.2.13`；
- `server/conf.py`：新增 `DB_POOL`（默认 True）/ `DB_POOL_MIN_SIZE`（默认 2）/ `DB_POOL_MAX_SIZE`（默认 8）配置键；
- `server/settings/base.py`：`DB_ENGINE=postgresql` 且 `DB_POOL` 开启时注入 `OPTIONS.pool`，并强制 `CONN_MAX_AGE=0`（池模式与持久连接互斥，Django 会拒绝启动非零配置）、`CONN_HEALTH_CHECKS=True`（池取用前轻量存活校验）；MySQL/vastbase/sqlite 引擎行为不变；
- 池为**每进程独立**（Django `_connection_pools` 类属性）：gunicorn 4 worker + celery 子进程，容量核算公式见 conf.py 注释（默认配置下 server 侧峰值 4×8=32 连接，远低于 compose PG `max_connections=200`）。

### 压测验证（k6 routes 20VU/1m，专用隔离环境，生产同参 gunicorn UvicornWorker ×4）

| 指标 | Before（psycopg2，每请求建连） | After（psycopg3 池，2-8×4） |
|------|------|------|
| 请求失败率 | **20.33%**（28,732/36,066，599.5rps） | **0%**（62,780/62,780，1,042.6rps，+74%） |
| PG 连接数波动 | 3 ↔ 289（震荡） | 13 ↔ 25（池容量内稳定） |
| P50 / P95 | 31.3 / 53.7 ms | **20.2 / 32.5 ms（-35% / -39%）** |
| 端口耗尽 500 | 复现（EADDRNOTAVAIL） | 未复现 |

## 后果

- 正面：高并发稳定性问题根治；每请求 TCP+认证常数延迟消除；部署形态零变化；为 2027-01 Django 6.2 升级铺路（psycopg3 为 6.x 主推驱动）；
- 负面：驱动大版本切换（psycopg2 → psycopg3），若下游存在依赖 psycopg2 私有行为的裸 SQL 需回归（当前无）；连接池需纳入容量核算（新增配置键）；
- 中性：`CONN_MAX_AGE` 在 postgresql 池模式下失效是 Django 既定语义；sqlite（测试/E2E）与 MySQL 引擎不受影响。

## 回滚

`config.yml` 设置 `DB_POOL: false` 即恢复 `CONN_MAX_AGE=600` 旧行为（驱动仍为 psycopg3，可独立回滚依赖版本）。
