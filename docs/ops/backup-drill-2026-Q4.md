# 备份恢复演练报告（2026 Q4，提前于 2026-09-11 执行）

> 关联：排期《剩余任务排期-2026.09-2027.02》D4「Q4 季度备份演练（按 N5 清单 SOP）」、
> N5 季度演练常态化、S2 备份失败告警（本报告一并验收）
> 脚本：`utils/db_backup.sh`（备份，本次未做任何修改）、`utils/db_restore.sh`（恢复，本机以等价命令执行）
> 结论：**本轮演练通过**——备份链路 PASS、sha256 自校验与异地副本一致 PASS、媒体包 PASS、
> 恢复 RTO **0.28s**、**67 表逐表行数 0 不一致**（4785 行）；S2 告警接线在真实失败路径上 PASS。

## 一、演练范围与方法

| 项 | 内容 |
|---|---|
| 执行日期 | 2026-09-11（按 N5「提前完成不推迟」惯例，替 Q4 计划窗口执行；非计划季度内提前完成属正常） |
| 备份触发 | `PGHOST=127.0.0.1 PGDATABASE=xadmin_q4drill BACKUP_ONCE=1 bash utils/db_backup.sh`（走真实脚本，非手工 pg_dump） |
| 被备份库 | 本机 PostgreSQL 16 的 `xadmin_q4drill`：**真实迁移 schema（67 表，含 approval 五表）** + 初始化 fixture（1619 objects / 4785 行） |
| 异地副本 | `BACKUP_REMOTE_TYPE=local`，`/tmp/xadmin-q4-drill-remote-a` |
| 媒体目录 | `BACKUP_MEDIA=true`，`MEDIA_DIR=/tmp/xadmin-q4-media`（含样本文件） |
| 恢复目标 | 独立验证库 `xadmin_q4drill_restore`（先 DROP 再 CREATE，等价 `utils/db_restore.sh` 步骤） |
| 一致性验证 | 源库 vs 验证库全表 `count(*)` diff（`pg_tables` 取清单，67 表全量） |
| 环境偏差 | **Docker/OrbStack 守护进程未运行**，容器路径（`xadmin-db-backup` / `db_restore.sh` 的 `docker exec`）本轮未覆盖，改用宿主机 PostgreSQL 原生执行；备份脚本本体零改动，链路逻辑（pg_dump → gzip -t 校验 → sha256 sidecar → 异地同步 → 媒体包）完全一致 |

## 二、结果（2026-09-11 19:03–20:16）

| 项 | 结果 |
|---|---|
| 触发备份链路 | PASS |
| 备份包 sha256 与 sidecar 自校验 | PASS（本地重算值 == `.sha256` 记录值） |
| 异地副本存在且一致 | PASS（本地与异地 sha256 一致：`af30afbe…4125` / `9fc2b52d…48f9`） |
| 媒体包产出与同步 | PASS（`xadmin_q4drill_20260911_190309.media.tar.gz` + `.sha256` 同步到异地） |
| `.latest_backup` 标记 | PASS（sql.gz + media.tar.gz 两行） |
| 恢复耗时 RTO | **0.28s**（目标 ≤30min） |
| 逐表行数一致 | PASS（**67 表比对、20 张非空、4785 行、0 不一致**） |
| 失败路径退出码 | PASS（`BACKUP_ONCE=1` 下 pg_dump 失败 → 脚本退出码 **1**，调度侧可感知） |
| S2 失败告警接线 | PASS（失败分支真实调用告警；告警地址不可达时仅追加一条 WARN，**不影响备份流程**） |

备份包体积：`xadmin_q4drill_20260911_190309.sql.gz` 144K（67 表 / 4785 行，含索引与约束）。

### 2.1 容器路径补验（同日 21:00，Docker/OrbStack 启动后）

首次执行时本机 Docker 守护进程未运行，容器路径未覆盖；当日晚补跑一次**完整容器链路**：

| 项 | 内容 |
|---|---|
| 环境 | OrbStack 启动 + `docker compose up -d postgresql db-backup`（复用既有 `xadmin-postgresql/data` 数据目录与 aliyuncs 镜像，无新增拉取） |
| 触发方式 | `bash utils/backup_drill.sh`（内部 `docker exec -e BACKUP_ONCE=1 xadmin-db-backup bash /utils/db_backup.sh`，走真实容器任务链路） |
| 恢复方式 | 脚本内 `db_restore.sh` 全流程（`docker exec` 建库 + `gunzip \| psql` 回灌） |
| 异地副本 | `BACKUP_REMOTE_TYPE=local` → 容器 `/remote` 挂载点（`xadmin-db-backups-remote/`） |

