# 框架能力速查（二开 CookBook）

> 面向二次开发者：一页看全"框架给了什么、在哪里覆写、前端怎么对上"。
> "为什么这样设计"见同目录各机制篇章；"第一次建业务模块"见 xadmin-docs
> `example/new-app-*.md` 五篇教程。本文所有代码引用均可在仓库内找到真实出处，
> **`demo` app 是官方活范例**（`demo/views.py` + `demo/serializers/`）。

## 一、一个业务模块的最小组件链

```
demo/models.py          模型（继承 common.core.models.DbAuditModel）
demo/serializers/       序列化器（继承 BaseModelSerializer，声明式字段 → 驱动前端渲染）
demo/views.py           视图（继承 BaseModelSet + Mixin；filterset_class 声明搜索）
demo/urls.py            SimpleRouter 注册
demo/config.py          URLPATTERNS（自动注入总路由）+ PERMISSION_WHITE_REURL（白名单）
config.yml              XADMIN_APPS 注册 app
菜单注册                 权限码/菜单/模型关联（xadmin-docs example/new-app-menu.md）
```

## 二、ViewSet 选型（common/core/modelset/viewsets.py）

| ViewSet | 组成 | 适用 |
|---|---|---|
| `BaseModelSet` | 全量 CRUD + 批量删 + 元数据 | 绝大多数业务资源（默认选它） |
| `ListDeleteModelSet` | 只读 + 删除 + 批量删 | 日志/记录类 |
| `DetailUpdateModelSet` | 详情 + 更新 | 个人设置类 |
| `OnlyListModelSet` | 只读列表 | 下拉数据源 |
| `NoDetailModelSet` | 更新 + 详情（无列表） | 少见 |

不满足时按 mixin 自行组合（每个 Mixin 独立成模块，见下表）。

## 三、内建 Action ↔ 前端 BaseApi 方法对照

| Mixin（modelset/） | 后端 Action | 前端 `BaseApi` 方法（src/api/base.ts） |
|---|---|---|
| ListAction | `list`（GET；`with_meta=1` 内联元数据） | `list()` |
| CreateAction | `create` | `create()` |
| DetailAction | `retrieve` | `retrieve(pk)` / `detail(pk)` |
| UpdateAction | `update` / `partialUpdate`（审计 diff 挂钩 `_audit_*`） | `update()` / `partialUpdate()` |
| DestroyAction | `destroy` | `destroy(pk)` |
| BatchDestroyAction | `batch_destroy`（软删除模型走 `_needs_rowwise_delete` 行级判断） | `batchDestroy(pks)` |
| SearchFieldsAction / SearchColumnsAction / ChoicesAction | `search-fields` / `search-columns` / `choices` | `fields()` / `columns()` / `choices()` |
| OnlyExportDataAction | `export_data`（同步）/ `export_async`（heavy 队列） | `exportData()` / `exportAsync()` |
| ImportAsyncAction | `import_data` / `import_async` / `import_validate` / `import_headers` / `import_templates` | `importData()` 等 5 个 |
| RecycleBinAction | `recycle` / `recycle_restore` / `recycle_purge`（软删除三通道） | `recycleList()` / `recycleRestore()` / `recyclePurge()` |
| RankAction | `rank`（拖拽排序） | — |
| UploadFileAction | `upload` / `get_upload_size` | `http.upload()` |

自定义 action 直接用 DRF `@action`，返回 `ApiResponse`；**docstring 必写**（菜单与访问日志的显示名取自它，见 demo/views.py 的 `push` 范例）。

## 四、常用覆写点（BaseViewSet，modelset/base.py）

| 覆写点 | 用途 |
|---|---|
| `get_queryset()` / `filter_queryset()` | 数据权限全局挂载（`BaseDataPermissionFilter`），**不要绕过** |
| `get_serializer_class()` | 框架支持 **action 级序列化器**：类属性 `{action}_serializer_class`（如 `list_serializer_class`） |
| `get_serializer_related_fields()` / `optimize_queryset()` | 按序列化器**声明字段自动推断** select_related/prefetch_related（列表页消 N+1；勿手写） |
| `paginate_queryset()` | 已内建"导出（`?type=csv|xlsx`）绕过分页"；一般无需覆写 |
| `perform_destroy()` | 删除前钩子 |
| `filterset_class` | 搜索字段（django-filter + `BaseFilterSet`；`PkMultipleFilter` 自定义前端 input_type，见 demo/views.py） |
| `pagination_class` | `DynamicPageNumber(1000)` 控制最大页大小 |
| `ordering_fields` | 排序白名单 |

序列化器侧：`BaseModelSerializer` 已自动生成关联/choice 字段形态（demo/serializers/book.py
有注释说明），自定义输入形态用 `input_wrapper`（下拉/表格选择器/Tab 列等）。

## 五、约定与红线

1. **响应**：统一 `ApiResponse`（`code=1000` 成功）；协议已 JSON Schema 冻结（`docs/schema/`），改动先改 Schema 再补 `tests/unit/common/test_contract_schemas.py`；
2. **审计**：请求级中间件自动落 OperationLog（UpdateAction 做 diff 含 M2M），业务代码**不要手写审计**；
3. **并发防护**：测试库为 sqlite `:memory:`（不支持 `select_for_update`），状态流转用**条件更新 CAS**（范例 `system/utils/approval.py`）；
4. **数据权限**：查询集过滤统一走 `get_filter_queryset`，手写裸 filter 会绕过数据权限与审计口径；
5. **权限码**：PERMISSION 菜单 `name` = `动作:组件名`，且必须关联 `model`（见 example/new-app-menu.md）；
6. **新业务能力一律独立 app**，`system` 不再扩容（ADR-015 / T17 评估结论）。

## 六、前端契约（xadmin-client）

### BaseApi（src/api/base.ts）

构造 `new BaseApi("/api/<资源>/")` 即获得上表全部方法；子类只加自定义 action 方法
（范例 `src/api/system/task.ts`）。`formatParams` 自动处理序列化，勿在页面手拼查询串。

### RePlusPage（src/components/RePlusPage/）

- **Props 权威源**：`src/components/RePlusPage/src/utils/types.ts` 的 `RePlusPageProps`
  （api / auth / localeName / selection / immediate / operation / isTree / recycleBin /
  pagination / plusSearchProps / pureTableProps / addOrEditOptions /
  operationButtonsProps / tableBarButtonsProps / listColumnsFormat / detailColumnsFormat /
  searchColumnsFormat / baseColumnsFormat / searchResultFormat / beforeSearchSubmit ...）；
- **emits**：`rowClick`、`searchComplete`（携带 routeParams/searchFields/dataList 响应式引用）；
- **expose**：`dataList` / `searchFields` / `getTableRef` / `getSelectPks` / `getPageColumn` /
  `handleGetData` / `handleAddOrEdit`；
- **用法**：`new BaseApi(...) + <RePlusPage :api :auth locale-name .../>`；
  逻辑写在页面 `utils/hook.tsx`，单文件 ≤400 行（CONTRIBUTING 红线）；
- 页面组件 `name` 必须唯一——它是权限码 `动作:组件名` 的匹配键。

### 权限

- 页面级：`getDefaultAuths(instance, [...自定义动作])` 生成权限 map；
- 按钮级：`hasAuth("动作:组件名")` 或 `<Auth value="...">`；
- 三层权限机制见 `docs/architecture/{permission,data-permission,field-permission}.md`。
