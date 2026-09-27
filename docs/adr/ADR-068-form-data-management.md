# ADR-068：表单数据（管理端只读数据面）

- 日期：2026-09-27
- 状态：**已交付**（后端 11 例集成 + 权限点覆盖守护；前端 11 例单测 + E2E 双浏览器 4 例）
- 背景：[ADR-067](ADR-067-online-table-evaluation.md) D2 登记的 **B2（表单数据页）** 触发条件命中——管理员需要在页面上按表单浏览 / 筛选 / 导出「某个表单的全部提交」，现状只能走导出或数据集配置，路径不直观。本 ADR 记录 B2 的实施口径与边界。

## 决策

### D1 独立只读端点，不给「我的填报」加管理视角

新增 `DynamicFormDataViewSet`（`/api/dataset/form-data`，basename `form-data`），只含 `list / retrieve / export-data / export-async` + `form-options / user-options` 两个数据源动作。**不**在既有 `dynamic-form-submissions`（「我的填报」）上按权限点切数据面：

- 同一 URL 无法区分两个权限点（权限链按 path 匹配，命中的菜单上下文只有一条）；
- 提交与修改留在「我的填报」，管理端保持只读（`POST/PATCH/DELETE` 明确 405），避免两处写路径并发维护。

### D2 行可见域 = 数据权限编译器（不新开旁路）

管理端列表**不做 creator 隔离**，取值域交给全局默认的 `BaseDataPermissionFilter`：

- 超管全量；非超管按角色 / 部门的数据权限授权规则收敛可见行；**未配置授权即空集**（fail-closed，与系统其它管理列表同款两层口径：页面权限点 × 行级授权）；
- `PERMISSION_DATA_AUTH_APPS` 加入 `dataset`——让 `dataset.dynamicformsubmission` 进入「数据权限」规则的表树（`sync_model_field` / `post_migrate` 自动重建 DATA 树），否则非超管无授权表可配、恒空集；
- 授权菜单维度选择「表单数据」或留空（通用）。

### D3 权限点与数据源动作

`sync_menu_permissions` 生成 5 个权限点挂在「表单数据」菜单（`PARENT_MENU_MAP` 登记 `api/dataset/form-data → FormData`）：

| 权限点 | 端点 |
|---|---|
| `list:FormData` | `GET api/dataset/form-data$` |
| `retrieve:FormData` | `GET api/dataset/form-data/(?P<pk>[^/.]+)$` |
| `exportData:FormData` | `GET api/dataset/form-data/export-data$` |
| `exportAsync:FormData` | `POST api/dataset/form-data/export-async$` |
| `formOptions:FormData` | `GET api/dataset/form-data/form-options$` |

`form-options` / `user-options` 用 `shared_list_action` 声明（剥后缀按父级 list 权限解析），运行时与前端交互不需要额外授权动作。

### D4 契约与复用

- **列表 / 详情契约分离**：`FormDataListSerializer`（固定列 + `data` 全量，动态列渲染所需）与 `FormDataDetailSerializer`（+ `form_schema` / `approval_trail` / `instance`，详情抽屉渲染）；列表不携带详情字段，避免逐行展开 schema 与流程任务造成载荷膨胀。
- **导出复用**：`SubmissionExportSerializer`（固定列 + 按涉及表单 schema 展开的动态列）与 `export_dynamic_fields()` 从视图层抽到 `dataset/serializers/dform.py`，「我的填报」与「表单数据」两条导出口径共用（导出范围自动跟随过滤条件与行级可见域）。
- **前端**：新页 `views/form/data/`（顶部「选择表单」入口 + RePlusPage：动态列按所选表单 schema 展开、行操作只保留「详情」+ 框架导出）；切换表单以 `key=表单 pk` 重建表格（列集合随 schema 变化）；提交详情抽屉组件提升为 `views/form/components/SubmissionDetail.vue` 供两页共用。

## 边界

- 不做管理端编辑 / 删除 / 重新提交（申请人操作，留在「我的填报」）；
- 不做 JSON 内部字段级筛选（`data.kind` 既非 B2 承诺也非模型字段，属 **B1 数据集 JSON 路径列** 的登记面）；
- 不做跨表单合并视图（动态列语义以「一次一个表单」为准；跨表单导出仍由 `export-data` 的多表单合并列承担）；
- 历史字段可见性（schema 快照）维持 ADR-067 登记——导出与列表列均取当前 schema。

## 验证

- 后端：`tests/integration/dataset/test_form_data_api.py` 11 例（超管全量 / 表单与状态筛选 / 详情契约 / 表单选项含停用与模板排除 / 导出动态列 / 数据权限三态[无授权空集、仅本人、全部数据] / 导出口径同源 / 权限点缺失 403 / 只读边界 405）；
- 权限点覆盖：`tests/unit/system/test_permission_seed_coverage.py` 0 缺口（菜单种子已回写）；
- 前端：`views/form/data/utils/__tests__/format.spec.ts` 11 例（字段值展示口径）；
- E2E：`e2e/dform-data.e2e.ts` 双浏览器 4 例（选表单浏览动态列 + 详情抽屉 + 导出下载）。

## 部署注意

1. `migrate` 无需新迁移（无模型变化）；`load_init_json` 或定向 upsert 写入新菜单与 5 个权限点；`sync_model_field`（或管理页「生成数据」）重建数据权限表树后，`dataset.dynamicformsubmission` 才出现在「数据权限」规则选择器；
2. 新菜单默认**不授任何角色**（最小权限）：需要在角色页为管理员角色勾选「表单采集 → 表单数据」页面及其权限点，并（可选）配置行级数据授权；
3. 前端需重新构建部署（新增 `form/data` 页面与词条）。
