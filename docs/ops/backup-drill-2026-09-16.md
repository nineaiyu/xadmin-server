# 备份恢复演练记录（2026-09-16，第四年度 2030-06 季度窗口）

> 季度备份演练（12/03/06/09 口径）。上次记录：[backup-drill-2027-03.md](backup-drill-2027-03.md)。
> 链路：`utils/db_backup.sh`（`xadmin-db-backup` 容器，`BACKUP_INTERVAL=6h`）→ `/backups`
> （`pg_dump | gzip` + sha256 sidecar + 媒体包 + `.latest_backup` 标记）。

## 一、产物校验

| 项 | 结果 |
|----|------|
| 最新备份 | `xadmin_20260916_190410.sql.gz`（612K）+ `media.tar.gz`（1.9M）+ 各自 `.sha256` |
| 生成间隔 | 13:04 → 19:04（**6h 周期达成**，RPO 6h 口径成立）|
| SHA256 校验 | ✅ 通过（sidecar 与实测哈希一致）|

## 二、恢复演练（真实恢复，不触碰生产库）

步骤：拷贝产物 → sha256 校验 → `CREATE DATABASE xadmin_drill` → `gunzip | psql` 导入 → 校验 → 清理。

| 校验项 | 结果 |
|--------|------|
| 导入错误 | **0**（`grep -c ERROR`）|
| 表数 | drill **89** = 生产 **89** ✓ |
| `system_userinfo` | 1 / 1 ✓ |
| `system_menu` | 553 / 553 ✓ |
| `system_deptinfo` | 4 / 4 ✓ |

清理：临时库已 drop、本地临时文件已删除。

## 三、结论与观察项

- **结论**：RPO 6h 备份链路可用、**产物完整可恢复**（校验和 + 全量导入 + 抽样一致）✓；
- 恢复耗时秒级（当前库 612K，**生产规模 RTO 需随数据增长重测**）；
- 观察项：① 异地副本未启用（[release-checklist](release-checklist.md) §3 挂起项不变，需独立盘/远端目标）；
  ② 本记录填补了 2027-03 之后的季度记录断档（节奏恢复）；
  ③ 媒体包恢复未在本次演练覆盖（仅校验存在与校验和）——下次演练补媒体恢复抽样。