结果：**五项全 PASS**——触发备份链路 PASS、sha256 自校验 PASS、异地副本 sha256 一致 PASS、
恢复 RTO **0.91s**（61 表 0 不一致，源库为该本机 dev 库）、全流程 1.74s。
清理：演练后 `docker compose down`（保留卷）+ 停止 OrbStack，环境恢复演练前状态。

> 备注：首次未设 `BACKUP_REMOTE_TYPE/TARGET` 时异地项报 FAIL（异地缺少文件）——
> 属**配置未开**而非链路故障；按 `BACKUP_REMOTE_TYPE=local BACKUP_REMOTE_TARGET=/remote`
> 重启 db-backup 后重跑即 PASS。生产部署同样必须显式配置这两项（见 deployment.md §3.1 检查清单）。

## 三、S2 备份失败告警一并验收

- 脚本侧：`utils/db_backup.sh` 新增 `send_alert()`，在 pg_dump / 归档校验 / 异地同步 / 媒体打包四处失败点上报；
  令牌走 `X-Backup-Token` 请求头（不进 URL、不落日志），未配置 URL/TOKEN 时静默跳过（保持纯日志模式）。
- 服务端：`POST /api/common/api/backup-alert`（独立令牌 `BACKUP_ALERT_TOKEN`，`secrets.compare_digest` 比较），
  60s 同源节流后发站内信 + 邮件给在用超管（订阅缺失/收件人为空时自愈补建）。
- 实测（本机）：备份地址不可达 → 脚本仅 `WARN 告警投递失败（备份流程不受影响）`，退出码仍为失败码，符合设计。
- 单元/集成测试：`tests/integration/common/test_backup_alert.py`（令牌鉴权 403 / 站内信落库 / 同源节流 /
  多来源独立计数 / 脚本语法与告警接线的静态守护）。

## 四、过程中的发现

1. **演练数据也能反哺种子质量**：建库时 `load_init_json` 暴露了 `loadjson/systemconfig.json` 中 7 条新配置
   pk 非法（`...6a7b7b24` 形式的重复后缀，非合法 UUID），已修正并对全部 `loadjson/*.json` 加了主键合法性/唯一性校验；
   该缺陷若流入 E2E/生产种子会直接导致初始化失败。
2. **备份库需先有 `creator_id=1`**：fixture 装载顺序在全新库上会先插 `menumeta`（FK → `userinfo`），
   导致 `Key (creator_id)=(1) is not present`；本次先建超管再装载。生产首次初始化走 `init_data`
   不受影响，但**裸 `load_init_json` 建新库需注意顺序**。
3. **zsh 下 `for t in $TABLES` 不会按行分词**（与 bash 行为不同），逐表比对脚本须用 bash/显式分词或 python；
   否则会出现「只比 1 张表还显示 0 不一致」的假通过——建议后续把逐表对比固化进 `utils/backup_drill.sh` 时用 python 实现。
4. **容器路径未覆盖**：本机 Docker 守护进程未运行，`db-backup` 容器与 `db_restore.sh` 的 `docker exec` 本文未验证；
   下次季度演练（12-08 提醒）在有容器环境时补一次，重点看 `docker exec -e BACKUP_ONCE=1` 触发与 `RESTORE_MEDIA=1` 解包。

## 五、仍存缺口（滚动）

| # | 缺口 | 说明/建议 |
|---|---|---|
| 1 | ~~容器路径未在本次覆盖~~ | **✅ 2026-09-11 当日补验完成**：OrbStack + compose 容器路径五项全 PASS（见 §2.1） |
| 2 | 无 PITR（WAL 归档） | 沿用滚动缺口：RPO 6h 仍可能丢最坏 6h 数据，需秒级 RPO 时另立项 |
| 3 | 演练用异地副本为同盘目录 | 仅验证链路；生产必须指向独立故障域（独立磁盘/NFS/对象存储） |
| 4 | 逐表对比未固化进演练脚本 | 已固化在 `utils/backup_drill.sh`（`count_tables` + 「0 表即 FAIL」护栏）；本次原生路径曾用 python 复核同一口径，结论一致 |
