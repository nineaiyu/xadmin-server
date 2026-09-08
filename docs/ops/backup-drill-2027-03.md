# 备份恢复演练报告（异地副本收口，2026-09-08 执行 / 2027.03 规划 N1）

> 关联：下期规划 N1 / 遗留缺口 L1（备份三项）、上次演练 `docs/ops/backup-drill-2026-09.md`
> 脚本：`utils/db_backup.sh`（备份）、`utils/db_restore.sh`（恢复）、`utils/backup_drill.sh`（一键演练）
> 结论：**L1 三项全部闭环**——异地副本已落并 sha256 校验一致、媒体目录入包、RPO 24h → 6h；
> 恢复 RTO **0.89s**，53 表逐行一致，演练通过。

## 一、本次关闭的缺口

| # | 缺口（上期遗留） | 处置 | 落点 |
|---|---|---|---|
| 1 | 无异地副本（备份与源库同盘） | 备份脚本支持 `BACKUP_REMOTE_TYPE=local\|rsync\|rclone`，逐个包同步并带 `.sha256` 校验和；`local` 模式按天滚动清理 | `utils/db_backup.sh` → `sync_remote()` / `prune_remote()` |
| 2 | 备份不含媒体目录（`data/upload` 需手工 tar） | `BACKUP_MEDIA=true` + `MEDIA_DIR=/media`（compose 只读挂载 `./data/upload`），产出 `<同名>.media.tar.gz` | `utils/db_backup.sh` → `backup_media()` |
| 3 | RPO 最长 24h（每日一备） | `BACKUP_INTERVAL` 默认 86400 → **21600**（6h） | `utils/db_backup.sh` + `docker-compose.yml` |

配套能力：

- **落盘即校验**：`gzip -t` + 非空检查，损坏包不落正式名、不进异地
- **校验和 sidecar**：每个包附带 `.sha256`，异地副本可直接 `sha256sum -c` 验真
- **单次模式**：`BACKUP_ONCE=1` 跑一轮即退出，失败返回退出码 1（演练 / 外部 cron 可感知）
- **一键演练**：`utils/backup_drill.sh` 串起「触发真实备份 → 异地校验 → 恢复验证库 → 逐表行数对比 → 输出报告」

## 二、演练范围与方法

| 项 | 内容 |
|---|---|
| 备份触发 | `docker exec -e BACKUP_ONCE=1 xadmin-db-backup bash /utils/db_backup.sh`（走真实任务链路，非手工 pg_dump） |
| 异地副本 | `BACKUP_REMOTE_TYPE=local`，容器 `/remote` 挂载宿主机 `xadmin-db-backups-remote/` |
| 媒体目录 | `BACKUP_MEDIA=true`，`./data/upload` 只读挂载为 `/media` |
| 恢复目标 | 同实例独立验证库 `xadmin_restore_test`（先 DROP 再 CREATE，演练后 DROP） |
| 一致性验证 | 源库 vs 验证库全表行数 diff（排除 `pg_catalog`/`information_schema`） |
| 命令 | `BACKUP_REMOTE_DIR=../xadmin-db-backups-remote bash utils/backup_drill.sh` |

## 三、结果（2026-09-08 18:37）

| 项 | 结果 |
|---|---|
| 触发备份链路 | PASS |
| 备份包 sha256 自校验 | PASS |
| 异地副本存在且一致 | PASS（本地与异地 sha256 一致） |
| 恢复耗时 RTO | **0.89s**（目标 ≤30min） |
| 逐表行数一致 | PASS（**53 表 0 不一致**） |

- 备份包：`xadmin_20260908_183706.sql.gz`（228K）+ 同名 `.media.tar.gz`
- 全流程耗时（备份→异地→恢复→对比→清理）：1.73s

## 四、过程中的发现

1. **逐表对比必须连对库**：初版用 `-d postgres` 取表清单，只能看到 postgres 库的表
   （结果 0 表 → 空 diff → 假通过）。已改为在「被统计的库自身」上取 `pg_tables`，
   并新增「0 张表即判 FAIL」的护栏，避免静默假绿。
2. **媒体包 4KB 属正常**：dev 环境 `data/upload` 仅 `.gitkeep`，tar 空目录也有头信息；
   生产按真实附件量重估体积与耗时即可（脚本无需变更）。
3. **异地清理只对 `local` 生效**：`rsync`/`rclone` 远端不可控，误删代价高，交由远端生命周期
   策略（对象存储的 lifecycle rule）处理，脚本只提示不代删。

## 五、配置速查

```bash
# docker-compose.yml（db-backup 服务，均有默认值）
BACKUP_INTERVAL: ${BACKUP_INTERVAL:-21600}        # RPO 6h
BACKUP_MEDIA: ${BACKUP_MEDIA:-true}               # 媒体目录入包
MEDIA_DIR: /media                                  # 只读挂载 ./data/upload
BACKUP_REMOTE_TYPE: ${BACKUP_REMOTE_TYPE:-}       # local | rsync | rclone | 空=关闭
BACKUP_REMOTE_TARGET: ${BACKUP_REMOTE_TARGET:-}   # /remote | user@host:/path | oss:bucket
BACKUP_REMOTE_KEEP_DAYS: ${BACKUP_REMOTE_KEEP_DAYS:-7}
```

`rclone` 的凭据在宿主机 `rclone config` 侧配置（不入仓库）；`rsync` 走免密 SSH key。
本地验证异地副本：`BACKUP_REMOTE_TYPE=local BACKUP_REMOTE_TARGET=/remote docker compose up -d db-backup`。

## 六、仍存缺口（滚动）

| # | 缺口 | 说明/建议 |
|---|---|---|
| 1 | 无 PITR（WAL 归档） | RPO 6h 仍意味着最坏丢 6h 数据；需秒级 RPO 时再立项 WAL 归档 + 回放演练（含磁盘成本） |
| 2 | 异地副本为同盘目录（本次演练） | 演练用 `../xadmin-db-backups-remote` 仅验证链路；**生产必须指向独立磁盘/NFS/对象存储**，否则同盘故障仍双丢 |
| 3 | 异地副本无人值守告警 | 同步失败目前只落 `WARN` 日志，建议接入既有资源告警/日志采集 |
| 4 | 演练未常态化 | 建议 N5 阶段纳入季度演练（直接跑 `utils/backup_drill.sh` 即可，成本 ~2s） |
