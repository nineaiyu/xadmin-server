# 备份恢复演练报告（T5.4，2026-09-07）

> 关联：半年规划 P5/T5.4、TD-21（P1 已落最低可用备份）；脚本：`utils/db_backup.sh` / `utils/db_restore.sh`；
> 结论：**恢复 RTO ≈ 1 秒（目标 ≤30min），52 表行数全部一致，演练通过**。

## 一、演练范围与方法

| 项 | 内容 |
|----|------|
| 备份任务 | compose `db-backup` 服务（postgres:16.8 容器内 pg_dump + gzip，启动即备份 + 每 86400s 一次，滚动保留 7 天，落 `../xadmin-db-backups/`） |
| 触发方式 | `docker restart xadmin-db-backup`（走真实任务链路，非手工 pg_dump） |
| 恢复目标 | 同实例独立验证库 `xadmin_restore_test`（db_restore.sh 支持 `[目标库]` 参数，先 DROP 再 CREATE，不触碰源库） |
| 一致性验证 | ① 表数（information_schema）；② 全部 52 表逐表行数对比；③ 关键业务表抽查（system_userinfo / system_menu / django_migrations） |
| 媒体文件 | `data/upload` tar 打包流程验证（dev 环境仅 .gitkeep；生产同路径） |
| 数据规模 | 52 表 / 备份包 544-548KB（dev 数据量小，生产需按真实规模重估 RTO） |

## 二、结果

| 指标 | 实测 | 目标 | 结论 |
|------|------|------|------|
| 备份耗时 | <1s（00:31:36 start → done，544KB） | — | ✓ |
| 恢复耗时（RTO） | **0.88s**（db_restore.sh 全流程，含 DROP/CREATE 库） | ≤30min | ✓ |
| 表结构一致性 | 52/52 表恢复成功 | 全部 | ✓ |
| 行一致性（紧凑闭环：备份→恢复→立即逐表对比） | **52 表 0 不一致** | 0 | ✓ |
| 媒体文件备份 | tar 流程验证通过 | — | ✓ |

## 三、过程中的发现（记录存档）

1. **运行时表的自然漂移不是恢复缺陷**：首次对比中 `common_monitor`（+6）与
   `django_celery_results_taskresult`（+6）不一致，为备份快照后源库持续写入所致；
   紧凑闭环（备份→恢复→对比间隔 ~1s）0 不一致，证实恢复保真。
2. **pg_dump 默认 COPY 格式恢复极快**：548KB 包 0.88s 完成全库重建；生产库变大后
   RTO 按同口径重测即可（脚本无需变更）。

## 四、遗留缺口与建议（后续排期）

| # | 缺口 | 建议 | 建议排期 |
|---|------|------|---------|
| 1 | **无异地副本**：备份文件落在与源库同宿主机的卷上，磁盘故障仍可能同时丢失 | `rclone`/云对象存储同步 `xadmin-db-backups/`（每日） | 2027-01 窗口 |
| 2 | **db_backup.sh 不含媒体目录**：`data/upload` 仅手工 tar | 脚本加可选 `BACKUP_MEDIA=true`（tar data/upload 一并入卷） | 2027-01 窗口 |
| 3 | **RPO 最长 24h**：当前每日一备 | 缩短 interval（如 6h）或引入 WAL 归档（PITR）；按业务可接受丢失窗口决策 | 2027-01 窗口（与 ADR-005 Redis 拆分同窗口评审） |
| 4 | 恢复演练依赖宿主机 docker exec 路径 | deployment.md 增加「备份/恢复检查清单」（本次已补） | 已完成 |

## 五、部署检查清单（备份/恢复部分，同步至 ops/deployment.md）

- [ ] `db-backup` 容器 healthy 且 `xadmin-db-backups/` 有当日 `.sql.gz`
- [ ] 恢复演练：`sh utils/db_restore.sh <备份包> <验证库名>` 后逐表行数核对，演练完 DROP 验证库
- [ ] 确认验证库恢复**不得指向 `xadmin`**（db_restore.sh 会先 DROP 目标库）
- [ ] `data/upload` 媒体目录是否需要纳入当日备份（当前需手工 tar）
- [ ] 异地副本策略是否已启用（当前未启用）
