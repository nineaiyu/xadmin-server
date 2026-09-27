# ADR-064：动态表单设计器升级（拖拽排序 / 联动规则 / schema 版本化）

- 日期：2026-09-27
- 状态：**已交付**（backend pytest 全量 EXIT=0 + 联动单测 16 例 + 设计器集成 9 例 / client typecheck + strict + vitest 646（新增 linkage 8 + fieldOrder 4）+ eslint/prettier/stylelint/i18n + 行数门禁 + build 与体积门禁 549.8 KB / 预算内 / E2E 双浏览器「联动 + 版本」4 例通过 + 既有 dform 16 例回归）
- 背景：动态表单设计器此前是**字段表编辑 + 上移/下移**形态（`DynamicFormForm.vue` 手工维护字段数组），不支持拖拽排序、字段间联动（表单里所有字段永远可见且必填固定）、schema 版本（改动即覆盖，无法回看或回滚）。P2.1 的决策点是「是否对齐 jeecg Online 的拖拽/联动/版本化；在线建表延后评估」——本 ADR 交付前三项，在线建表仍不做。

## 决策

### D1 联动规则进 schema，扁平结构 + 顺序求值

`schema.linkages`（可空数组）承载联动规则，每条规则**单目标、单条件**：

```json
{"target": "amount", "field": "kind", "op": "eq", "value": "A", "effect": "require"}
```

- `op` 白名单：`eq` / `ne` / `in` / `notin` / `empty` / `notempty`；值型操作符（前四）必须给 `value`，`in`/`notin` 要求标量列表；
- `effect` 白名单：`hide` / `show` / `require` / `optional`；
- `target` / `field` 必须命中本表单字段，且**不得自引用**（语义歧义）；规则数 ≤ 50；
- 求值口径：**按数组顺序，最后一条命中规则的 effect 生效**（隐藏与必填两个维度独立覆盖）；未命中任何规则 = 沿用字段自身定义；
- 多值触发字段（checkbox / 多选）按「与规则值集合有交集」判定，`notin` 取反。

选择扁平结构而非嵌套表达式树的理由：可被前端规则表格一行一列地编辑与展示、求值一次线性遍历且天然收敛（无环检测需求）、服务端校验点少（字段存在性 + 白名单 + 值形态）；复杂条件组合可拆成多条规则按顺序表达（顺序覆盖语义已明确定义）。

### D2 服务端提交校验「联动优先」，前端只是镜像

`validate_submission_data` 先按原始提交数据求值联动，再逐字段校验：

- **隐藏字段**：跳过必填与取值校验，且**不写入落库数据**（`normalized[key] = None`）——隐藏即不生效，避免审批快照里出现用户已看不到的旧值；
- **必填/非必填**：`require` / `optional` 覆盖字段自身 `required`；
- 前端 `views/form/my/utils/linkage.ts` 镜像同一份求值口径（隐藏不渲染、必填动态加星、切隐藏即清值），但**仅作展示层**：后端校验是唯一判定（下拉多选、手改载荷都绕不过服务端）。

### D3 schema 版本化：实质变更 +1，历史可回看可回滚

- `DynamicForm.schema_version`（默认 1）+ `DynamicForm.schema_history`（新 → 旧，保留最近 20 个版本，含 schema 全文、时间、操作人）；
- **规范化后比较**：同内容保存（PATCH 相同 schema）不产生新版本，避免「点一次保存就 +1」的噪声版本；
- 回滚（`POST {pk}/rollback {version}`）**应用历史 schema 并生成新版本**（历史不删除、可再次回滚），写入走与常规编辑同源的规范化 + 校验，历史脏数据不可能绕过校验落库；
- 提交记录 `DynamicFormSubmission.schema_version`（保存时的表单版本，审计口径）；**提交校验始终按提交当时的 schema**（不回放旧版本校验，保持 fail-closed 语义）；
- 历史端点 `GET {pk}/schema-history` 返回版本全文（20 个版本 × 中小型 schema 的响应规模可控），前端用展开行做只读预览（不做差异对比视图）。

