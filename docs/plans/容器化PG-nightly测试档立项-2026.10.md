# 容器化 PG 的 nightly 测试档（立项文档）

> 2026-10-01 立项。**一句话**：在 PR 门禁（sqlite 内存库）之外，新增一个**每晚对真实 PostgreSQL 容器跑全量后端测试**的夜间档位，收口 sqlite 门禁的「方言盲区」。改动面：新增 `tests/settings_pg.py` + 一个 workflow 文件；**不改 PR 门禁、不改 E2E、不改 loadtest**。

---

## 一、背景与动机（为什么 sqlite 门禁不够）

现状测试分层与各自的隔离策略：

| 层 | 环境 | 文件 | 覆盖 | 盲区 |
|---|---|---|---|---|
| PR 单测门禁 | sqlite `:memory:` + FakeRedisCache（进程内）+ eager celery | `tests/settings_test.py`、`.github/workflows/test.yml` | 4600+ 用例、`-n auto`、~10min | **方言盲区**（下表） |
| E2E（playwright） | sqlite 文件库（WAL + busy_timeout）+ FakeRedis + 真 daphne | `tests/settings_e2e.py` | 主链路 × 双浏览器 | 同上 + 确定性优先 |
| 性能基线（k6） | **真实容器**：PG 17.11 + Redis 8.10.2 + gunicorn×4 | `loadtest/settings_loadtest.py`（§3.1） | 6 接口性能 | 只测性能口径，不做正确性断言 |
| 生产同构栈 | docker-compose（nginx + gunicorn + PG 17.11 + Redis 8） | `docker-compose*.yml` | 部署冒烟 | 无自动化测试 |

sqlite 门禁的方言盲区（已发生 / 可预判）：

1. **PG 专属模型检查曾漏进生产**：`GinIndex` 缺 `django.contrib.postgres` 安装（`postgres.E005`）、索引名超长（`models.E034`）等检查仅在 `check --database` 运行时执行——**2026-09-18 部署实测卡死 `manage.py start`，CI 此前无覆盖**（`.github/workflows/test.yml:45-50` 已补 `check --database`，但那只跑「检查」，不跑「迁移与查询」）。
2. **迁移从未在 PG 上真实执行过测试**：pytest-django 建测试库跑全部迁移，当前在 sqlite 上执行；PG 上的约束命名（≤63 字符截断）、`GinIndex`/`JSONField` 索引 DDL、`django.contrib.postgres` 特性路径零覆盖。
3. **PG 配置分支「构造了但从未连接」**：`tests/settings_test.py:20-23` 显式钉住 `DB_ENGINE=postgresql`（防配置分支漂移），但实际连接仍被 sqlite 覆盖——psycopg3 连接池（`OPTIONS.pool`，2026-09-07 根因修复引入）**只在 loadtest/生产被真实创建过**，测试零覆盖。
4. **FakeRedis 是进程内仿真**：实现了 `lock` / `delete_pattern`（`tests/cache_backend.py`），但真实 Redis 的 TTL/INCR/阻塞语义、连接池行为、跨进程可见性均未覆盖（`metadata` 单飞锁、限流计数器等）。
5. **JSON 路径列（ADR-069/070/071）等特性在 PG 上可能走不同执行计划与函数**（`Substr` 分桶 / JSON 路径），sqlite 与 PG 的函数库不同，行为差异只能靠真库暴露。

成本收益：sqlite 门禁保留（快、稳、零依赖），方言正确性由**每晚一次**的真库档兜底——失败不阻断 PR，次日分诊。

## 二、目标与非目标

**目标**

- G1：每晚定时对真实 PostgreSQL（17 系，与生产/loadtest 同大版本）跑**全量后端 pytest**；
- G2：单变量原则——除 `DATABASES` 外，缓存（FakeRedis）/ channel / celery eager / hasher 等与 sqlite 档完全一致，失败归因不混淆；
- G3：方言差异逐条处置——每个红 = 修复 commit 或显式登记「已接受差异」，**禁止用 skip 污染套件**；
- G4：PR 门禁零变化（时长、口径、稳定性）。

**非目标（明确不做）**

- ❌ PR 门禁换成 PG（速度/稳定性/成本三输，sqlite 保留为快反馈）；
- ❌ E2E 容器档（E2E 价值在确定性重置，保持现状）；
- ❌ MySQL 档（双库支持为 ADR 触发制：出现真实 MySQL 部署需求再评估）;
- ❌ 阶段一不引入真实 Redis（见 §四 阶段二）。

## 三、方案设计

### 3.1 `tests/settings_pg.py`（单变量换库）

沿用 `settings_loadtest.py` 的「前置 Config 注入 + star-import」形态（该模块不可放 `server/settings/` 包内，父包 `__init__` 会强制加载 config.yml）：

- `DB_ENGINE=postgresql`（已钉）、`DB_HOST/DB_PORT/DB_DATABASE/DB_USER/DB_PASSWORD` 指向 service container / 本地容器；
- **xdist 每 worker 独立库**：`DB_DATABASE = f"xadmin_pgtest_{PYTEST_XDIST_WORKER}"`（settings 按 worker 进程各导入一次，天然隔离；pytest-django 自动建 `test_<库名>` 并跑迁移）——**禁止共享库**，xdist 共享文件库的历史 flaky 见 `tests/settings_test.py:35-38` 注释（2026-09-08 教训）；
- 其余（`CACHES=FakeRedisCache`、内存 channel、eager celery、`MD5PasswordHasher`）star-import `settings_test` 继承，不重写；
- 连接池走 base settings 的 `DB_POOL_ENABLED` 分支真实生效（`min_size/max_size` 用默认值即可）——这本身就是被测面（§一.3）。

