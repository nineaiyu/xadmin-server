# ADR-080：物理表名归域重命名（解除 ADR-057 的 `system_*` 冻结）

- 日期：2026-10-03
- 状态：**已交付**（存量升级模拟 + 全新库 scratch 重放全链 + pytest 全量门禁；触发制台账 TG-3 / NEXT-DEV-PLAN O11-4 同步收口）
- 背景：TG-3 触发命中（2026-10-03 专项评估立项）。ADR-057 按域拆分 system 巨型 app 时，「表名不变」是四道迁移硬约束之一——25 个迁出模型以显式 `Meta.db_table = "system_*"` 冻结物理表名（ai 6 / approval 13 / dataset 6），使存量 PG 库零 DDL 完成代码拆分。冻结属迁移期兼容保留，重命名成本高、非专门立项不做；本 ADR 即该专门立项。

**对账记录（触发命中流程第一步）**：红线表逐项核对——本项不触碰多租户、移动端形态、BPMN 化、在线建表、AI 成本计价、SAML/CAS、AI 多供应商、common 包化、pgbouncer 任一重开条件；候选池无同域在办项；`DBRouter` 仍为预留占位（真多库拆分是本项的下一个典型场景，届时另立项，本改名不改路由面）。

## 决策

### D1 目标命名 = Django 默认归域名，删除显式 `db_table`

- 25 个模型表名 `system_<name>` → `<app>_<模型名小写>`；逐一核对与 Django 默认命名完全一致，因此**终态 = Meta 无显式 `db_table`**（与 system / message / notifications 等其余 app 同口径），此后模型类改名不再牵动物理表名，也不再有「代码 app 归属 ≠ 表名前缀」的双轨心智；
- 3 张自动 m2m 中间表随之改名（`system_approvalinstance_cc_users` / `system_approvalrequest_current_assignees` / `system_approvalrequeststep_assignees` → `approval_*`）；
- `DB_PREFIX` 部署（非默认）：前缀在 `class_prepared` 期统一叠加（`server/utils.py`），改名前后前缀语义不变，前缀部署的实际表名同步换挡。

### D2 专门迁移 = 各 app 单迁移 `AlterModelTable`

- `ai/0006`、`approval/0005`、`dataset/0006` 共 25 个 `AlterModelTable(table=None)`（回落默认命名）；Django 6 的该操作对 PG 执行元数据级 `ALTER TABLE ... RENAME TO`，**单迁移单事务原子完成、无数据重写**，并自动连带重命名 auto m2m 中间表；
- **索引与约束名不重命名**：trgm / GIN / HNSW 索引名本就不含表前缀（`idx_approvalrequest_path_trgm`、`idx_dformsub_filter_gin`、`aichunk_embedding_vector_hnsw`）且随表自动跟随，命中路径不变；Django 自建 B-tree 索引 / 约束的旧名（含 `system_` 串）保留——批量重命名需按 vendor 分支 DDL（MySQL 无 `ALTER INDEX RENAME`），无行为收益，登记为外观性余量（indexes.md 已注）。此后新建索引按新表名自动取名，无名称冲突面；
- **历史迁移一律不改**（approval/0001 等建表语句仍写 `system_*`）：全新链重放 = 0001 建旧名表 → 0005/0006 改名，自洽；存量库（当前链已应用库）直接 `migrate` 应用 3 个改名迁移即可，无 SeparateDatabaseAndState 兼容层（ADR-058 清库重建口径下不存在旧链升级包袱）。

### D3 外部 SQL 兼容期评估：无消费者，不设兼容视图

台账登记的触发后动作含「视图 / 存储过程 / 外部 SQL 兼容期」，逐面排查结论：

- 全仓（server / installer / client / web / docs / scripts / loadjson）**无** `CREATE VIEW | PROCEDURE | FUNCTION | TRIGGER | MATERIALIZED`；
- 业务代码无 raw SQL / cursor 触达这些表：dataset 报表与数据集执行、AI NL 查数、全局搜索（`system/search.py` 提供者）均走 ORM，表名经 `_meta.db_table` 动态解析，自动跟随；
- loadjson 种子用 model label（如 `approval.approvalrequest`），content_type / 权限点 / Menu.path / ModuleSpec 均不依赖物理表名（ADR-057 已把 app_label 类持久化状态平移收口）；
- 代码内手写 DDL 两处同源化：`ai/utils/ai_vector_ddl.py` 表名改从模型 Meta 取（`_chunk_table()`，不再硬编码）；trgm 索引建在迁移内、随表跟随。

→ 兼容期 / 兼容视图**无对象，不设**（避免引入只读转发层的长期债）。若某部署侧存在仓外备份脚本、外接报表直连旧表名，属部署资产：PG rename 后旧名直接失效（不会静默错读），随发布 checklist 自查。

### D4 trgm 快照折算（漂移守护保持三处一致）

ADR-058 冻结在 `approval/0001` 内的 `TRGM_INDEXES` 快照是**建索引时点的历史表名**，不能改写（改了全新链重放会在改名前时点找不到表）。折算机制：

- `approval/0005` 登记 `TRGM_TABLE_RENAMES`（历史表名 → 现名），仅供守护折算，不含 DDL；
- 漂移守护 `test_snapshot_matches_runtime_registry` 按迁移序折叠折算后与运行期清单比对，`test_rename_registration_covers_only_known_tables` 防折算登记本身腐化；
- 运行期清单 `system/search_indexes.py` 改登现名（覆盖守护以实时 `_meta.db_table` 比对，自动闭环）。

### D5 守护（回填）

- `tests/unit/server/test_table_domain_alignment.py`：拆分 app 表名必须等于默认归域名（例外须显式登记 `TABLE_NAME_OVERRIDES`）；任何非 system app 禁止 `system_*` 表名；比对剥离 `DB_PREFIX` 后的基名（前缀部署同样受守护）；
- 6 处 SQL 捕获断言（审批流 / prefetch / relation_count / 检索索引守护）随表名更新，原守护语义不变。

## 后果

- 部署 = 常规 `migrate`（链上 +3 迁移）。PG 上 RENAME 取 ACCESS EXCLUSIVE 锁：与任何 DDL 同纪律，部署窗口避开长事务/长查询即可；元数据级操作秒级完成，无长锁持有面；
- 回滚对称：`migrate approval 0004` / `ai 0005` / `dataset 0005` 即整体换回旧名；
- 索引 / 约束旧名为登记在案的外观性余量（D2），不设清理窗口；
- 真做多库拆分 / 表再归域时，`DBRouter` 立项可直接消费本终态：app 域与物理表名已一一对应，按 app_label 分库不再需要表名映射层。
