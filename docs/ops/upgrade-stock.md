# 存量库升级通道（版本间迁移口径）

> **一句话结论**：**4.2.5 及之后的库支持原地升级**——4.2.5 → 5.0 由
> `python manage.py upgrade_ledger --apply` 认领迁移账本（不执行 DDL），再 `migrate`；
> 更早的库按本文 §五的清库重建流程处置。**升级前先跑** `python manage.py upgrade_check`**，
> 由命令给出该库的判定与处置建议。

## 一、为什么会有「不能直接升级」的库

2026-10 期间，迁移链经历过四轮**清库重建式**整理（业务 app 历史迁移全部删除、按当前模型
重新生成全新初始迁移）：

| 轮次 | 范围 | 依据 |
|------|------|------|
| 第一轮 | approval / ai / dataset 等 app 的初始迁移由「零 DDL 认领」改为真实建表 | [ADR-058](../adr/ADR-058-system-migration-squash.md) |
| 第二轮 | system `0004~0011` 合并；dataset / approval / ai / message 全链合并为单一初始迁移；表归域改名 | [ADR-084](../adr/ADR-084-migration-squash-2026h2.md) |
| 第三轮 | system 拆分为 identity / file / audit / task 四域，全业务 app 迁移再次重建 | [system四域切分映射-2026.10.md](../plans/system四域切分映射-2026.10.md) |
| 第四轮（5.0） | 全部业务 app 收敛为初始迁移（每 app `0001_initial`，必要时 `0002_initial`），表结构与 4.2.5 逐行等价 | [升级 5.0](upgrade-5.0.md) |

重建后，旧版库的 `django_migrations` 记录与当前代码链**不再对应**（记录名不存在于
代码中，或同名迁移的内容已变化导致表结构漂移）——直接执行 `migrate` 会造成迁移状态
错乱。`upgrade_check` 命令就是为识别这两种情况而存在的。

**分界线**：以 4.2.5（2026-10 迁移链重建完成）为**唯一支持原地升级的基线**；5.0 的第四轮
合并对 4.2.5 链是**账本认领**而非结构重建（表结构等价，见 §三），因此 4.2.5 起的库可持续
原地升级；更早的库仍需清库重建。

## 二、支持矩阵（先判断你的库属于哪一档）

| 库的来源 | `upgrade_check` 结论 | 处置 |
|----------|----------------------|------|
| 4.2.5 迁移链创建（含全新安装） | `legacy-chain-reconcilable` | **账本认领**（§四 第 4 步）→ `migrate`（5.0 起） |
| 5.0 迁移链创建（全新安装）或已认领的库 | `up-to-date` / `needs-migrate` | 标准升级流程（§四） |
| 2026-10 四轮重建之前的旧版库 | `legacy-chain` | 清库重建（§五） |
| 迁移记录完整但表结构缺表/缺列 | `schema-drift` | 人工排查（§三 处置表；通常是混用旧版代码/库或被人工改过结构） |

> 认领的适用边界：**只对 4.2.5 链（白名单见 `system/utils/platform/migration_ledger.py`
> 的 `LEGACY_4X_CHAIN`）生效**，且要求表结构体检无缺表/缺列——即「库结构与合并链等价」。
> 白名单之外的游离记录一律拒绝（`legacy-chain`），不做自动认领：更早的重建轮次涉及表改名
> 与字段演进，自动认领的等价性无法可靠保证。

## 三、升级前体检与账本认领

```bash
python manage.py upgrade_check           # 人类可读报告（只读）
python manage.py upgrade_check --json    # JSON 输出（供脚本 / 安装器消费）
python manage.py upgrade_ledger          # 迁移账本对账（只读，默认）
python manage.py upgrade_ledger --apply  # 认领账本（写迁移记录，不执行 DDL）
```

`upgrade_check` 输出三个层面：迁移记录对账（已应用 / 待应用 / 4.x 游离 / 未知游离）、
表结构体检（缺失表 / 缺列）、结论与处置建议。退出码：`0` = 可继续升级；`1` = 需人工介入。

| 结论 | 含义 | 处置 |
|------|------|------|
| `up-to-date` | 库与当前代码一致 | 无需迁移 |
| `needs-migrate` | 存在未应用迁移（含全新空库） | 执行 `python manage.py migrate`（§四） |
| `legacy-chain-reconcilable` | 库来自 4.2.5 链、表结构一致，账本待认领 | `python manage.py upgrade_ledger --apply` → `migrate`（§四） |
| `schema-drift` | 记录完整但缺表/缺列 | 核对库的来源与版本；确认结构被人工改过则先修复，否则按 §五 清库重建 |
| `legacy-chain` | 存在不在当前代码链中的迁移记录（更早版本的库） | **不要 migrate**，按 §五 清库重建 |

`upgrade_ledger` 的对账/认领口径（幂等）：

- `consistent`：迁移记录与当前链一致（全新库 / 已认领）——no-op；
- `reconcilable`：删除 4.x 游离记录 + 把合并链未记录的迁移补记为已应用（**不执行 DDL**）；
- `unsupported`：存在未知游离记录或表结构漂移——拒绝写入，退出码 1。

> 说明：向量列（`ai_aiknowledgechunk.embedding_vector`）随 pgvector 扩展可选，
> 扩展缺失的库该列不存在属设计内降级（检索回退词频通道），不参与漂移判定。
>
> 两个命令同时被安装器升级流程调用（见 §六）；也可以在任意版本上单独执行。

## 四、标准升级流程（库可原地升级）

前置：`upgrade_check` 结论为 `up-to-date` / `needs-migrate` / `legacy-chain-reconcilable`。

