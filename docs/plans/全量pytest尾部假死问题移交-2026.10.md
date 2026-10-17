# 全量 pytest 尾部假死问题移交说明（2026-10-17）

> **移交对象**：接手的 AI / 开发者。本文记录「全量 pytest 跑到 96~98% 假死」问题的
> 完整调查链、已排除假设与下一步方向，目的是**不复跑已做过的排查**。
> 台账口径：本文是一次性问题排查记录，结论回填后按「已完结一次性台账」移入 archive/。

## 1. 一句话现状

后端全量测试（`cd xadmin-server && uv run pytest -n auto`，6221 个用例）在机器 A
（开发机，18 核 / 63G 内存 / Docker Desktop）上**必然在 96~98% 处假死**：所有 worker
休眠、master 在 `DSession.loop_once` 等事件、永不结束。CI（4 核独立 runners）不受影响。
**数据库与测试套件本身均健康**（见 §4），问题在「尾部阶段 + 本机资源条件」的交叉点。

## 2. 可观测现象

- 进度条停在 96~98%（约剩 250 个用例），日志字节数不再增长；
- 4/8/2 worker 三种并行度均复现；单 worker（`-n 1`）也复现（停在 19%）；
- 假死时 `pg_stat_activity` 有 1~2 个 active 查询，均为
  `SELECT system_modellabelfield.id ... JOIN system_fieldpermission_field ...`，
  单条显示 dur≈1s，但**同一条查询手动 EXPLAIN ANALYZE 实测 0.6ms**（见 §4.1）；
- worker 主线程栈（`sample` + SIGABRT faulthandler 双取）固定在：
  ```
  load_init_json.py:194 _sync_builtin_roles_after_seed
    → identity/builtin.py:135 sync_builtin_roles
    → identity/builtin.py:150 _fill_builtin_role_fields
    → system/services/permission_sync/apply.py:188 fill_field_permissions
    → related_descriptors.py:1309 set（M2M .set()）
    → psycopg/connection.py:484 wait（等查询结果）
  ```
  调用方是 `tests/unit/system/test_dump_split_apps.py:30` 的 `seeded` fixture
  （该 fixture 跑完整 `load_init_json`）；
- 假死 worker 进程内常驻线程：`pool-2-scheduler` / `pool-2-worker-N`
  （psycopg_pool 后台线程）+ `health-probe_N` + `lock-watchdog:*`；
- 杀掉任一 worker → 运行立即收尾：**5989 passed / 2 skipped / 230 errors**
  （230 errors 全部是被杀 worker 的未跑用例，2 failed 见 §4.3）。

## 3. 环境与时间线（git log 实锤）

- 机器：18 核 / 63G 内存 / macOS / Docker Desktop；测试栈 `compose.test.yml`
  （PG 17 于 55433、Redis 8 于 56379，`tests/settings_real.py` 真环境档）。
- `2026-10-17 15:48:44` 提交 `b0ec67bb`：并行度 15→8，提交信息记载
  **「实测 8 worker 全量 6219 passed / 5分23秒稳定通过」**——旧种子体系基线。
- `2026-10-17 15:49:07` 提交 `c82452f9`（种子按 app 分层，149 文件 +82812/-77500）：
  `load_init_json` 重写为「发现→规划→校验→装配」，**新增 65 个种子测试**
  （test_seed_plan / test_seed_ownership / test_seed_references / test_seed_grants /
  test_dump_split_apps 等），每个都跑完整 `load_init_json`。
- `16:05` 起本机全量（= 分层后第一次全量）即复现假死；此后多次复现。
- 调查期间并发/连续全量跑批 6~7 次，机器进入内存压缩 19G、PG checkpoint 写入
  从 <1s 恶化到 203~270s 的状态（`docker logs xadmin-test-pg` 有
  `checkpoint complete: ... write=269.922 s` 记录），**放大了尾部缓慢**；
  资源恢复后（内存 65% 空闲、磁盘 93ms/48MB）尾部仍慢，故资源衰竭是放大器不是根因。

## 4. 证据链（按调查顺序，均附复现命令）

### 4.1 数据库无罪

- 卡住现场同款查询 `EXPLAIN (analyze, buffers)`：`actual time=0.615..0.615`（**毫秒**），
  rows=0。所谓「每条查询 1 秒」是 pg_stat_activity 的 wall time，不是执行时间。
- 无锁等待：`pg_blocking_pids` / `pg_locks ... where not granted` 均为空。
- 无死元组累积：`VACUUM ANALYZE` 前后 `n_dead_tup=0`、查询耗时不变。
- 磁盘健康：容器内 `dd 48MB` = 93ms。
- 结论：**1 秒耗时发生在 Python 客户端侧（GIL 争抢 / 调度延迟），不在 PG**。

### 4.2 xdist 机制无罪（结构正常）

- master 栈 = `queue.get()` in `loop_once`（等 worker 事件）；worker 主线程在
  execnet 引导的 `read()` 或 `gen_send_ex2` 等锁——**双向互等**，但这是「worker 慢」
  的果，不是 xdist 调度器 bug 的因。
- 非 worker 崩溃重启：卡住时 worker PID 不变（`ps --ppid` 核实）。
- 非连接池耗尽假死：`psycopg_pool` 后台线程虽在，但卡住栈是「等查询结果」不是
  「等池槽位」；且 `probe_db` 已有正确的连接归还逻辑（`common/utils/health.py`）。

