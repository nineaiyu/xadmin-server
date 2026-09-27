# ADR-071：JSON 日期列与趋势分桶

- 日期：2026-09-27
- 状态：**已交付**（后端单测 + 集成；前端提示与日期字段输入同步）
- 背景：[ADR-069](ADR-069-dataset-json-columns.md) D3 登记项——JSON 路径列的**日期类型标注与 `date_trunc` 趋势**当时因跨库 Cast 方言差异未做（fail-closed 拒绝）。本 ADR 记录落地口径。

## 决策

### D1 类型标注新增 `|date`（不引入 `|datetime`）

列声明扩展为 `字段.键|date`。值契约 = `YYYY-MM-DD`（**与表单 `date` 控件产出、明细子表 date 列校验同口径**，见 D3）；控件集中没有 datetime 控件，因此不引入 `|datetime`（避免声明与数据形态不匹配）。

### D2 分桶实现：`Substr` 前缀截断（跨库一致），不用 `Trunc + Cast`

| 方案 | SQLite | PG | 结论 |
|---|---|---|---|
| `Trunc(Cast(expr, DateTimeField()), day)` | `CAST(x AS datetime)` **无类型亲和性 → 数值化**（`'2026-09-01'` → 2026）| `x ->> 'k'` 需 `::timestamp` | ✗ 两端方言不一致 |
| `Substr(expr, 1, 7 \| 10)` | `SUBSTR(json_extract(...), 1, 7)` ✓ | `SUBSTR(x ->> 'k', 1, 7)` ✓ | **采用** |

- `date_trunc=month` → `Substr(expr, 1, 7)` → `YYYY-MM`；`day` → `Substr(expr, 1, 10)` → `YYYY-MM-DD`；
- 桶名与模型字段路径（`Trunc`）**格式完全一致**，前端图表无需区分数据源形态；
- ISO 日期字符串的字典序等于时间序，`filters` 的 `gte/lte` 文本比较即区间过滤（语义正确，已由探针与测试钉住）。

### D3 写入侧加固：顶层 `date` 字段提交校验收紧

`dataset/utils/dform.py` 的顶层 `date` 类型此前只校验「是字符串」（**与明细子表 date 列的 `DATE_RE` 校验不一致**），API 直提可写入任意文本，会让趋势分桶失真。本批统一收紧为 `^\d{4}-\d{2}-\d{2}$`：

- 前端 `el-date-picker` 默认 `YYYY-MM-DD`，正常链路零影响；
- 存量库中的非 ISO 历史值不阻断读取（分桶落空桶），需要时按业务自行修正。

### D4 `config.date_field` 支持 JSON 日期列

折线趋势卡片的默认分组字段此前仅模型字段（JSON 路径被拒）。现允许 `data.x|date` 形态，校验要求：必须命中该数据集的 `columns` 且带 `|date` 标注（与聚合时的分桶要求同源，避免「配置能存、执行必失败」）。

### D5 边界（登记，不阻断）

- `daterange` 列（数组值）不做趋势（区间是两段文本，分桶语义需单独设计）；
- JSON 日期列的时间粒度只有 day/month（与模型字段路径一致）；
- 非 ISO 值（存量直写数据）落空桶，不报错。

## 验证

- 单测：`|date` 解析与 `Substr` 表达式（month/day 截断长度）、非法标注拒绝；
- 集成：JSON 日期列的趋势分桶（month/day 桶数与桶名）、`filters` 日期区间过滤（ISO 文本序）、无标注 JSON 列做趋势 fail-closed、`config.date_field` 接受 `data.x|date` 且拒绝未标注/不在 columns 的形态、顶层 date 字段非 ISO 提交被拒（与明细子表口径一致）；
- 前端：数据集设计器提示补充 `|date`；趋势字段下拉支持输入 JSON 日期列（allow-create）。

## 部署注意

无模型变化、无迁移、无权限点；顶层 date 字段的提交校验收紧属**行为变更**（非 ISO 日期值将被拒绝，正常前端链路不受影响）。前端需重新构建（提示文案与趋势字段输入）。