1. **备份先行**：确认最近一次 `db-backup` 产出完好（或手动 `pg_dump` 一次）；
2. 读 Release Notes 的「升级注意」段落；
3. 拉取新版本并**先体检**：`python manage.py upgrade_check`；
4. **账本认领**（结论为 `legacy-chain-reconcilable` 时）：`python manage.py upgrade_ledger --apply`
   ——删除 4.x 游离记录并把合并链迁移补记为已应用，不执行 DDL；随后 `python manage.py migrate`
   （预期 `No migrations to apply`；若仍有待应用迁移即是真迁移，按常规执行）；
5. 按 [deployment-upgrade.md §6.1](deployment-upgrade.md) 执行迁移与滚动重启（单实例迁移 → 重启 → healthz 四项全 `true`）；
6. 涉及新增菜单/权限点、文案或 API 前缀调整的版本，执行 `python manage.py post_upgrade`
   （内含账本认领 → API 前缀平移 `migrate_api_prefixes --apply` → 种子导入 → 语言包编译 → 缓存失效；安装器已自动调用）；
7. 验证：`python manage.py upgrade_check` 复跑应为 `up-to-date`；再做登录冒烟。

回滚以**备份恢复**为准（Django 迁移原则上不做反向回滚），见 [deployment-upgrade.md §6.2](deployment-upgrade.md)
与 [runbook.md](runbook.md) 的「migrate 卡住 / 恢复」条目。

## 五、清库重建流程（旧链库，或明确不要旧数据）

> **语义边界**：迁移链重建按「大版本升级、不兼容老数据」口径执行——**数据不会自动迁移**。
> 演示/测试环境直接重灌种子即可；真实业务数据需在重建前自行导出（见第 2 步），重建后按
> 新结构核对导入。**重建不可逆，备份是唯一退路**。

1. **备份**：`pg_dump` 全库留档（保留到升级验收通过之后）；
2. **导出需要保留的业务数据**（可跳过——若确认数据可丢弃）：
   - 常用办法：`python manage.py dumpdata <app> --indent 2 > <app>.json` 按 app 导出；
   - 跨过三轮重建的表名/字段有差异，回灌时需按新结构核对（`loaddata` 只认当前模型结构）；
   - 只保关键业务（表单提交、审批实例、消息等）时，建议按 app 逐个评估，宁少勿滥；
3. **停服**：停止 web / worker 容器（避免写入）；
4. **重建库**：`DROP DATABASE` + `CREATE DATABASE`（或安装器的重装流程）；
5. **初始化**：`python manage.py migrate` → `python ops/init_data.py`（幂等；演示数据可加 `--with-demo`）；
6. **回灌**（可选）：把第 2 步导出的数据按新结构导入（`loaddata` 或业务导入模板）；
7. **收尾**：`python manage.py post_upgrade` → 重启容器 → `upgrade_check` 复跑应为 `up-to-date`；
8. **验收**：登录冒烟 + 关键业务抽查（表单提交、审批、通知）。

安装器部署可直接走其「备份 → 重装/升级」流程（内部即上述步骤的自动化版本）。

## 六、安装器集成

安装器的升级流程（`7_upgrade.sh`）在停服迁移前依次调用 `upgrade_check` → `upgrade_ledger --apply` → `migrate`：

- 结论 `up-to-date` / `needs-migrate` / `legacy-chain-reconcilable`（退出码 0）：继续升级；
  可认领的库随后由 `upgrade_ledger --apply` 自动完成认领（幂等；全新库与已认领库 no-op）；
- 结论 `legacy-chain` / `schema-drift`（退出码 1）：**中止**并打印本文指引——需人工确认走
  清库重建（或显式跳过预检，见安装器脚本内的环境变量开关）；
- 升级基线：`verify_upgrade_version` 要求当前版本 ≥ `v4.2.5`（更早版本需先清库重建）。

## 七、常见问题

| 现象 | 原因与处置 |
|------|-----------|
| `migrate` 报 `Table ... already exists` | 库不是当前链（或上次迁移中途失败）。`upgrade_check` 判定为 `legacy-chain`/`schema-drift` 时按 §五 重建；`needs-migrate` 时检查是否有并发迁移实例 |
| 升级 5.0 后 `migrate` 无任何动作，是漏升级吗 | 不是。4.2.5 → 5.0 的表结构等价，`upgrade_ledger --apply` 认领后 `migrate` 正常输出 `No migrations to apply`（见 [升级 5.0](upgrade-5.0.md)） |
| `upgrade_ledger --apply` 报 `unsupported` | 库里有白名单之外的游离迁移记录（更早版本链路）或表结构缺表/缺列：按 §五 清库重建，不要强行认领 |
| 升级后部分页面 403 / 菜单缺失 | 权限点未灌库：`python manage.py post_upgrade` 后重启（与迁移链无关） |
| 升级后用户 / 文件 / 日志 / 任务接口 404 或 403 | 存量库未执行 API 前缀平移（四域已迁至 `/api/identity|file|audit|task/`）：`python manage.py migrate_api_prefixes --apply`（`post_upgrade` 已包含），并确认后端已重启加载新路由 |
| 中文界面回退英文 | 语言包未编译：`python manage.py compilemessages`（`post_upgrade` 已包含） |
| `upgrade_check` 报缺表但库里数据完好 | 核对代码版本与库的来源；`schema-drift` 常因「新代码 + 旧库」混用，先对齐版本再判定 |
| 能不能只升一部分（比如只升前端） | 前后端版本必须一致（发布 tag 门禁强校验）；数据库升级以 §二 判定为准，不支持跨链部分升级 |
