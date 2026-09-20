# 元数据协议规范（search-columns / search-fields）

> 面向二开者与协议消费方：两个元数据接口的**字段语义、`input_type` 推断与注册表扩展方式、
> 与权限体系（尤其字段权限）的关系**。响应结构已用 JSON Schema 冻结于
> [docs/schema/](../schema/README.md)（服务端契约测试持续校验真实响应）；本文解释"为什么长这样"。
> 前端消费侧速查见 [framework-cookbook.md](framework-cookbook.md) §六。

## 一、接口与来源（谁生产、谁消费）

| 接口 | 内容来源 | 消费方 |
|---|---|---|
| `GET /api/<app>/<model>/search-columns` | `BaseModelSerializer`（`Meta.fields` + `Meta.table_fields` + `Meta.tabs`） | 列表列 / 编辑表单 / 详情抽屉（`RePlusPage`） |
| `GET /api/<app>/<model>/search-fields` | `filterset_class`（django-filter 声明字段） | 搜索表单 |
| `GET /api/<app>/<model>/choices` | `choices_models` 各模型的 choice 字段聚合 | 下拉数据源兜底 |
| 列表接口 `?with_meta=1` | `ListAction.inline_metadata` 把前两者内联进列表响应 | 页面首开 3 请求合 1 |

- 实现位置：`common/core/modelset/metadata.py`（Action）+ `common/drf/metadata.py::SimpleMetadataWithFilters`
  （字段信息装配，经 DRF `DEFAULT_METADATA_CLASS` 挂载）；
- **序列化器是唯一真源**：改字段先改序列化器，前端零改动（渲染器按 `input_type` 分派）。

## 二、字段语义（search-columns 条目）

| 键 | 语义 | 备注 |
|---|---|---|
| `key` / `label` / `help_text` | 字段名 / 显示名（优先模型 `verbose_name`）/ 描述 | label 兜底链：字段 label → 模型 verbose_name → 字段名 |
| `input_type` | **渲染分派键**（见 §三） | 前端四通道注册表按它选择渲染器 |
| `required` / `read_only` / `write_only` | 表单校验与显隐 | 来自序列化器字段 |
| `default` / `max_length` / `min_value`… | 表单默认值与约束 | 仅在非空时下发 |
| `choices` | 选项数组（`{value,label,color?}`）；关联字段为候选集（`{pk,value,label}`） | 关联候选超 `SEARCH_CHOICES_MAX_COUNT`（默认 200）截断并带 `choices_truncated` |
| `multiple` | M2M / 多选 | 值为数组 |
| `table_show` | 列表列序号（`table_fields` 下标 +1；未声明 `table_fields` 时全部为 1） | **缺列 = 后端没声明，前端无本地列定义** |
| `tabs_index` / `tabs_label` | `Meta.tabs` 分组 | 分组表单 |
| `suggest_url` | 远程联想地址（`{prefix}/suggestions`） | 仅 `input_type=api-search-*` 且 ViewSet 混入 `SuggestionsAction` 并声明 `suggestion_fields` 白名单（ADR-043） |

search-fields 条目为子集：`key/label/help_text/input_type/choices/default`（多选默认 `[]`）+
`ordering` 特殊条目（`ordering_fields` 生成升降序选项）。

## 三、`input_type` 推断链与注册表

**服务端推断链**（`search-columns` 对每个序列化器字段执行）：

1. 字段显式携带 `input_type`（`input_wrapper(某字段)(input_type=...)` 包装，或内核字段类自带）——最高优先；
2. 前后缀修饰（`get_format_intput_type`）：`input_type_prefix` / `input_type_suffix`，
   如上传关联自动加 `_file` 后缀；
3. 类型判定 `common/drf/metadata.py::get_field_type`：
   - **必须 isinstance**：`LabeledMultipleChoiceField → labeled_multiple_choice`、
     `LabeledChoiceField → labeled_choice`（含子类 `DictChoiceField`）、
     `BasePrimaryKeyRelatedField → object_related_field`、`ManyRelated → m2m_related_field`；
     类名精确匹配会让业务子类退化成 `choice`/未知，前端拿到对象值渲染空白（历史缺陷，已加守护）；
   - 其余回退 DRF `label_lookup`；
4. choice 字段附加 `choices`；`DictChoiceField.choice_colors` 把颜色映射并进每个选项（前端彩色 tag）。

**客户端注册表**（四通道互不兜底，漏一侧即空白/`[object Object]`）：

| 通道 | 登记处 | 备注 |
|---|---|---|
| 列表 | `renderers-*.tsx` 的 `cellRenderer` | 只认 `cellRenderer` |
| 详情 | 同文件 `render` + `valueType` | 只认 `render`/`valueType` |
| 表单 | `renderers-form.tsx` | 详情有渲染器的类型必须可编辑，否则登记 `FORM_EXEMPT_TYPES` 并写理由 |
| 搜索 | `renderers-search.tsx` | 新键登记 `SEARCH_REGISTRY_TYPES` |

成对守护：`renderers-pairing.spec.ts`（未分类即测试失败）。

### 前端三通道内置渲染器清单（源码事实）

三个注册表各自内建 `input_type → handler`（文件：`RePlusPage/src/utils/renderers-{search,form,detail}.tsx`）：

