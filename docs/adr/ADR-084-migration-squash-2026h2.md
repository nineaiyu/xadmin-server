# ADR-084：2026-10 迁移合并（清库重建窗口）

- 日期：2026-10-03
- 状态：**已交付**（`makemigrations --check` 零漂移 + 全新 PG 库全链 replay + pytest 全量 + trgm 9 索引守护通过）
- 背景：ADR-058 合并后半年内各 app 又累计新增 2026 年度迁移 25 个（system 0005~0011、dataset 0002~0006、
  approval 0002~0005、ai 0002~0006、message 0002~0003、notifications 0004、common 0002）。其中包含
  ADR-080 表归域改名的整批 `AlterModelTable`、两个「退役权限点」存量库数据迁移与向量列回填，
  新装重放路径长且携带大量过程操作。用户决策再次清库重建，合并时机成熟。

## 决策

### D1 按 app 合并

| app | 处置 | 结果 |
|-----|------|------|
| system | **0004~0011 合并为新 `0004_*_and_more`**（对 0003 图谱重新 autodetect 生成）；0001~0003 原样保留 | 11 → 4 |
| dataset / approval / ai / message | 全链合并为**单一真实建表 `0001_initial`**（由当前模型 autodetect 生成，表名即 ADR-080 后的现名，改名迁移不再存在） | 各 → 1 |
| notifications | `0003+0004` 合并为 `0003_messagetemplate_messagecontent_deleted_at` | 5 → 3 |
| common | `0002` 单文件，原样保留（未与其 2024 年 `0001` 合并——跨年度边界维持 ADR-058 口径） | 不变 |
| settings / captcha / demo / mfa | 无需合并（settings/captcha 链路为 2024~2025 存量，demo/mfa 已是单迁移） | 不变 |

迁移文件总数 42 → 18（不含 `__init__.py`）。

### D2 受控执行逻辑退役与保留

- **退役**（新装库无意义，随清库重建一并去除）：
  - `system/0006`、`system/0011` 的「退役权限点」RunPython（存量库清 Menu/MenuMeta 残项）——
    对应守护测试 `test_retired_permission_points_pruned_by_migration` 同步退役，种子不登记删除权限点的
    覆盖测试保留；
  - `approval/0003` 的 `flow.version` 回填、`ai/0005` 的向量列存量回填（全新库无存量行）；
  - ADR-080 的整批 `AlterModelTable`——初始迁移直接以现名建表，`TRGM_TABLE_RENAMES` 改名折算登记
    随之退役。
- **保留**（性能/能力优化不阻断部署的受控执行形态）：
  - pg_trgm 检索索引 9 个——仍按表归属拆为 system/0004（5 个）+ approval/0001（4 个）的
    `SeparateDatabaseAndState`（state 声明与模型 Meta 一致 + 仅 PG 受控执行、失败只告警），
    快照以现名冻结；
  - pgvector 向量列——ai/0001 将 `embedding_vector` 从 CreateModel 拆出：`CREATE EXTENSION vector`
    前置 + 列 DDL 手写幂等执行，扩展缺失的库跳过 DDL 不阻断 migrate（检索层 fail-open 回退词频）。

### D3 前提与重开条件

本合并以**清库重建**为前提：已应用旧迁移链（旧文件名）的存量库不再能线性升级，需按 ADR-057 的
SeparateDatabaseAndState 认领形态另行提供升级迁移。本地开发库重建口径：删除旧库后 `migrate` +
种子灌库（`loadjson`）即可，`django_migrations` 无需手工对账。

### D4 守护与文档同步

- `tests/unit/system/test_search_indexes.py`：快照导入路径不变（system/0004、approval/0001），
  **删除改名折算逻辑**（`RENAME_MIGRATIONS` / `TRGM_TABLE_RENAMES` 对账测试）——快照即现名；
- `system/search_indexes.py` 注释与 `docs/architecture/indexes.md` 口径同步；
- 新装全链验证：pytest 真环境档（全新 `test_xadmin_realtest` 库即全新迁移链 replay）全量通过。
