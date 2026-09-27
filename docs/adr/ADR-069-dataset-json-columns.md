# ADR-069：数据集 JSON 路径列（B1）

- 日期：2026-09-27
- 状态：**已交付**（后端单测 + 集成测试；前端列输入与预览口径同步）
- 背景：[ADR-067](ADR-067-online-table-evaluation.md) D2 登记项 **B1（数据集支持 JSON 路径列）** 触发条件命中——表单字段值都在 `DynamicFormSubmission.data`（JSON）里，此前数据集只能对 `data` 整列消费，无法「按表单字段级别做报表 / 看板」。本 ADR 记录列声明语法、能力边界与实现口径。

## 决策

### D1 列声明语法：`字段.键`（可带 `|number` 类型标注）

`Dataset.columns` 列表项扩展为三种形态（其余校验入口同语法）：

| 形态 | 示例 | 语义 |
|---|---|---|
| 模型字段 | `created_time` | 现状：`values("created_time")` |
| JSON 路径 | `data.kind` | 首段 = 模型上的 JSONField，第二段 = 键（提取为文本） |
| JSON 路径 + 类型 | `data.amount\|number` | 数值标注：sum / avg 与数值比较（gte 等）必需 |

要点：

- **列声明即输出列名**（含 `|number` 后缀）：`execute` 返回的行键与列头、CSV 表头、报表列配置、图表 `group_by` 全部使用同一字符串——前端渲染、报表设计列比对、`row.get(col)` 消费链**零改动即兼容**；
- 模型字段名不含 `.` / `|`（Django 字段名字符集），形态之间无歧义；解析按**最后一个 `|`** 切分且类型必须在白名单内，否则 fail-closed 拒绝；
- aliases：JSON 列在 ORM 侧注解为 `json_<根>_<键>`（如 `json_data_amount`），输出前重命名回列声明；sanitize 后重名（`data.a-b` vs `data.a_b`）在解析期检测并拒绝。

### D2 能力面：明细 / 筛选 / 排序 / 分组 / 数值聚合

- **明细列**：`KeyTextTransform` 提取（SQLite `json_extract` / PG `->>`，Django 原生支持），`values(别名)` 输出；
- **筛选**：字段为 JSON 路径时改为按别名过滤——SQLite / PG 均走同一 ORM 路径；`|number` 列的 `gte/gt/lte/lt` 经 `Cast(FloatField)` 做**数值**比较（避免 PG 上 `->>` 文本字典序把 `"10" < "5"` 判真）。**所有 JSON 列表达式统一 Cast 到显式类型**（文本列 `TextField`）：裸 `KeyTextTransform` 参与比较时会继承 JSON 字段语义、比较值被按 JSON 文档准备——SQLite 直接报 `malformed JSON`、PG 的文本比较语义也不符预期（实测结论，已由单测钉住输出类型）；
- **排序**：`ordering` 引用别名（`-data.amount|number` 即降序数值）；
- **分组聚合**：`group_by` 支持 JSON 路径（文本分组，布尔/枚举映射不适用 → 原始值，与 `_group_label(value, None)` 同口径）；
- **数值聚合**：`value_field` 为 JSON 路径时必须有 `|number` 标注，`Sum/Avg` 作用于 Cast 表达式；
- **字段权限**：JSON 路径列映射到根字段（`data.*` → `data`）参与浏览者可见性收敛——没有 `data` 字段权限的浏览者，其 JSON 列在明细与聚合中一并被裁 / 报错（与模型字段同口径）。

### D3 本段不做项（登记后续，fail-closed 显式拒绝；日期标注已于 ADR-071 交付）

1. **嵌套路径**（`data.a.b`）：表单提交的 `data` 为扁平一层；多段路径涉及键名歧义与深度上限，登记为后续（触发：出现嵌套 JSON 消费诉求）；
2. ~~**日期类型标注与 JSON 趋势**（`|datetime` + `date_trunc`）~~ → **已交付（2026-09-27，ADR-071）**：类型标注定为 `|date`（值契约 `YYYY-MM-DD`），分桶改用 `Substr` 前缀截断（跨库一致），`config.date_field` 同步支持；
3. **JSON 路径参与写入侧定义**（如 `filters` 的 `in` 列表类型强转）：维持既有 `in` 语义（列表值原样传入）。

### D4 校验口径（保存与执行双侧同源）

`parse_column` 为唯一解析入口，下列情形一律 `ValidationError`：

- 含 `|` 但类型不在白名单（当前仅 `number`）；
- 路径段数 ≠ 2，或任一段为空 / 含 `[A-Za-z0-9_-]` 以外字符；
- 根字段不在该模型的 DATA 字段白名单，或根字段不是 `JSONField`；
- 别名 sanitize 后冲突；
- `config.date_field` 为 JSON 路径（趋势不支持）；
- `ordering` 引用不在 `columns` 的列（既有口径）。

## 验证

- 单测：列解析（三形态、失败面全枚举）、别名与表达式生成；
- 集成：JSON 列明细 / 筛选（文本 + `|number` 数值比较）/ 排序 / 分组 / sum·avg / 缺键行为（空值不扰其它列）/ 字段权限（无 `data` 权限被裁与报错）/ fail-closed 面（嵌套、`|datetime`、非 JSONField 根、未知键字符）；
- 前端：数据集列选择器可输入 JSON 路径（`allow-create` + 提示）、报表明细预览取值口径与其它渲染点统一。

## 部署注意

无模型变化（`columns` 仍是 JSON 列表，元素语义扩展）；存量的模型字段列零影响。前端需重新构建（列输入提示与报表预览取值）。生产库建议先在一个绑定 `dataset.dynamicformsubmission` 的数据集上验证 `data.<字段>|number` 的聚合结果与导出/报表一致。

## 顺带修复

B1 实施中发现存量缺陷：`dataset` / `ai` 拆分（ADR-057）后，种子「示例-表单提交」数据集的 `bound_model` 仍是拆分前的 `system.dynamicformsubmission`，而字段白名单（`ModelLabelField` DATA 树）与 `apps.get_model` 的口径已是 `dataset.dynamicformsubmission`——该示例数据集在拆分后**保存校验与执行都会报「模型不可用」**。已随本批修正 `loadjson/dataset.json`（`load_init_json` 幂等导入按 pk 覆盖，重灌种子即修复存量库），并同步 ADR-067 的引用。
