# ADR-058：2026 年度迁移合并（清库重建窗口）

- 日期：2026-09-27
- 状态：**已交付**（全新 PG 库 scratch 重放 82 迁移全链 OK + `makemigrations --check` 零漂移 + trgm 9 索引就位 + 种子/演示数据灌库 + doctor 9/9 + pytest 全量 + `test_search_indexes` 双迁移守护）
- 背景：ADR-057 拆分后 system 链上留下 2026 年度迁移 16 个（0004~0019，含零 DDL 认领链与三个存量库数据迁移），新装重放路径长且携带大量「建了再删」的过程操作。用户决策清库重建本地容器，存量库升级链路失去存在前提，合并时机成熟。

## 决策

### D1 按 app 合并为单文件

- **system**：`0004~0019`（8 个文件）合并为 `0004_accountrisk_..._and_more`（单一净增量，由当前模型对 0003 图谱重新 autodetect 生成——建了再删的审批/AI/数据分析模型不再出现在 system 链上）；0001~0003 原样保留；
- **approval / ai / dataset**：`0001_initial` 由「零 DDL 认领」（SeparateDatabaseAndState 纯状态）改写为**真实建表初始迁移**（`db_table` 仍为 `system_*` 旧表名，ADR-057「表名不变」约束延续，loadjson 种子与外部引用零改动）；
- **notifications**：`0003+0004` 合并为 `0003_messagetemplate_messagecontent_deleted_at_and_more`；
- **demo**：`0001+0002` 合并为 `0001_initial`（`book.instance` 外键直指 `approval.approvalinstance`，不再需要 state-only 改指）；
- **message / common**：各自仅一个 2026 后迁移（0001 / 0002），原名保留零改动；
- **settings / captcha / mfa**：无 2026 后迁移，不涉及。

### D2 存量库升级专用逻辑退役与保留

- **退役**（新装库无意义）：`0014/0016/0019` 的 content_type·权限·标签串 app_label 改写、`0004` 的种子行时间戳回填、`0005` 的审批显示名回填；
- **保留**（性能优化不阻断部署）：pg_trgm 检索索引的「state 声明 + 受控执行」模式——按表归属拆入 `system.0004`（userinfo ×4 + uploadfile ×1）与 `approval.0001`（approvalrequest ×3 + leave ×1），快照常量 `TRGM_INDEXES` 冻结于迁移内；`tests/unit/system/test_search_indexes.py` 守护口径升级为**双迁移合并快照 ↔ 运行期清单**比对。

### D3 前提与重开条件

本合并以**清库重建**为前提：已应用旧迁移链（0004~0019 旧文件名）的存量库不再能线性升级。若未来出现需原位升级的存量部署，按 ADR-057 的 SeparateDatabaseAndState 认领形态另行提供升级迁移，不回滚本合并。

### D4 顺带修复（种子复检）

`loadjson/modellabelfield.json` 中 7 行 AI 模型节点仍挂拆分前旧标签 `system.ai*`（ADR-057 种子同步后，AI 控制台批次后置追加所致漏网），修正为 `ai.*`；全新安装字段树与存量部署完全对齐（菜单 668 / 字段树 1860 / 权限点 360），doctor 9/9。全局搜索索引快照的文档引用同步更新（`system/search.py`、ADR-028 不改历史正文）。

## 事故记录

容器内执行 `utils/init_data.py` 时未显式指定 `DJANGO_SETTINGS_MODULE`，脚本回落 config.yml 误连正在运行的 xadmin 库：migrate 在新链首个迁移处因表已存在失败（脚本 try/except 吞掉，无任何 DDL 落库），幂等种子/演示命令重复执行（演示用户实际为既有数据复用口径）。**教训：容器内一切管理命令必须显式 `--settings`/环境变量指向目标库；执行前先 `SELECT 1 FROM django_migrations LIMIT 1` 确认连接目标。** 该库随后按计划清库重建，无最终影响。
