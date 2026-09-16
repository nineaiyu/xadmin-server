# ADR-043：远程联想（suggestions）接口

- 状态：**已实施（2026-09-16，用户拍板重开）** —— 首个消费方：审批委托「代理人」（`ApprovalDelegationViewSet` +
  `suggestion_fields = ("delegate",)` 字段白名单）；
  本文第三节方案 C 为实施口径，实施记录见 §八
- 日期：2026-09-16
- 关联：`docs/plans/README.md`（候选池「suggestions 候选接口」）、[ADR-035](ADR-035-api-contract-governance.md)（契约纪律/命名约定）、
  [ADR-039](ADR-039-open-platform-phase2.md)（应用四级授权/PAT scope）、`docs/architecture/菜单权限与字段同步补全方案-2026.09.md`（权限点登记口径）

## 一、背景

表单与筛选栏里的关联字段（FK / M2M）"选一个值"目前只有两条路，都存在规模化或成本问题；
候选池登记的「suggestions 候选接口」想补第三条（输入联想）。本 ADR 的产出是：**判定当前不立项**，
并把将来真要做时的方案、边界、门禁一次性钉死。

## 二、现状（2026-09-16 实测）

| 路径 | 机制 | 边界 |
|---|---|---|
| ① 全量 choices 下拉 | `BasePrimaryKeyRelatedField.get_choices()` 把关联表前 N 行塞进 `search-columns` / `search-fields` | N = `SEARCH_CHOICES_MAX_COUNT`（默认 **200**），超出置 `choices_truncated` |
| ② 远程弹窗选择器 `api-search-*` | 手写 `input_type="api-search-user"` → 前端渲染 `RePlusSearch`（el-select 内嵌整张 RePlusPage） | 现仅 **user / dept / role / menu** 四套；每套 = ViewSet + 路由 + 前端组件 + 注册 + 权限点 |
| ③ 输入联想 | **不存在** | 前端全仓 `remote-method` / `remoteMethod` 零命中 |

证据：

- 截断机制与配置：`common/core/fields.py:170-211`（`get_choices`）、`common/core/fields.py:37-60`（配置读取 + 60s 进程内缓存）、`common/core/config.py:216-224`（`SEARCH_CHOICES_MAX_COUNT` 默认 200）；
- 前端降级：`xadmin-client/src/components/RePlusPage/src/utils/columns.tsx:53-67`（`choices_truncated` → 强制 `filterable` + DEV 告警引导改 `api-search-*`）；
- 远程选择器四件套：`system/views/search/{user,dept,role,menu}.py` + `system/urls.py:137-140` + `xadmin-client/src/views/system/apiSearch.ts`（异步组件注册）+ `xadmin-client/src/views/system/components/Search*.vue`；
- 权限链：新增子路径 action **必须显式处理**，否则一律 403 —— `common/core/permission.py:247-268` 里 `search-columns$` 与 `export|import-*` 两条 URL 特例即为此存在。

## 三、候选方案对比

| 方案 | 形态 | 权限 / 数据门 | 判定 |
|---|---|---|---|
| A 统一端点 + 模型白名单 | `GET /api/system/suggestions?model=<label>&search=` | 需新增权限点 + 自造模型白名单防枚举；数据权限要自己拼，易与字段校验口径分叉 | ❌ 新增枚举面，"谁能联想哪些模型"变成新决策 |
| B 复制现有模式 | 继续加第 5、6、7 套 `SearchXxx` | 每套都要新权限点 + 前端组件 + 注册 | ❌ 它就是现状成本的来源，不是解法 |
| **C 引用方 suggestions** | 在**引用方** ViewSet 混 `SuggestionsAction`：`GET {prefix}/suggestions?field=<field>&search=&pks=` | 复用该 ViewSet 的 **list 权限点**（`_resolve_menu_pk` 加 `suggestions$` 特例，与 `search-columns` 同口径）+ 复用字段自身 queryset（已过 `get_filter_queryset` 数据权限与字段权限） | ✅ **推荐**：候选集与写入校验同源，零新权限点、零枚举面 |

方案 C 优于 A 的根本理由：候选集直接取 `serializer.fields[field]`（`BasePrimaryKeyRelatedField`）的 `get_queryset()`，
与 `to_internal_value` 共用同一 queryset，不会出现"下拉里能选、提交报 `does_not_exist`"的口径分叉。

## 四、推荐设计（触发时照此实施）

### 后端

