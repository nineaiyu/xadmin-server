# PITR（WAL 归档）方案与演练

> 候选池「PITR（WAL 归档）」交付物：备份 RPO 6h（pg_dump 逻辑备份）→ **分钟级**。
> 本文给出现状、启用步骤、成本口径、演练流程与回滚。
> **✅ 2026-09-16 已启用**（发布窗口执行：archive_mode=on 重启 + gzip 压缩归档 + 归档滞后告警接线，
> 归档卷为同盘独立目录——单机退让决策与迁移条件见 §6）。
> 相关：[deployment.md](deployment.md)（备份总览）、[backup-drill-*.md](backup-drill-2027-03.md)（既有演练口径）、`utils/pitr_drill.sh`（链路检查助手）。

## 1. 现状与目标

| 维度 | 现状（逻辑备份） | PITR 后 |
|------|------------------|---------|
| 恢复点 | 最近一次 pg_dump（RPO ≤ 6h） | 任意时间点（RPO ≈ archive_timeout，建议 60s） |
| 恢复粒度 | 整库 | 整库 + 时间点（可回放到误操作前 1 分钟） |
| 成本 | 备份盘 + 异地副本 | 额外 WAL 归档存量（约 16MB/段 × 写入量，典型 < 20GB/月） |
| 风险 | — | 归档链路故障需告警；归档目录不可与数据同盘（同盘丢失无意义） |

## 2. 启用步骤（发布窗口执行）

### 2.1 PostgreSQL 侧（`xadmin-server/docker-compose.yml` 的 postgresql 服务，已按此启用）

```yaml
    command:
      - postgres
      - -c
      - max_connections=200
      - -c
      - archive_mode=on
      - -c
      - archive_timeout=60
      - -c
      - "archive_command=test ! -f /var/lib/postgresql/archive/%f.gz && gzip < %p > /var/lib/postgresql/archive/%f.gz"
    volumes:
      - ${VOLUME_DIR:-../}/xadmin-postgresql/data:/var/lib/postgresql/data
      - ${VOLUME_DIR:-../}/xadmin-postgresql/archive:/var/lib/postgresql/archive   # 独立卷
```

要点：

1. `archive_mode=on` **需要重启** PostgreSQL 才生效（`archive_command` 可 reload，`archive_mode` 不可）；
2. `archive_command` 用 `test ! -f … && …` 幂等写法（同段重归档不覆盖已有文件；
   覆盖被拒绝时归档器报失败，由 §2.2 巡检捕获）；
3. 归档采用 **gzip 压缩**：空段 16MB → KB 级，持续写入段约压 5~10 倍，
   磁盘成本几乎归零（实测：16MB 段压缩后 1.4MB，见 §6）；恢复端需 gunzip（见 §3）；
4. **归档目录必须与数据目录分盘/分卷**（同盘故障时两者同失，归档失去意义）。
   当前部署为**同盘独立目录**（单机退让，用户 2026-09-16 确认）：目录独立于数据目录，
   但与数据同盘；换独立盘后仅需把 compose 中该卷的**宿主路径**改为挂载点（容器内路径不变）；
5. 归档目录应纳入异地副本同步链路（`BACKUP_REMOTE_TYPE` 当前未启用，随异地副本一并规划），
   保留期建议 ≥ 14 天。

### 2.2 告警（已接线：`utils/db_backup.sh` 的 `check_wal_archive`）

`db-backup` 容器每轮备份后执行一次归档巡检（`archive_mode=on` 才生效），两个信号都
**不依赖库空闲**（空闲不切段，「最新归档时间」在静默期会误报，故不用它做判据）：

1. **归档器失败态**：`pg_stat_archiver.last_failed_time` 晚于 `last_archived_time`
   （目录不可写/磁盘满/权限 → 告警 `wal archive failing`）；
2. **待归档积压**：`pg_wal/archive_status` 有 `.ready` 滞留且最老者超过
   `WAL_ARCHIVE_STALL_SECONDS`（默认 600s）——`archive_command` 挂死或归档器停转时
   没有失败计数，只有积压能暴露（告警 `wal archive stalled`）。

