# 审计日志冷归档（P-5）

操作日志 / 登录日志是单库单表、过期物理删除（全量 180 天 / 错误 365 天）——过期即失忆。
本机制为过期日志提供**冷归档**：过期前按月导出 `JSONL.gz`（含校验和与清单），
物理删除变为「归档后清理」，支撑「半年前谁改的」类追溯与合规留存。

## 1. 机制：归档水位驱动清理（删必已归档）

- **归档窗口**：`月末 <= now - 保留期` 的整月（含成功与错误日志）各导出为一份
  `<model>-<YYYY-MM>.jsonl.gz`；同月已归档且校验通过时幂等跳过；
- **清理边界 = 归档水位**：从系统最早数据起「连续已归档且整月超期」的边界
  （与保留窗口取较小值）。**未归档的数据永不删除**；「未整月超期」的边界月不归档、
  不删除——最多多留一个月，保留期是下限；
- **分层留存语义不变**：成功日志按 `OPERATION_LOG_RETENTION_DAYS`（默认 180 天）、
  错误日志按 `OPERATION_LOG_ERROR_RETENTION_DAYS`（默认 365 天）；
- **登录日志已纳入自动面**：按 `LOGIN_LOG_RETENTION_DAYS`（默认 365 天；0 = 不自动清理）
  走同一条「归档 → 水位驱动清理」链路（同属每日 02:02 任务）；
- **归档失败 = 跳过清理**（保数据优先）：清理任务先归档后删除，归档抛错时任务在
  celery 记录中可见失败，不会「未归档即删除」；
- **产物三件套**：`*.jsonl.gz`（未压缩 JSONL 的 sha256 为校验基准）+ `*.sha256`（sidecar）
  + `*.manifest.json`（行数 / 时间范围 / 大小 / 校验和 / 归档时间）。

归档目录：`settings.LOG_ARCHIVE_DIR`（默认 `DATA_DIR/log_archive`），
环境变量 `LOG_ARCHIVE_DIR` 可覆盖（如指向异地同步目录）。

定时任务：`auto_clean_operation_job`（每日 02:02）已改为「先归档后清理」，无需额外配置。

## 2. 日常操作（`manage.py log_archive`）

```shell
python manage.py log_archive                     # 归档全部「整月已超保留期」的月份（幂等）
python manage.py log_archive --dry-run           # 只统计将归档的月份与行数
python manage.py log_archive --month 2026-02     # 归档指定月份（可重复）
python manage.py log_archive --model login --month 2026-01   # 登录日志指定月份归档
python manage.py log_archive --model login --prune           # 登录日志水位驱动清理
python manage.py log_archive --list              # 归档清单
python manage.py log_archive --verify            # 校验完整性（sha256 + 行数；失败退出码 1）
python manage.py log_archive --prune --dry-run   # 查看当前归档水位与将删除行数
python manage.py log_archive --prune             # 执行水位驱动清理（删必已归档）
```

登录日志与操作日志共用同一套归档 / 校验 / 恢复 / 清理命令，保留期分别为
`LOGIN_LOG_RETENTION_DAYS`（默认 365）与 `OPERATION_LOG_RETENTION_DAYS`（默认 180）；
任一保留期置 0 即该对象只支持手动归档、不自动清理。

## 3. 与备份链路的关系（重要）

- **归档 ≠ 数据库备份**：数据库离线包 / PITR（[pitr.md](pitr.md)）与审计归档分属两层，互不替代；
  前者恢复「整库任意时间点」，后者回答「某条审计记录当年是什么样」；
- **纳入异地同步**：把 `LOG_ARCHIVE_DIR` 指向（或定期 rsync）异地副本目录即可复用既有同步链路；
- **不要直接放备份卷根目录**：`utils/db_backup.sh` 的 `prune_local` 会按
  `*.sql.gz / *.media.tar.gz / *.sha256` 后缀清理超保留期文件（当前不递归区分目录），
  归档的 `.sha256` sidecar 会被误删——如必须同卷，请放子目录并确认 `.sha256` 不受影响；
- 归档目录属主需与容器用户（uid 1001）一致，否则归档写入失败 → 清理跳过（表增长）。

## 4. 恢复查询（离线，不落库）

```shell
# 按月流式查询（默认 jsonl；limit 0 = 不限）
python manage.py log_archive --restore-range 2026-02 --grep xadmin --limit 50
python manage.py log_archive --restore-range 2026-02 --format table --limit 100
```

不建临时表、不导入数据库：直接流式读取归档文件（`grep` 为原始 JSON 行子串匹配），
用完即走；适合排障与「历史追溯」类一次性查询。

## 5. 校验与演练

- `--verify`：逐个归档重算 sha256 与行数并与清单比对，失败退出码 1（可入巡检）；
- 季度演练池场景「**从冷归档恢复查询**」：选取一个已归档月份 →
  `--verify` 通过 → `--restore-range` 能查到已知记录（含按 `--grep` 定位特定操作）；
- 演练记录追加到本节下方。

| 日期 | 操作人 | 归档月份 | 校验 | 查询验证 | 结论 |
|------|--------|----------|------|----------|------|
| 2026-09-23 | 集成演练（`tests/integration/system/test_log_archive.py::TestColdArchiveRestoreDrill`） | `operation`（400 天前，含成功 + 错误日志；归档目录为临时目录） | `log_archive --verify` 通过（sha256 + 行数一致）；向归档追加一行后 `--verify` 失败且退出码 1 | `log_archive --restore-range <月> --grep archive-test --limit 3` 命中 3 行（流式读取，不落库） | 通过：归档 → 校验 → 恢复查询 → 篡改检测全链路成立 |
| 2026-09-23 | 集成演练（`TestLoginLogArchive::test_login_verify_restore_prune_drill`） | `login`（400 天前） | `verify_archive` 通过（`ok=True`，rows=3） | `read_restore_rows(..., limit=2)` 命中 2 行；随后水位驱动清理删除 3 行且再次清理幂等 | 通过：登录日志自动面（归档/校验/恢复/清理）与操作日志同口径 |

> 生产环境首次执行时请按上表口径补一行真实归档月份与操作人（本表先以集成演练背书全链路）。

## 6. 排障

| 现象 | 原因与处置 |
|------|------------|
| 日志表不再清理、持续增长 | 归档失败（磁盘满 / 权限 / 目录不可写）→ 查 celery 任务日志与归档目录；修复后重跑 `log_archive` 与 `log_archive --prune` |
| `--prune --dry-run` 显示水位很旧 | 正常保护：边界月未整月超期、或中间月份有数据但未归档（洞）——执行 `log_archive` 补齐即可推进 |
| 归档目录搬迁后不清理 | 水位按现有清单计算，搬迁后清单缺失 → 水位回退（安全方向）；用 `--list` 确认清单与文件成对存在 |
| 磁盘占用增长 | 归档是压缩 JSONL（约为原始行文本的 10-20%）；如仍需控制，按业务口径调整保留期或归档目录的同步/保留策略 |