### D4 写入侧规范化：顶层收口、字段级维持兼容

- `dataset/utils/dform.py::normalize_schema` 是写入侧唯一入口：字段校验沿用既有 `validate_schema`（只校验不改写，兼容存量 schema 的历史字段属性），**顶层未声明键丢弃**、联动规则校验后落库（缺省不写入 `linkages`，存量 schema 形态零变化）；
- 校验消息可读（目标/触发字段不存在、未知操作符/效果、值缺失、自引用），直接回给前端。

### D5 前端：字段表拆组件 + sortablejs（fallback）+ 规则清单 + 版本弹窗

- `FormFieldTable.vue` 从 `DynamicFormForm.vue` 拆出（行数门禁），承载行内编辑 + 拖拽手柄 + 按钮事件；
- 拖拽用 `sortablejs`（项目既有依赖，dashboard/搜索历史同款）并开 `forceFallback`：走鼠标事件而非原生 HTML5 拖拽（跨浏览器一致，且能被自动化驱动）；**回调里先撤销 Sortable 的 DOM 位移再更新数据**——否则 el-table 的虚拟 DOM 与真实 DOM 失步，会出现「数据已换序但界面不更新」；
- 排序数据路径抽为 `utils/fieldOrder.ts::moveItem`，拖拽与上移/下移按钮共用同一实现（单测覆盖）；
- 联动规则以「清单区块 + 规则弹窗」编辑（弹窗内可多选目标字段，保存时展开为多条同条件规则），规则引用了已删除字段时保存前自动剔除并提示；
- 版本入口在设计器列表行「版本」（主键可用、与编辑弹窗解耦），弹窗内展开行预览该版本字段与联动，具备 `rollback:FormDesigner` 权限才显示回滚按钮。

### D6 权限点

新增 `schemaHistory:FormDesigner`（GET 历史）与 `rollback:FormDesigner`（POST 回滚）两个权限点，由 `sync_menu_permissions --update-seed` 生成并写入 `loadjson/menu.json` + `menumeta.json`。

## 验证

- 后端：联动单测 16 例（操作符矩阵 / 多值交集 / 顺序覆盖 / 自引用与白名单拒绝 / 隐藏跳过必填且剔除 / 动态必填与非必填 / 未声明键丢弃）+ 设计器集成 9 例（规范化落库、非法联动 400、版本递增与归档、同内容不递增、历史端点、回滚生成新版本且历史保留、未知版本拒绝、提交记录版本、隐藏字段不落库、动态必填拒绝）；
- 前端：`linkage.spec.ts` 8 例（与后端同口径的求值矩阵）+ `fieldOrder.spec.ts` 4 例（位移/越界返回原引用）；
- E2E（双浏览器）：`e2e/dform-linkage.e2e.ts` 两例——设计器配两条规则 → 填报页隐藏/显示与动态必填 → 缺动态必填被服务端拒绝 → 补齐提交；改 schema → 版本弹窗看历史 → 回滚 → 版本与字段数回退且历史保留。既有 `dform / dform-depth / dform-flow` 16 例回归通过。

## 边界

- **不做在线建表**（jeecg Online 的「表单即建表」形态）：schema 仍落 JSON 字段，业务聚合走数据集/报表路径；在线建表属独立立项（涉及 DDL 权限、迁移与回滚面）；
- 不做字段级公式/计算字段、不做默认值随联动变化（联动只控制可见性与必填）；
- 不做联动规则的传递闭包校验（A 隐藏 B、B 隐藏 C 这类链路按顺序求值即可表达，不做图分析）；
- 版本历史只做「查看 + 回滚」，不做版本差异对比与逐字段合并；
- 拖拽手势未入 E2E（sortablejs fallback 与合成事件时序不稳，chromium/webkit 表现漂移）：数据路径单测覆盖 + 按钮路径 E2E 覆盖 + 手势人工验证，登记为滚动观察项。
