# PITR（WAL 归档）方案与演练

> 候选池「PITR（WAL 归档）」交付物：备份 RPO 6h（pg_dump 逻辑备份）→ **分钟级**。
> 本文给出现状、启用步骤、成本口径、演练流程与回滚；**默认不启用**（需发布窗口 + 成本确认）。
> 相关：[deployment.md](deployment.md)（备份总览）、[backup-drill-*.md](backup-drill-2027-03.md)（既有演练口径）、`utils/pitr_drill.sh`（链路检查助手）。

## 1. 现状与目标

| 维度 | 现状（逻辑备份） | PITR 后 |
|------|------------------|---------|
| 恢复点 | 最近一次 pg_dump（RPO ≤ 6h） | 任意时间点（RPO ≈ archive_timeout，建议 60s） |
| 恢复粒度 | 整库 | 整库 + 时间点（可回放到误操作前 1 分钟） |
| 成本 | 备份盘 + 异地副本 | 额外 WAL 归档存量（约 16MB/段 × 写入量，典型 < 20GB/月） |
| 风险 | — | 归档链路故障需告警；归档目录不可与数据同盘（同盘丢失无意义） |

## 2. 启用步骤（发布窗口执行）

### 2.1 PostgreSQL 侧（`xadmin-postgresql/docker-compose.yml` 的 postgresql 服务）

```yaml
    command:
      - postgres
      - -c
      - max_connections=200
      # —— PITR（按需启用；归档目录必须是独立卷/独立盘）——
      - -c
      - archive_mode=on
      - -c
      - archive_timeout=60
      - -c
      - archive_command=test ! -f /var/lib/postgresql/archive/%f && cp %p /var/lib/postgresql/archive/%f
    volumes:
      - ./data:/var/lib/postgresql/data
      - ${PG_ARCHIVE_HOST_DIR:-./archive}:/var/lib/postgresql/archive   # 新增：独立卷
```

要点：

1. `archive_mode=on` **需要重启** PostgreSQL 才生效（`archive_command` 可 reload，`archive_mode` 不可）；
2. `archive_command` 用 `test ! -f … && cp` 幂等写法（重复归档同段不报错）；
3. **归档目录必须与数据目录分盘/分卷**（同盘故障时两者同失，归档失去意义）；
4. 归档目录纳入既有备份同步链路（异地副本），保留期建议 ≥ 14 天。

### 2.2 告警（复用既有通道）

`utils/db_backup.sh` 的 `send_alert` 已覆盖 pg_dump/校验/同步/媒体四处；
PITR 增加一条**归档滞后检查**（建议加进 db-backup 容器的健康检查脚本）：

```bash
# 归档滞后 = 最新归档文件 mtime 距今秒数；> 600s 告警（含"归档目录不可写"场景）
find "${PG_ARCHIVE_DIR}" -name '0000*' -mmin -10 | head -1 | grep -q . || echo "WAL archive stalled" | send_alert
```

## 3. 时间点回放演练（`utils/pitr_drill.sh`）

| 步骤 | 命令/动作 |
|------|-----------|
| 1. 链路检查 | `./utils/pitr_drill.sh`（只读：打印 archive_mode / 归档文件数 / 最新归档时间） |
| 2. 选时间点 | 记录 `2026-09-15 12:00:00`（先制造一条可辨识的测试数据再"误删"） |
| 3. 隔离恢复 | 在**临时容器 + 副本数据目录**（切勿覆盖生产数据目录）执行基础备份恢复 + `recovery_target_time` |
| 4. 校验 | `psql -c "select count(*) …"` 对比预期；确认误删数据回来了、其后的正常数据未被回退（按时间点语义） |
| 5. 记录 | 演练结果追加到本文件 §5 记录表（RTO/RPO 实测） |

标准恢复配置（临时实例）：

```
restore_command = 'cp /var/lib/postgresql/archive/%f %p'
recovery_target_time = '2026-09-15 12:00:00+08'
recovery_target_action = 'promote'
```

## 4. 回滚

- 关闭：`archive_mode=off` + 重启（归档文件保留可继续用于历史回放）；
- 归档目录占满：先扩容/清理早于保留期的段，再重启（PG 会重试待归档段，不会丢段）。

## 5. 演练记录（逐次追加）

| 日期 | 操作人 | 目标时间点 | RTO（到可查询） | RPO（实际丢失窗口） | 结论 |
|------|--------|-----------|-----------------|--------------------|------|
| （待发布窗口启用后首次执行） | — | — | — | — | — |

## 6. 成本评估口径

- 归档存量 ≈ 日写入 WAL 量 × 保留天数（PG 默认 16MB/段；低写入场景每天 < 100MB）；
- 独立盘/卷按保留期容量规划（建议 3 个月余量）；
- 演练频次：与季度备份演练同一周期（`backup-drill-reminder.yml` 提醒 workflow 复用）。