### 4.3 套件基本健康

- 完整一轮（杀 worker 收尾）：5989 passed / 2 skipped。
- 2 failed：`test_doc_paths_reachable`（调查者移动空壳种子文件导致，已修文档）+ 
  `test_load_init_json_fills_builtin_role_menus_and_fields`（杀 worker 连带
  AdminShutdown），均非真实回归。
- 230 errors：被杀 worker 的未跑用例，全为连带。

### 4.4 调查者改动无罪（可 git diff 审查）

本轮仅 4 个文件：`loadjson/systemconfig.json` 与 `loadjson/fieldpermission.json`
移入 `_delete/`（各为 3 字节 `[]` 空壳，装载贡献 0 行）；`pytest.ini` 加
`faulthandler_timeout=600`；`docs/architecture/handbook-backend.md` 一行路径修正；
前端 `preferenceFieldSync.spec.ts` 种子发现改跨层（修复 93 个因种子分层导致的
路径腐烂失败，已复跑 93/93 绿）。

## 5. 根因假设（按可能性排序，待验证）

1. **`c82452f9` 引入的尾部工作量倍增（主假设）**：65 个新种子测试每个跑完整
   `load_init_json` → `sync_builtin_roles` → `fill_field_permissions`，后者对每个
   (role, menu) 缺口执行 M2M `.set()`（DELETE+INSERT+SELECT 三连）。分层前该路径
   负载小；分层后尾部阶段 Python 侧工作量数量级上升，在 GIL 争抢
   （psycopg_pool 后台线程 + health-probe 池 + asgiref 执行器常驻）下被进一步放大，
   单查询 wall time ~1s × 数百组合 = 尾部数分钟级爬行，表现为假死。
2. **本机资源条件（放大器）**：Docker VM CPU 配额 + 18 核超售 + 内存压缩，
   使 Python 进程调度延迟劣化；CI 4 核独立 runners 无此条件故不复现。
3. **连接/事务泄漏累积（次要，未证实）**：`tests/conftest.py::_release_pool_connections`
   只在 session 结束归还后台线程连接；若某测试中途泄漏持锁连接，会拖慢后续写路径
   （本次未在 pg_locks 抓到实证，暂列观察项）。

## 6. 复现步骤（机器 A）

```bash
cd xadmin-server
uv run pytest -n 2 -q            # 或 -n 8；-n 1 亦可复现（停在 19%）
# 观察：进度停 96~98% 后不再推进；ps 见 worker 休眠；docker stats 见 PG 100%+
```

## 7. 下一步诊断（建议按序，勿再全量硬跑）

1. **计时种子测试组，量化回归**（最关键，先做这个）：
   ```bash
   uv run pytest tests/unit/system/test_dump_split_apps.py tests/unit/system/test_seed_plan.py \
     tests/unit/system/test_seed_ownership.py tests/unit/system/test_seed_grants.py \
     tests/unit/system/test_seed_references.py tests/unit/system/test_builtin_seed.py \
     -n 2 --durations=15 -q
   ```
   对照 `b0ec67bb` 的 5分23秒基线判断尾部增量；若该组 >3 分钟即实锤回归。
2. **单测 `fill_field_permissions` 热点**：在 `apply.py:188` 的 `.set()` 循环内加计时
   （或临时 `--durations` + cProfile），确认 M2M 三连是否为瓶颈；若是，考虑
   `bulk_create`/`set()` 合并或按 menu 批量。
3. **验证 GIL 假设**：假死现场对 worker 做两次间隔 10s 的 `sample <pid>`，
   对比主线程在 `gen_send_ex2`/`lock_PyThread_acquire_lock` 的驻留比例；
   或用 `faulthandler_timeout`（已入 pytest.ini，600s）等它自动打栈。
4. **若定位为 Python 侧调度**：评估 ① 种子测试改用 `--create-db` 隔库；
   ② `conftest` 把 `_release_pool_connections` 改为每 N 个测试执行一次（非阻塞提交）；
   ③ 给测试库连接加 `lock_timeout`/`statement_timeout` 兜底把假死变可见失败
   （口径参照 `tests/settings_real.py` 既有「即时失败」注释）。

## 8. 明确不要做的事（已排除，勿重复）

- 不要再以「xdist worker 数」为方向（8/4/2/1 全复现，pytest.ini 注释里
  「15 worker 卡死」的旧归因已证伪，已改写该注释）。
- 不要再查数据库锁/死元组/磁盘（§4.1 已逐一排除）。
- 不要在本机连续全量硬跑（资源衰竭会放大问题、污染观测；每次跑前确认
  `memory_pressure` free% > 50 且无其他 pytest 残留）。

## 9. 相关提交与文件

- 提交：`b0ec67bb`（并行度 8，含被证伪的旧归因注释）、`c82452f9`（种子分层，主嫌疑）。
- 代码热点：`system/services/permission_sync/apply.py`（fill_field_permissions）、
  `identity/builtin.py`（sync_builtin_roles）、
  `system/management/commands/load_init_json.py`、`tests/conftest.py`（连接归还）。
- 调查者本轮改动（未提交，待人工 review）：`pytest.ini`、
  `docs/architecture/handbook-backend.md`、`_delete/seed-empty-husks-20261017/`、
  `xadmin-client/src/utils/__tests__/preferenceFieldSync.spec.ts`。