1. `common/core/modelset/metadata.py`（或新的 `suggest.py`）新增 `SuggestionsAction`，形态对齐 `SearchFieldsAction`：`@action(methods=["get"], detail=False, url_path="suggestions")`。
2. 参数：`field`（必填，白名单 = 当前 serializer 的 related 字段）、`search`（icontains，最小长度 1~2）、`pks`（编辑回显批量取 label，逗号分隔）、`limit`（默认 20，硬上限 50）；**不分页**。
3. label 复用字段的 `attrs` + `format`（即 `to_representation` 已有的 `{pk, ..., label}`），与列表/详情展示口径一致。
4. 权限：`common/core/permission.py::_resolve_menu_pk` 增加 `(?P<url>.*)/suggestions$` 特例，回落到父资源的 list 权限点；应用凭证与 PAT scope 由 `IsAuthenticated.has_permission` 统一覆盖（无需额外接线）。
5. 元数据暴露：`search-columns` 对 related 字段附加可选字段 `suggest_url`（仅当该 ViewSet 混入了 action）；`choices` 与 `choices_truncated` 语义**保持不变**（向后兼容）。
6. 防滥用：最小搜索长度 + 硬上限 + 确认现有 throttle 覆盖该端点；禁止不带 `search` 且不带 `pks` 的全量调用。

### 前端

7. 新增 `ReSuggestSelect`（debounce 300ms + 请求序号防竞态 + 多选 + `pks` 回显），注册进 `columns.tsx` 渲染器表；**保留** `RePlusSearch` 弹窗给"需要看多列再选"的场景，二者并存。

### 契约与门禁

8. `search-columns.schema.json` 新增可选字段 → 同批更新 `xadmin-server/docs/schema/` 源 + `xadmin-client/contract/schema/` 镜像 + `test_metadata_schema.py`（ADR-035 纪律，三者缺一视为未完成）。
9. 测试：越权矩阵（无 list 权限 403 / 数据权限裁剪 / 字段权限裁剪 / 非白名单 field 拒绝 / `pks` 越权不回显）、`limit` 与最小长度、E2E 主链路双浏览器。
10. 门禁：`pytest` + ruff；前端 typecheck / typecheck:strict / eslint / prettier / stylelint / vitest / `check:contract`；改后端必跑 `test:e2e:fresh`。

工作量估算：≈2~3 天（含测试与契约同步）。

## 五、不适用场景登记：菜单管理「自动添加 API 权限」

> 结论：**不需要** suggestions。登记在此，防止后续重复评估。

事实链：

- 入口：`system/views/admin/menu.py:142-161` `POST {pk}/permissions`（"自动添加API权限"）；
- 前端：抽屉内「视图」多选下拉（`xadmin-client/src/views/system/menu/utils/useMenuPermissions.tsx:45-78`），`filterable + multiple`，按 `item.view.split(".").pop()` 本地匹配；
- 数据源：`GET {prefix}/api-url` → `get_all_url_dict()`（`common/core/utils.py:90-98`）递归遍历 `ROOT_URLCONF`，返回 `{name, url, view, label}` 全量；前端进页面时拉一次（`useMenuData.ts:32-46`）。

不需要的四条理由：

1. **数据源性质不同**：这是运行时路由表（静态、随代码发布变化、百级量级、去重后视图约百个），不是会持续增长的数据库关联表 —— suggestions 要解决的"表可能上万行"在这里不成立；
2. **无数据权限 / 无字段权限 / 无分页语义**：路由清单对所有有该页面权限的管理员一致，没有"按人裁剪"的需求；
3. **已经全量在本地**：一次性请求 + `filterable` 本地过滤，百级选项的交互成本可忽略；
4. **低频管理操作**：不是业务高频表单，没有"每次打开页面都下发 200 条 choices"的重复成本。

若确实觉得"搜不到"，更便宜的解法（**零后端改动**）：给该 el-select 加 `filter-method`，同时匹配 `view` 全路径与 `item.label`（中文描述），而不是只匹配末段类名。
另一个可选项是把 `api-url` 结果做前端缓存（运行时路由表每次进页面都递归重算一次），但那是缓存问题，不是搜索形态问题。

## 六、暂缓理由与重开条件

暂缓理由：当前**没有任何字段真正需要**第三条路径 —— user / dept / role / menu 已有 `api-search-*`，
其余关联表规模普遍在 200 行以内，且前端零联想式交互诉求。缺消费方而先建通用能力，是直接违反
「候选池启动前需产品优先级确认」与「先 bundle 分析再立项」类纪律的（参考 ReIcon 实测后关闭的教训）。

重开条件（**任一命中即实施本 ADR 第四节**）：

1. 出现**第 5 个**需要远程搜索的关联实体（现有四套之外的业务模型互相引用，如 表单提交→表单、数据集→模型）；
2. 实测到业务页字段带 `choices_truncated`（排查法：对主要业务页拉 `search-columns` 筛该标记，或统计关联表行数 > 200 的模型）；
3. 出现"选一个值"被弹窗明显拖慢的高频表单，且产品明确要求改为输入联想。

命中后仍需遵守：先补首个消费方页面作为验证载体，再铺开。

## 七、附：相邻内务项（common/decorators.py）

与 suggestions 同批被问及，但不属本 ADR 范围，结论登记在候选池：

- 261 行实为 4 组语义、仅 3 个 import 点：`cached_method`（2 处：`message/base.py`、`common/core/utils.py`）、`Singleton`（1 处：`common/startup.py`）、
  防抖/延迟执行基础设置（**0 业务消费**，仅 `tests/unit/common/test_decorators.py`）、`on_transaction_commit`（**0 消费**）；
