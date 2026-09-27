# ADR-070：历史字段可见性（schema 版本快照解析）

- 日期：2026-09-27
- 状态：**已交付**（后端单测 10 例 + 集成 6 例；前端零改动）
- 背景：ADR-067 / ADR-069 共同登记的改进项——提交只存 `schema_version`（不存 schema 快照），列表 / 详情 / 导出都按**当前 schema** 取列，表单改版（删字段）后历史提交里该字段的值仍在 `data` 中却「值还在、看不见」。

## 决策

### D1 以 `schema_history` 为准解析「提交时的 schema」

`DynamicForm.schema_history` 已保留最近 `MAX_SCHEMA_HISTORY`（20）版的 schema 全文（版本 + 全文 + 操作人），无需给每条提交冗余存快照。新增解析层 `dataset/utils/dform_history.py`：

| 函数 | 口径 |
|---|---|
| `schema_for_version(form, version)` | 命中快照返回快照；缺失回落当前 schema |
| `merged_fields(form)` / `merged_fields_of_forms(forms)` | 当前 schema 字段（原序在前） ∪ 历史快照中当前已删除的字段（历史字段 label 追加标注 `historical: true`） |
| `submission_schema(obj)` | 单条提交的详情口径：提交版 schema 字段（当前已删的标历史） ∪ `data` 中无法识别的键（以 key 为标签兜底） |

### D2 三处消费口径统一（同一「（历史）」标注）

1. **详情**（我的填报 + 表单数据的 `form_schema`）：按提交版本渲染——字段标签与控件形态与提交时一致，已删字段带标注；
2. **导出**（两个导出口径共用的 `export_dynamic_fields`）：列集合 = 当前 schema ∪ 涉及版本的快照字段（跨表单按出现顺序合并、key 去重）——改版后导出不再丢列，旧值随行导出；
3. **「表单数据」选择表单**（管理端 `form-options`）：`schema.fields` 用合并口径，列表动态列同样能回看旧字段值。

标注文案 `" (historical)"` 为服务端 i18n 串，三个入口一致（前端零改动——按 `label` 渲染）。

### D3 边界与兜底

- 快照超出保留窗口（版本被裁）/ 手工改库等导致的**未知键**：`submission_schema` 以 key 为标签兜底展示（值确定可见，不回退为「看不见」）；与当前 schema 无关的键不再静默丢弃；
- **填报入口（`available-forms`）维持当前 schema**：历史字段不回灌填报（避免用户填一个已删除字段）；
- 不改提交存储（不存冗余快照）：`schema_history` 是唯一版本事实源，避免双写漂移；若将来保留窗口不足（> 20 版的回看诉求）再评估快照落库。

## 验证

- 单测：版本解析（快照命中 / 当前版本 / 缺失回落）、合并（历史标注、当前字段不被标注、无历史、跨表单去重保序）、详情口径（快照渲染 + 历史标注、未知键兜底、快照缺失仍可见）；
- 集成（`test_dform_history_api.py`）：v1 提交 → 改 schema 删字段 → 我的填报详情 / 表单数据详情按 v1 渲染、两个导出口径的 CSV 表头含历史列且旧值在行内、`form-options` 合并 schema、`available-forms` 维持当前 schema；
- 回归：dataset（unit + integration）与 ai 全量子集绿。

## 部署注意

无模型变化、无迁移、无权限点；纯后端读取口径调整（前端无需重建，但重新部署亦无害）。存量库无需数据修补——历史字段依据既有 `schema_history` 即时解析。