### 3.2 `.github/workflows/test-nightly-pg.yml`

- 触发：`schedule`（cron 每日一次，UTC 18:30 ≈ 北京 02:30，错开 PR 高峰）+ `workflow_dispatch`（手动/分诊后补跑）；
- `services:` 块挂 `postgres:17`（`POSTGRES_USER/PASSWORD/DB`，健康检查 `pg_isready`）——大版本与生产/loadtest 对齐（17.11 / `registry...nineaiyu/postgres:17.11` 同源）；
- 步骤与 `test.yml` 同构（uv sync → `uv lock --check`），测试命令：`DJANGO_SETTINGS_MODULE=tests.settings_pg uv run --no-sync pytest -n auto`——**不带 `--cov`**（夜间档目标是方言正确性，coverage 拦路 ~20-30% 时长且口径由 PR 门禁负责）；
- 追加一步 `manage.py check --database default`（与 test.yml 同款，在真库上跑）；
- 失败处理：nightly 红**不阻断**任何 PR；失败即 GitHub 邮件通知 + 次日窗口分诊（修复 commit 或登记），台账记在本文档 §五。

### 3.3 本地等价跑法（runbook，与 loadtest 容器端口错开）

```bash
docker run -d --name xadmin-pgtest-pg \
  -e POSTGRES_USER=server -e POSTGRES_PASSWORD=pgtest -e POSTGRES_DB=xadmin_pgtest \
  -p 127.0.0.1:55433:5432 postgres:17
cd xadmin-server
DJANGO_SETTINGS_MODULE=tests.settings_pg \
  DB_HOST=127.0.0.1 DB_PORT=55433 DB_USER=server DB_PASSWORD=pgtest \
  .venv/bin/python -m pytest -n auto
```

（端口 55433 与 loadtest 的 55432 错开，两者可共存。）

## 四、已知 sqlite↔PG 差异与首轮分诊清单

首轮 nightly 预计暴露（按风险排序，逐条处置）：

| # | 差异面 | 涉及 | 预判 |
|---|---|---|---|
| 1 | 迁移真实执行：GinIndex / 约束命名截断 / `django.contrib.postgres` DDL | 全部 migration | `check --database` 已兜过检查层，迁移层首测 |
| 2 | JSON 路径列与 JSON 字段函数（`Substr` 分桶、路径查询） | dataset（ADR-069/070/071） | sqlite 与 PG 函数库不同，可能需要 `django.contrib.postgres` 查询表达式分支 |
| 3 | `icontains`/`search` 大小写语义（sqlite LIKE 不区分大小写；PG 走 `UPPER()`/`ILIKE`） | filterset 断言 | 多数断言两侧等价，重点盯依赖「sqlite 宽松匹配」的断言 |
| 4 | 事务/锁并发语义（PG 行锁 vs sqlite 写串行化） | 单 worker 内低并发，风险低 | xdist 单库隔离后基本规避 |
| 5 | DateTimeField 精度与时区回读 | 时间断言 | 微秒精度两侧一致，tz 回读口径需核对 |
| 6 | psycopg3 连接池真实创建（`OPTIONS.pool` 分支） | `server/settings/base.py:238-260` | 首次被测试真实触发；池参数缺省即可 |
| 7 | 自增主键回填 / UUID pk 行为差异 | 少量断言 | 预计低风险 |

分诊纪律：修复 = 独立 commit + 守护；登记 = 在 §五台账写明「差异 + 判定 + 依据」；**两者之外不允许第三种状态**（skip/删除断言视为逃避）。

## 五、分诊台账（滚动登记）

| 日期 | 用例 | 差异描述 | 处置 |
|------|------|----------|------|
| — | — | 首轮 nightly 尚未执行 | — |

## 六、阶段与工作量

| 阶段 | 内容 | 工作量 | 状态 |
|------|------|--------|------|
| 一 | `tests/settings_pg.py` + `test-nightly-pg.yml` + 首轮跑通与分诊 | 0.5–1 人日 | 待启动 |
| 二（触发制） | 真实 Redis service container 替换 FakeRedis（TTL/INCR/锁语义）；**前置**：xdist 跨 worker 共享 Redis 需按 worker 隔离 db/键前缀，限流类用例需重审 | 1 人日 | 触发条件：出现缓存语义类缺陷逃逸到 loadtest/生产 |
| 三 | — 不做 — | — | MySQL 档 / E2E 容器档（见 §二 非目标） |

## 七、验收

1. workflow 落库且 schedule 首跑成功；
2. **连续 3 次 nightly 全绿**（或差异全部按 §四 纪律处置完毕）；
3. PR 门禁（`test.yml`）时长与口径零变化；
4. 本文档 §五 台账与实际状态一致。

## 八、成本

- GHA 私有仓：单次 ~15–25 min（PG 建库迁移 × N worker + 真 SQL 执行慢于内存 sqlite），每日一次 ≈ **450–750 min/月**，位于 2000 min/月免费额度内；`uv` 缓存与 test.yml 共享命中。
- 本地：runbook 单命令，容器与 loadtest 端口错开可共存。

## 九、关联

- 版本对齐：`postgres:17` ≙ loadtest §3.1 与生产 compose 的 `postgres:17.11`；psycopg3 依赖已在 `requirements.txt:94-96`（无需新增安装面）；
- 与 `perf.yml`（k6 基线）分工：nightly 管**正确性**，perf 管**性能口径**；
- 台账入口：[NEXT-DEV-PLAN.md](../../../NEXT-DEV-PLAN.md) §三 B **F6**；
- 关联登记：`docs/ops/performance-baseline.md` §九（2026-10-01 fields 深度定位——本次立项的动因之一，ASGI/真库行为差异在真实环境下才可见）。