- 真正值得做的是把 `common/decorators.py:64-70` 的**模块级副作用**（import 即起守护线程 + `ThreadPoolExecutor(10)`）改为惰性初始化 —— 每个进程都能少一个常驻线程和 10 个 worker；
- 按域拆包（cache / debounce / transaction / singleton + `__init__` re-export 兼容）只有绑在惰性化一起做才有意义，单独拆是纯 churn；
- 不删 `delay_run` / `merge_delay_run` / `on_transaction_commit`（框架对外能力，教程与分析文档有记载），仅文档标注"当前无内部消费"。

## 八、实施记录（2026-09-16）

| 层 | 落点 |
|---|---|
| 后端 Action | `common/core/modelset/suggest.py`：`SuggestionsAction`（`GET {prefix}/suggestions?field=&search=&pks=&limit=`；候选集 = `serializer_class` 字段自身 `BasePrimaryKeyRelatedField.get_queryset()`；label 复用字段 `attrs/format`；`pks` 优先于 `search`；无 `search` 无 `pks` 返回空；`limit` 硬上限 50）+ `expose_suggest_url` 元数据挂钩 |
| 字段级白名单 | ViewSet 声明 `suggestion_fields`（元组/集合）：**名单内的关联字段**才下发 `suggest_url` 并接受 `field` 参数，未声明或字段不在名单 → 零变化 / 1001。同一表单可混合形态——审批委托：代理人走联想、委托人保持弹窗。元数据与端点校验共用同一份声明，防绕过元数据直接枚举其它关联字段 |
| 白名单内字段读取 | `get_serializer_class()(..., ignore_field_permission=True)` 取**类型定义**字段（非权限裁剪后实例——非超管无字段权限配置时 fields 全裁，会误判 1001）；**输出**仍按 `request.fields` 逐字段裁剪（`BasePrimaryKeyRelatedField.get_allow_fields` 独立读 request 级标志） |
| 权限特例 | `common/core/permission.py::_resolve_menu_pk` 加 `suggestions$` 剥离，回落资源 list 权限点（`test_falls_back_to_list_permission` / `test_denied_without_list_permission` / 数据权限 fail-closed 守护） |
| 元数据 | `metadata.py::search_columns` 经 `expose_suggest_url` 仅对 `input_type="api-search-*"` 字段下发 `suggest_url`；**`with_meta=1` 内联路径**（页面首开元数据来自 list 内联）同样生效——`get_suggest_url` 兼容两种调用形态（`/search-columns` 后缀剥离 / list 路径直接拼接），此坑有 `test_inline_metadata_with_meta_exposes_suggest_url` 守护 |
| 首个消费方 | `ApprovalDelegationViewSet` 混入并声明 `suggestion_fields = ("delegate",)`——**代理人**在新建/编辑弹窗走输入联想；**委托人**未入白名单，保持 `api-search-user` 弹窗选择器（2026-09-16 用户决策：部门管理的部门主管不采用联想，已移除该侧混入） |
| 前端组件 | `RePlusPage/src/components/SuggestSelect.vue`：el-select remote + 300ms 防抖 + 请求序号防竞态 + `pks` 回显补 label；**关键词不信 EP remote-method 回调参数**（自动化环境下曾收到翻倍值），直接读内部 input 的 DOM 真值；**候选列表为「已选常驻项 + 本次响应」整体重建**——不能用追加合并（remote 下拉渲染全部 options 且无本地过滤，空结果搜索会残留上一轮候选，实测搜 hh 仍显示 xadmin）；已选项必须常驻 options（EP 单选 selectedLabel 按 pk 从 options 取 label，缺失则显示裸 pk） |
| fetcher 注入 | `RePlusPage/src/utils/suggest.ts` 注册表（同 apiSearch 模式，框架层不依赖 http）；业务侧 `src/views/system/apiSearch.ts` 启动时注册 |
| 渲染器 | `renderers-form.tsx::formFallbackRenderer`：`api-*` 分支优先 `suggest_url` → SuggestSelect；未下发维持 `api-search-*` 弹窗 |
| 契约 | `search-columns.schema.json`（服务端源 + 前端镜像）加可选 `suggest_url`；前端类型 `@/api/types.ts` 同步；`check:contract` 通过 |
| 测试 | 后端 `tests/integration/system/test_suggestions.py` 14 例（契约/越权/数据权限裁剪/limit 硬上限/pks 回显/many 字段/内联元数据）；E2E `e2e/suggest.e2e.ts` 双浏览器（联想选人 → 保存 → 列表回显 → 二次编辑回显）；E2E 三坑已录 `e2e/README.md` 教训表 |
| 门禁 | 后端 pytest 全量 + ruff；前端 typecheck / typecheck:strict / eslint / prettier / vitest 250 / check:contract；`suggest.e2e.ts` 双浏览器通过 |
