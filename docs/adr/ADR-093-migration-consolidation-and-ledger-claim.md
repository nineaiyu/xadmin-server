# ADR-093：5.0 迁移链合并与存量库账本认领

- 日期：2026-10-14
- 状态：**已交付**（合并链 + `upgrade_ledger` 认领 + 安装器接线 + 守护测试同批落地）

- 背景：2026-10 期间迁移链经历三轮**清库重建式**整理（app 拆分、表归域改名、历史迁移合并），
  代价是每个 app 的迁移被拆成一串小文件（本次合并前 14 个业务 app 共 26 个迁移文件）。
  重建链只对「全新安装」与「当期基线（4.2.5）」友好：更早的库必须清库重建，跨版本升级
  的运维成本高。5.0 是大版本，正是收敛迁移文件体积、同时把 4.2.5 库的升级路径从
  「清库重建」升级为「原地认领」的窗口。

## 决策

### D1 迁移收敛为「每 app 初始迁移」

- 删除全部业务 app 的历史迁移文件，按当前模型重新生成：每 app `0001_initial`；
  跨 app 循环依赖处由 Django 自动拆为 `0001_initial` + `0002_initial`（先建表、
  再补外键/约束）。
- **结构等价性是硬约束**：合并前用旧链在临时库建结构、合并后用新链建结构，
  `pg_dump --schema-only` 归一化（列序无关、剔除 dump 随机 token）后**逐行比对必须 0 差异**。
  本次实测 12016 行、126 张表、0 差异。
- 迁移中不可由 `makemigrations` 重建的**扩展创建操作**（`pg_trgm` / `pgvector`）以
  `RunPython` 保留在新初始迁移的**首操作**（后置索引 DDL 依赖扩展先行）。
- 新迁移头注释登记被替换的 4.x 迁移名（血缘可读），不使用 `replaces` 元数据——
  初始迁移与旧链首迁移同名，`replaces` 自指会造成语义歧义。

### D2 存量库走「账本认领」而非结构重建

- 新增 `manage.py upgrade_ledger`（只读对账 / `--apply` 认领）与
  `system/utils/platform/migration_ledger.py` 的白名单 `LEGACY_4X_CHAIN`
  （4.2.5 全部迁移名）。
- 判定三分支：`consistent`（全新库 / 已认领，no-op）→ `reconcilable`（可认领）
  → `unsupported`（未知链路或结构漂移，拒绝写入并退出码 1）。
- 认领动作只写 `django_migrations`：删除白名单内的游离记录，把同 app 合并链中
  未记录的迁移补记为已应用，**不执行任何 DDL**。
- 前置校验两层：① 游离记录全部命中白名单（未命中的一律拒绝，不做「尽力而为」的清理）；
  ② 表结构体检无缺表/缺列（按模型逐列比对，向量列随扩展可选、不参与判定）。
- `upgrade_check` 新增 `legacy-chain-reconcilable` 判定（退出码 0，处置提示指向认领命令），
  「白名单外游离」与「结构漂移」仍为退出码 1。

### D3 升级链路与基线

- 安装器升级链路：`upgrade_check` → `upgrade_ledger --apply` → `migrate` → `post_upgrade`
  （`post_upgrade` 首步也调用认领，幂等；旧镜像无该命令时告警跳过，回退原有迁移路径）。
- 安装器升级基线由 `v3.10.11` 提升到 `v4.2.5`：更早版本不再尝试原地升级，直接提示
  清库重建路径（避免「跑到一半失败」）。
- 认领的**前提**是「5.0 没有结构变更」。后续版本若有结构变更，按常规新增迁移表达，
  不修改已发布的初始迁移。

## 后果与边界

- 4.2.5 起的库可原地升级到 5.0（一条命令认领 + `migrate` 无动作 + `post_upgrade` 补全）；
  更早的库仍需清库重建（三轮重建涉及表改名与字段演进，等价性无法可靠保证）。
- 迁移文件体积收敛：26 → 21 个（含 7 个跨 app 循环依赖拆分的 `0002_initial`），
  新库建表路径不再执行 26 步历史操作。
- 首发公告与运维口径：升级指南 [ops/upgrade-5.0.md](../ops/upgrade-5.0.md)、
  存量库通道 [ops/upgrade-stock.md](../ops/upgrade-stock.md)。

## 验证

- 结构等价：旧链库与新链库 `pg_dump --schema-only` 归一化比对 0 差异（126 表 / 12016 行）。
- 认领实测：旧链库 `upgrade_check` = `legacy-chain-reconcilable`（12 条 4.x 游离 / 7 条待补记）
  → `upgrade_ledger --apply` → `migrate` 输出 `No migrations to apply` → 复检 `up-to-date`。
- 守护测试：`tests/unit/system/test_migration_ledger.py`（判定分支 / 真实库对账 / 幂等 / 拒绝写入）
  与 `tests/unit/system/test_upgrade_check.py`（五分支配色 + 真实库判定），共 25 例。
- 模型无漂移：`manage.py makemigrations --check --dry-run` → `No changes detected`。

## 修订：四域切分前的库不适用原地升级

发布后的 4.2.5 → 5.0.1 升级演练（虚拟机实测，源码形态 + 官方安装器形态）推翻了本 ADR 原先的
「4.2.5 及之后均可原地升级」表述，补齐两点事实与对应处置：

1. **表名与列集在四域切分时已变**：v4.2.5（2025-10）的库是 `system_userinfo` / `system_deptinfo` /
   `system_uploadfile` / `system_operationlog`，而 5.0 的合并链按域建表（`identity_userinfo` /
   `file_uploadfile` / `audit_operationlog` …），并新增了 `deleted_at`、`system_menumeta`
   的展示相关列等。合并链的 `0001_initial` 与**四域切分之后的** 4.x 链等价，与此前的库不等价。
2. **同名迁移会让结构永久停旧**：Django 按迁移名判定「已应用」，旧库里的 `system.0001_initial`
   与合并链同名，因此合并链的建表/加列永不执行——`migrate` 会在访问新列时中断
   （实测 `column system_menu.deleted_at does not exist`），随后还会撞
   `InconsistentMigrationHistory: system.0001_initial is applied before its dependency
   identity.0001_initial`。

因此新增判定 `legacy-chain-pre-split`（`migration_ledger.PRE_SPLIT_TABLES`：旧表在、当前表不在）
与拒绝认领的保护；替代路径是「备份 → 清库重建 → 回灌」，回灌由 `legacy_export` /
`legacy_import` 承担（自然键关联，不依赖主键；覆盖组织 / 角色 / 用户 / 数据权限 / 系统配置 /
附件 / 公告，日志与任务历史不搬运）。安装器升级的预检文案同步指引该路径。

本轮同时确认的两处运维口径已写进 [ops/deployment-upgrade.md](../ops/deployment-upgrade.md)：
PG 数据目录 16 → 17 需 `pg_dump` / `pg_restore`（换镜像会因数据目录版本不符循环重启），
以及切分前旧库的判定与回灌步骤（[ops/upgrade-stock.md](../ops/upgrade-stock.md) §五、§七）。