| 通道 | 内置 `input_type` 键 | fallback 行为 |
|---|---|---|
| 搜索（`renderers-search.tsx`） | `text`、`datetime`、`datetimerange`、`number`、`select`、`select-multiple`、`select-ordering` | 命中 `api-` 前缀走 `api-search-*` 组件；其余 `valueType = input_type` |
| 表单（`renderers-form.tsx`） | `integer`、`float`、`string`、`field`、`color`、`datetime`、`date`、`boolean`、`textarea`、`choice`、`multiple choice`、`labeled_choice`、`labeled_multiple_choice`、`object_related_field`、`m2m_related_field`、`object_related_field_file`、`object_related_field_image`、`m2m_related_field_file`、`m2m_related_field_image`、`image upload`、`file upload`、`list`、`phone`、`json` | 命中 `api-` 前缀走 `api-search-*` 组件；`suggest_url` 存在时升级为远程联想 `SuggestSelect` |
| 详情/表格（`renderers-detail.tsx`） | `labeled_choice`、`color`、`object_related_field`、`m2m_related_field`、`labeled_multiple_choice`、`json`、`object_related_field_image`、`object_related_field_file`、`m2m_related_field_file`、`m2m_related_field_image`、`image upload`、`file upload`、`boolean`、`list` | **无 fallback**——未登记类型不配置渲染，靠 plus-pro/pure-table 按 `valueType` 原生输出 |

> 速查提示（读源码才容易踩的边界）：
> - **基础类型不占详情通道**：`integer`/`float`/`string`/`date`/`datetime`/布尔/`choice` 等由服务端
>   DRF `label_lookup` 回退产出，详情/表格侧没有专门 handler，靠 `valueType` 原生渲染；
>   `datetime`/`date` 的列表格式化 `cellRenderer` 实为表单通道一并下发（同文件内）。
> - **`api-search-*` 是「业务启动时注册」的全局表**（`main.ts` 引入 `@/views/system/apiSearch` 完成登记）；
>   自定义该类类型必须同步 `registerApiSearchComponents`，否则 DEV 告警且字段静默空。
> - **详情无 fallback**：对象/数组值类型若漏登记详情 `render`，会退化成 `[object Object]`——这是四通道"
>   "互不兜底"最易翻车的一侧（`renderers-pairing.spec.ts` 用成对守护拦截）。

### 新增 `input_type` 五步清单

从服务端声明一个新 `input_type` 到前端四通道可用，按序核对（详见
[framework-cookbook.md](framework-cookbook.md) §六「新增 input_type 检查清单」）：

1. **服务端判定**：`common/drf/metadata.py::get_field_type` 用 `isinstance` 分支产出该 `input_type`
   （或字段类自带），并补 `tests/unit/system/test_data_dict.py` 同款守护；
2. **契约同步**（若涉及响应结构）：改 `docs/schema/` → `pnpm sync:contract` 镜像并重新生成
   `src/api/types/*.d.ts`；
3. **四通道登记**：搜索 `renderers-search.tsx`、表单 `renderers-form.tsx`、详情 `renderers-detail.tsx`
   （对象/数组值需同时给详情 `render` 与列表 `cellRenderer`），并在 `renderers-pairing.spec.ts` 分类清单登记
   （表单不可编辑的类型登记 `FORM_EXEMPT_TYPES` 并写理由；新搜索键登记 `SEARCH_REGISTRY_TYPES`）；
4. **取值口径**：`labeled_*` 系列值为 `{value,label,color?}`；关联字段为 `{pk,label}`……按既有渲染器
   同口径处理 `ElTag` 文字/边框覆盖（`src/utils/dict.ts`）；
5. **门禁**：`pnpm vitest`（成对守护）+ `pnpm typecheck:strict` + `pnpm check:contract`；
   改后端元数据后重启容器再跑 `pnpm test:e2e:fresh` 覆盖该字段的列表与详情。

## 四、与权限体系的关系

1. **字段权限是元数据的上游**：`BaseModelSerializer.__init__` 经 `get_allow_fields`
   把无权限字段**直接从 `serializer.fields` 移除**——因此该字段既不出现在数据里，也
   **不出现**在 search-columns 元数据中（列/表单/搜索同源消失）。元数据不是独立裁剪面，
   不存在"元数据有、数据没有"的二次授权缝隙；
2. **API 权限同口径**：`search-columns` / `search-fields` / `suggestions` 的权限点匹配
   剥掉尾缀后与 `list` 一致（`common/core/permission.py::_resolve_menu_pk`）——
   能进列表就能拿元数据，反之列表权限未授权时元数据同样 403；
3. **值级脱敏在上游**：脱敏（数据掩码）作用于序列化输出（`to_representation`），豁免口径与
   `get_allow_fields` 一致（超管 / 显式豁免 / 原文通道）；元数据只描述形态，不携带明文；
4. **数据权限在查询集**：与元数据无关；列表行级可见范围见 [permission.md](permission.md)。

## 五、失败可见性（防静默）

- `search-fields` 整体无法构建（filterset 配置异常）→ 返回 `code=500` 的**显式失败**，
  不下发"成功但残缺"的元数据让前端静默降级；
- 单字段装配失败 → 跳过该字段并记 error 日志，不牵连其余字段；
- 前端：列表请求完成后元数据仍未到达 → DEV 环境页面顶部出现排查指引警示条（元数据到达自动清除）；
  新增页面"表格有数据但空白列"优先核对序列化器 `table_fields`（新手陷阱清单 [dev-pitfalls.md](../dev-pitfalls.md)）。