告警经既有 `send_alert` 通道上报（`BACKUP_ALERT_URL`/`BACKUP_ALERT_TOKEN`，未配置时仅落 WARN 日志）。

## 3. 时间点回放演练（`utils/pitr_drill.sh`）

| 步骤 | 命令/动作 |
|------|-----------|
| 1. 链路检查 | `./utils/pitr_drill.sh`（只读：打印 archive_mode / 归档文件数 / 最新归档时间） |
| 2. 选时间点 | 记录 `2026-09-15 12:00:00`（先制造一条可辨识的测试数据再"误删"） |
| 3. 隔离恢复 | 在**临时容器 + 副本数据目录**（切勿覆盖生产数据目录）执行基础备份恢复 + `recovery_target_time` |
| 4. 校验 | `psql -c "select count(*) …"` 对比预期；确认误删数据回来了、其后的正常数据未被回退（按时间点语义） |
| 5. 记录 | 演练结果追加到本文件 §5 记录表（RTO/RPO 实测） |

标准恢复配置（临时实例；归档段为 gzip 压缩，restore 端解压，兼容未压缩段）：

```
restore_command = 'if test -f /var/lib/postgresql/archive/%f.gz; then gunzip < /var/lib/postgresql/archive/%f.gz > %p; else cp /var/lib/postgresql/archive/%f %p; fi'
recovery_target_time = '2026-09-16 12:00:00+08'
recovery_target_action = 'promote'
```

另需**与主库兼容的实例参数**（否则启动即中止，2026-09-16 演练实测）：
`max_connections` 必须 ≥ 主库值（本部署主库为 200），默认 100 时报
`FATAL: recovery aborted because of insufficient parameter settings`——
临时恢复实例以 `postgres -c max_connections=200` 启动（其余 `-c` 参数如 `archive_*` 恢复实例不需要）；
数据目录清空前先 `docker rm -f` 容器，避免旧实例持有目录。

## 4. 回滚

- 关闭：`archive_mode=off` + 重启（归档文件保留可继续用于历史回放）；
- 归档目录占满：先扩容/清理早于保留期的段，再重启（PG 会重试待归档段，不会丢段）。

## 5. 演练记录（逐次追加）

| 日期 | 操作人 | 目标时间点 | RTO（到可查询） | RPO（实际丢失窗口） | 结论 |
|------|--------|-----------|-----------------|--------------------|------|
| 2026-09-16 | 运维（用户授权） | 12:55:37+08（演练表 INSERT 后 / DROP 前） | 回放 <1s（数据量 21MB）；含解压基础备份约 30s | 0（目标点前数据零丢失；归档滞后 34s，RPO ≤ archive_timeout=60s） | ✅ 首次时间点回放演练通过：误删表与数据完整恢复、`pg_last_xact_replay_timestamp` 停在 INSERT 时刻（12:55:32）；对照恢复到 INSERT 前（12:55:25）确认表不存在，时间点语义精确 |
| 2026-09-16 | 演练备注 | — | 一次中止（参数不足） | — | 首次启动因 `max_connections=100 < 主库 200` 中止（见 §3 新增参数要求）；补参后断点续传成功。生产库演练表已 DROP 无残留、`pg_stat_archiver` 无失败记录 |

## 6. 成本评估口径（2026-09-16 实测口径）

- 实测基线：库 21MB；启用前 7 小时累计 WAL 写入 55MB（≈ 190MB/天业务数据量，轻负载）；
- gzip 压缩归档：空段 16MB → KB 级，含真实写入段 16MB → 1.4MB（实测首个归档段）；
  archive_timeout=60（RPO 1 分钟）下磁盘成本几乎归零，14 天保留预算 < 1GB；
- 若未来写入量级增长（WAL > 数 GB/天），复核方向：archive_timeout 提到 300s（RPO 5 分钟）
  或保留期下调；
- **独立盘迁移条件**：当前归档目录与数据同盘（单机退让，同盘故障两者同失的风险已标注）；
  换独立盘/挂载点后仅改 compose 中归档卷宿主路径，并把迁移完成日期回填本节；
- 演练频次：与季度备份演练同一周期（`backup-drill-reminder.yml` 提醒 workflow 复用）。
