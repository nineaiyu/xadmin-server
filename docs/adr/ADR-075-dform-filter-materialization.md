# ADR-075：动态表单可筛选字段物化（物化列 + GIN）

- 日期：2026-09-30
- 状态：**已交付**（后端单测 13 例 + 集成 5 例 + 重建命令 2 例；前端设计器开关与列表筛选 UI + e2e 3 处）
- 背景：ADR-067 D2 登记的后续项「高频筛选字段物化列 + GIN 索引」（在线建表的务实中间态）。表单数据都在 `DynamicFormSubmission.data`（JSON blob），列表此前只能按固定列（表单 / 状态 / 提交人 / 创建时间）筛选；按表单字段筛选需要 JSON 键路径比较，而 schema 是动态的——无法为每个字段建表达式索引，`data -> key = value` 的比较也命中不了任何索引。

## 决策

### D1 物化列 + JSON 包含查询（不投物理表）

- 设计器可对**等值型控件**勾选「可筛选」（字段属性 `filterable`；服务端按 `FILTERABLE_TYPES` 白名单校验：布尔取值 + 类型在可筛集合内，upload / table / daterange 拒绝）；
- 提交时把勾选字段的**规范化取值**物化到 `DynamicFormSubmission.filter_data`（只含勾选字段，空值不写入，非勾选字段不落）；
- 列表筛选编译为**单条 JSON 包含查询** `filter_data @> {...}`：PostgreSQL 命中 GIN 索引 `idx_dformsub_filter_gin`（`jsonb_ops` 支持 `@>`），一次查询覆盖任意字段组合，不受 schema 变化影响；
- `data` 仍是唯一数据事实源，`filter_data` 只是筛选索引面（可随时按当前 schema 重建）。

### D2 写入路径统一走单一实现

`dataset/utils/dform_filter.py::build_filter_data(schema, data)` 是唯一实现，五条写入路径接线（覆盖提交的全部入口）：

| # | 路径 | 位置 |
|---|---|---|
| 1 | 创建 / 编辑（含草稿、直接生效、绑定流程提交） | `dataset/serializers/dform.py::DynamicFormSubmissionSerializer.validate` |
| 2 | 草稿提交（`{pk}/submit`） | `dataset/views/dform.py::submit` |
| 3 | 驳回重提 | `dataset/utils/dform_flow.py::resubmit_submission` |
| 4 | 操作审批通过后自动落库（新建） | `dataset/utils/dform_flow.py::submit_from_approval` |
| 5 | 草稿经操作审批通过后自动完成（更新） | `dataset/utils/dform_flow.py::update_from_approval` |

### D3 筛选口径 fail-closed + 数据库分层

- 参数形态 `?filter_data={"key": value}`（JSON 对象）；随 `?form=` 给出时按该表单**当前 schema** 校验：字段未勾选「可筛选」或类型不可物化 → 可读报错（不回退扫原 `data` 列，避免绕过索引承诺）；
- 未给出 `form`（不限表单的列表，如「我的填报」）按通用形态编译（key 格式合法 + 标量 / 标量数组）；未物化的字段自然不命中任何行，查询仍受行级权限收敛，无绕过面；
- 取值按字段类型规范化（数字 / 布尔 / 用户主键 / 多选数组）；数组型字段（checkbox、多选 user）按 JSON 数组包含语义（「至少命中一项」），cascader 按整条路径匹配；
- **数据库分层**：PostgreSQL 走 `@>`（GIN）；其它后端（sqlite：测试 / 开发形态）退化为逐键精确比较 `filter_data -> key = value`，其中数组条件退化为「整值相等」——语义差异已登记（生产为完整语义）。

### D4 存量兼容：重建命令

物化列引入前落库的提交没有 `filter_data`；表单新勾选「可筛选」后按该字段筛选不命中旧行。`manage.py rebuild_dform_filter_data`（`--form` 限定表单 / `--batch` / `--dry-run`）按当前 schema 重算并**只更新有差异的行**（幂等、可重复执行，不触碰 `data`）。

### D5 前端

- 设计器字段属性弹窗新增「可筛选」开关（仅等值型控件可见）；保存时对不可筛类型剔除标记（与服务端同口径）；
- 「表单数据」页在选择表单卡片下新增**字段筛选行**：文本 / 数字 / 选项（内联或字典）/ 多选 / 日期 / 是-否 控件按字段类型选择，条件随列表与导出请求下发（导出自动跟随筛选），切换表单清空条件；
- `user` / `cascader` 需专用选择器，页面 UI 暂不渲染（接口可直接筛，登记为后续 UI 项）。

## 备选与不选

| 方案 | 不选原因 |
|---|---|
| 每字段物理列（jeecg Online 形态） | 需运行期 DDL，ADR-067 D1 已否决（权限面 / 迁移体系 / 备份口径 / 二开契约） |
| 每个可筛选字段一条表达式索引 | schema 动态、表单数以百计时索引面无界，无法随设计器操作安全扩缩 |
| 纯应用层过滤（Python 内筛） | 分页与总数失真，且数据权限与导出口径无法同源 |
| 物化 JSON 文本列 + pg_trgm 模糊匹配 | 本场景主诉求是等值筛选；模糊匹配需另加物化文本列（后续按需立项） |

## 验证

- 单测（`tests/unit/dataset/test_dform_filter.py`）：可筛选面派生（未勾选 / 不可筛类型剔除）、物化取值（空值不写、`false`/`0` 保留）、筛选编译 fail-closed、类型规范化与数组包单元素、通用模式（非法 key / 嵌套对象 / 非 JSON 拒绝）、后端分支选择（包含查询 vs 逐键比较）；
- 单测（`test_dform_filter_rebuild.py`）：存量回填、幂等、`--dry-run`、`--form` 范围；
- 集成（`tests/integration/dataset/test_dform_filter_api.py`）：提交物化（草稿创建 / 草稿提交）、编辑重物化、管理端多条件筛选命中、未勾选字段 400、不限表单通用编译、「我的填报」creator 隔离 + 同参数筛选、schema `filterable` 校验（类型 / 取值）；
- e2e：`dform.e2e.ts` 设计器勾选「可筛选」并保存；`dform-data.e2e.ts` 按可筛选字段筛选列表并清空恢复。

## 部署注意

- 需 `migrate`（dataset `0005_dform_filter_data`：新增 `filter_data` 列 + GIN 索引）；
- 存量提交按需执行 `python manage.py rebuild_dform_filter_data`（不执行不影响新提交与新筛选，只影响「新勾选字段筛选旧行」）；
- GIN 索引在 PostgreSQL 生效；其它后端按不支持跳过（语义退化为逐键比较，见 D3）。

## 边界

- 不支持模糊 / 范围筛选（等值语义；range 需控件级范围参数与索引面扩展，按需立项）；
- 不检索 `data` 中未物化的字段（fail-closed 报错而非静默全表扫描）；
- 不对 `filter_data` 做历史版本化：字段取消勾选后该键保留在旧行中（筛选面按当前 schema 校验，取消勾选即不可筛，数据不影响）。
