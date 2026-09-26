# 组件手册（二次开发）

> 定位：回答"框架里**有哪些组件**、每个组件**怎么用 / 怎么配 / 往哪扩**"。
> 与相邻文档的分工：
>
> | 我想…… | 读这篇 |
> |---|---|
> | 第一次动手建一个模块 | [guide/first-module-30min.md](../guide/first-module-30min.md) |
> | 按任务找步骤（加字段 / 加按钮 / 加任务……） | [guide/recipes.md](../guide/recipes.md) |
> | 在两个方案之间做选择 | [方案选型与对比.md](方案选型与对比.md) |
> | 理解某个机制的设计与边界 | [overview.md](overview.md) → 各机制篇章 |
> | 改内核前确认边界 | [common/README.md](../../common/README.md) |
>
> 本文每个组件都标注**权威源**（类型/实现的唯一事实处）——文档与代码冲突时以权威源为准，
> 改组件时先改权威源、再回来同步本页。

## 〇、全景图

```
后端（xadmin-server）                          前端（xadmin-client）
┌──────────────────────────────┐              ┌──────────────────────────────┐
│ 业务 app    models / serializers / views /  │  页面层    views/**（RePlusPage 声明式页面）
│             services / tasks / modules.py   │           └ utils/hook.tsx 逻辑收敛层
├──────────────────────────────┤              ├──────────────────────────────┤
│ 工程层      server/（settings 拼装 / urls / │  组件层    RePlusPage / ReDialog / ReDrawer /
│             asgi / celery / middleware）    │           ReIcon / RePlusSearch / ReAuth …
├──────────────────────────────┤              ├──────────────────────────────┤
│ 内核层      common/（模型基类 / modelset /  │  基础设施  api（BaseApi）/ utils/http /
│             元数据 / 权限 / 缓存 / 任务 /    │           router / store / directives / i18n
│             配置 / 模块裁剪 / SDK）          │
└──────────────────────────────┘              └──────────────────────────────┘
         │                                                 │
         └──────────── 咬合点（§四）──────────────────────┘
   元数据（search-columns / search-fields）· 权限码（动作:组件名）· 契约（docs/schema ↔ contract/schema）
```

## 一、后端组件（xadmin-server）

### 1.1 模型基类与模型字段

| 组件 | 职责 | 使用方式（继承/引用即用） |
|---|---|---|
| `DbUuidModel` | UUID 主键（不可枚举、种子可派生固定 pk） | 所有业务模型默认基类 |
| `DbBaseModel` | `created_time` / `updated_time` / `description` | 经 `DbAuditModel` 自带 |
| `DbAuditModel` | `creator` / `modifier` / `dept_belong`（审计与数据归属） | `class Customer(DbAuditModel)` |
| `SoftDeleteModel` | 软删（`deleted_at` + `objects`/`all_objects`），配套回收站三通道 | **必须放 MRO 首位**：`class Menu(SoftDeleteModel, DbAuditModel, DbUuidModel)` |
| `AutoCleanFileMixin` | 保存/删除时自动清理旧文件 | 模型含 `FileField`/`ImageField` 或关联 `system.UploadFile` 时混入 |
| `AESCharField` / `AESTextField` | 模型字段级加密（`aes:::` 前缀，落库密文） | 少量高敏字段：`from common.fields.char import AESCharField` |
| `signer`（值级加密） | HKDF+AES-GCM（`v3:` 前缀），用于 JSON 值内的敏感键 | `common/base/utils.py::signer.encrypt/decrypt`（webhook secret / AI api_key 同款） |
| `upload_directory_path` | 统一上传路径 `{app}/{model}/{creator}/{pk}/{uuid5}.{ext}` | `upload_to=upload_directory_path` |

- 依赖：仅依赖 `django.db`；`DbAuditModel.dept_belong` 关联 `system.DeptInfo`（数据权限的归属字段）。
- 配置项：无需配置；`Meta.ordering` 必须给默认排序（列表分页依赖，缺失有告警）。
- 扩展点：新增通用字段时在 `common/core/models.py` 加抽象基类（内核），**不要**改 `DbAuditModel` 既有字段语义。
- 权威源：`common/core/models.py`、`common/fields/char.py`、`common/base/utils.py`。

### 1.2 序列化器与字段形态（`BaseModelSerializer`）

| 能力 | 说明 |
|---|---|
| 字段权限裁剪 | 按请求用户的字段白名单直接移除 `self.fields`（元数据同步消失），无需业务代码处理 |
| `Meta.fields` / `table_fields` | `fields` = 接口字段；`table_fields` = 列表默认列与顺序（`index+1` 即元数据 `table_show`） |
| `id` → `pk` | 自动转换，无需声明 |
| `Meta.tabs` | `TabsColumn("分组名", ["字段", ...])` 表单分栏 |
| action 级序列化器 | ViewSet 类属性 `{action}_serializer_class`（如 `list_serializer_class`） |

常用字段形态（均在 `common/core/fields.py`；`system/serializers/fields.py` 仅为兼容别名）：

| 字段 | 元数据 `input_type` | 用途 |
|---|---|---|
| `LabeledChoiceField` | `labeled_choice`（值 `{value,label,color?}`） | 枚举下拉；`DictChoiceField` 是其字典驱动子类 |
| `LabeledMultipleChoiceField` | `labeled_multiple_choice` | 多选枚举 |
| `DictChoiceField(dict_code=..., fallback_choices=..., merge_fallback=...)` | 同上（走字典） | 枚举文案可运营化（字典页在线维护，不发版） |
| `BasePrimaryKeyRelatedField`（`attrs` + `format`） | `object_related_field` | 外键下拉；`attrs: ["pk"]` 输出 `{pk,label,format 渲染结果}` |
| `ManyRelatedField` | `m2m_related_field` | 多对多 |
| `input_wrapper(serializers.SerializerMethodField)(read_only=True, input_type="boolean")` | 自定义 | 只读展示字段 / 自定义渲染类型的注入口 |
| `PkMultipleFilter(input_type="api-search-user")` | 见 §1.4 | 搜索区的远程选择/多选 |

- 依赖：`common/core/fields.py`、`common/drf/metadata.py`（`input_type` 判定，**必须 isinstance**）。
- 配置项：`extra_kwargs`（`input_type` / `attrs` / `format` / `required`）；`Meta.fields_unexport`（导入导出忽略列）。
- 扩展点：自定义 JSON 校验走 `validate()`；新增字段类型 → 先在 `common/drf/metadata.py::get_field_type` 加 isinstance 分支，再走前端四通道（§4.2）。
- 权威源：`common/core/serializers.py`、`common/core/fields.py`；协议见 [metadata-protocol.md](metadata-protocol.md)。

### 1.3 ViewSet 体系（`common/core/modelset/`）

**预组合基类**（`viewsets.py`）——选型直接继承：

| 基类 | 组成 | 适用 |
|---|---|---|
| `BaseModelSet` | 全量 CRUD + 批量删 + 元数据 | 绝大多数业务资源（默认） |
| `ListDeleteModelSet` | 列表 + 详情 + 删除 + 批量删 | 日志/记录类 |
| `DetailUpdateModelSet` | 详情 + 更新 | 个人设置类 |
| `OnlyListModelSet` | 只读列表 | 下拉数据源 |
| `NoDetailModelSet` | 更新 + 详情（无列表） | 少见 |

**Mixin 清单**（每个可单独混入；与前端 `BaseApi` 方法一一对应见 [framework-cookbook.md](framework-cookbook.md) §三）：

| Mixin | 端点 | 说明 |
|---|---|---|
| `CreateAction` / `DetailAction` / `ListAction` / `UpdateAction` / `DestroyAction` | `create` / `retrieve` / `list` / `update`+`partial_update` / `destroy` | CRUD 五件；`UpdateAction` 挂审计 diff |
| `BatchDestroyAction` | `batch-destroy` | 软删/文件清理类模型自动逐行；覆写红线见 cookbook |
| `RankAction` | `rank` | 拖拽排序（上限 1000 条） |
| `ChoicesAction` / `SearchFieldsAction` / `SearchColumnsAction` | `choices` / `search-fields` / `search-columns` | 元数据三端点（后两者**必须成对**，预组合基类已内建） |
| `OnlyExportDataAction` / `ImportAsyncAction` / `ImportExportDataAction` | `export_data` / `export_async` / `import_*` 等 | 同步导出、异步导入导出（heavy 队列，无 worker 自动降级同步） |
| `RecycleBinAction` | `recycle` / `recycle/restore` / `recycle/purge` | 软删模型三通道 |
| `UploadFileAction` | `upload` / `get_upload_size` | 扩展名白名单 + 魔数校验 |
| `SuggestionsAction` | `suggestions` | 远程联想（`suggestion_fields` 白名单） |
| `CacheDetailResponseMixin` / `CacheListResponseMixin` | — | 响应级缓存（配 `cache_response`） |
| `BaseViewSet` | — | 自动 `select_related/prefetch_related` 推断、`paginate_queryset`、action 级序列化器 |

**典型用法**：

```python
class BookViewSet(BaseModelSet, ImportExportDataAction):
    """书籍"""  # docstring 首行 = 操作日志 module 与菜单显示名，必写

    queryset = Book.objects.all()
    serializer_class = BookSerializer
    ordering_fields = ["created_time"]
    filterset_class = BookFilter
    pagination_class = DynamicPageNumber(1000)  # 缺省最大 100 条
```

- 依赖：`common/core/modelset/` 各模块 + `common/core/response.py`。
- 配置项（类属性）：`queryset` / `serializer_class` / `filterset_class` / `pagination_class` / `ordering_fields` / `select_related_fields` / `prefetch_related_fields` / `{action}_serializer_class`。
- 扩展点：覆写点表与红线见 [framework-cookbook.md](framework-cookbook.md) §四；自定义动作 `@action` + `@extend_schema` + `ApiResponse`。
- 权威源：`common/core/modelset/viewsets.py`、`common/core/modelset/base.py`。

### 1.4 过滤与搜索

| 组件 | 职责 | 使用方式 |
|---|---|---|
| `BaseFilterSet` | 预置 `pk` / `created_time` / `updated_time` / `creator` / `dept_belong` 等常用过滤器 | `class BookFilter(BaseFilterSet): ...` |
| `PkMultipleFilter(input_type=...)` | 关联字段搜索（默认多选 pk；`input_type="input"` 变输入框、`"api-search-user"` 变远程选择） | FilterSet 类属性 |
| `BaseDataPermissionFilter` | **数据权限全局挂载点**（默认 filter_backends 已含，勿绕过） | 不改即可；自定义查询走 `get_filter_queryset` |
| 联想（`SuggestionsAction`） | 大表关联字段的远程候选（候选集与写入校验同源） | ViewSet 声明 `suggestion_fields = ("delegate",)`，前端自动升级 `SuggestSelect` |

- 约束：FilterSet 声明的过滤器**必须同时列入 `Meta.fields`**，否则 `search-fields` 不产出。
- 权威源：`common/core/filter.py`、`common/core/data_scope/`；设计见 [permission.md](permission.md)。

### 1.5 元数据通道

两个只读端点（由 §1.3 的 Mixin 提供）：

| 端点 | 来源 | 消费方 |
|---|---|---|
| `GET {base}/search-columns` | 序列化器 + `Meta.table_fields` + `Meta.tabs` | 前端列/表单/详情渲染 |
| `GET {base}/search-fields` | `filterset_class` | 前端搜索区 |
| `GET {base}/choices` | `choices_models` 聚合 | 下拉数据源 |

`input_type` 推断链：字段自带 `input_type`（最高）→ `input_type_prefix/suffix` → `common/drf/metadata.py::get_field_type` 类型判定 → DRF 默认。协议全文（字段语义 / 注册表 / 与字段权限关系 / 失败可见性）见 [metadata-protocol.md](metadata-protocol.md)；性能开关 `?with_meta=1` 把三请求合并为一。

### 1.6 权限组件

| 层 | 组件 | 位置 |
|---|---|---|
| API/菜单权限 | `IsAuthenticated`（白名单 → `get_user_permission` → 菜单 pk 解析） | `common/core/permission.py` |
| 数据权限 | `get_filter_queryset` + `core/data_scope/`（16 种规则，fail-closed） | `common/core/filter.py` |
| 字段权限 | `BaseModelSerializer` 自动裁剪 | `common/core/serializers.py` |
| 应用级授权 | `system/utils/api_grant.py`（仅 PAT 凭证，只收敛不提权） | 三处挂载 |
| 权限点治理 | `get_view_permissions` / `scan_gaps` / `sync_menu_permissions` / `doctor` | `system/utils/menu.py`、`permission_sync/` |
| 前端消费 | `hasAuth("动作:组件名")` / `<Auth>` / `getDefaultAuths` | 见 §2.6 |

- 权限码约定 `{action}:{ViewSetName}`；**新增端点必须登记权限点**（生成器种子或 `sync_menu_permissions`），漏登记 = 非超管 403。
- 深入：[permission.md](permission.md)（体系）、[data-permission.md](data-permission.md)、[field-permission.md](field-permission.md)（配置操作教程）。

### 1.7 统一响应与异常

- `ApiResponse(data=..., detail=...)` → `{code, detail, requestId, timestamp, data}`，`code=1000` 成功；前端 `isSuccess(res)` / `listRows(res)` 唯一拆包点。
- 异常：`common_exception_handler` 统一脱敏归一（JWT 40001/40002、ProtectedError 998、未预期 500 通用文案）；错误码登记制见 [exception-handling.md](../exception-handling.md)。
- 扩展点：新错误码先登记文档再使用；业务失败用 `ApiResponse(code=..., detail=...)`，不要自造响应形状。

### 1.8 中间件与请求上下文

请求链（顺序即执行序，`server/settings/base.py::MIDDLEWARE`）：

```
RequestMiddleware（request_id + 当前请求上下文）
→ ModuleGateMiddleware（停用模块 404）
→ …（Cors / Locale / Csrf / Auth / Message / XFrame）
→ RefererCheckMiddleware（可选开关）
→ CSPModeMiddleware
→ ApiLoggingMiddleware（操作日志，异步落库）
```

- 常用上下文：`server/utils.py`（`get_current_request` / `set_current_request`，当前请求与当前用户）；异步任务构造请求用 `common/core/task_request.py::build_task_request`。
- 扩展点：新增中间件写类后插入 `MIDDLEWARE`（注意响应阶段自内向外）；需要开关时 `raise MiddlewareNotUsed`。范例 `server/middleware.py`。

### 1.9 Celery 任务

| 组件 | 用途 |
|---|---|
| `@shared_task` | 普通异步任务（`{app}/tasks.py`，autodiscover 自动发现） |
| `@register_as_period_task(crontab=… 或 interval=…, name=…, module=…)` | 周期任务声明（`module=` 归属可裁剪模块：模块停用即不注册） |
| `@after_app_ready_start` / `@after_app_shutdown_clean_periodic` | 启动即注册 / 关停清理 |
| `background_task_view_set_job` + `run_view_by_celery_task` | 把 ViewSet action 丢进 heavy 队列跑（批量导入导出用，**无活跃 worker 自动降级同步**） |
| `CELERY_TASK_ROUTES` | 队列路由（default / heavy 双队列，重活加条目） |

- 权威源：`common/celery/decorator.py`、`common/tasks.py`；范例 `system/tasks/`（报表分发、清理任务）。

### 1.10 通知中心（`notifications/`）

| 组件 | 职责 |
|---|---|
| `BACKEND` 枚举 | 渠道清单（email / site_msg / sms / dingtalk / wecom / feishu） |
| `backends/<name>.py` | 渠道客户端：模块级 `backend`（`BackendBase` 子类）即被 `load_backend_clients()` 自动加载——**新增渠道 = 新增一个文件** |
| `register_backend_msg` | 消息类型 ↔ 渠道渲染映射（`notifications/notifications.py`） |
| `@register_message` / `publish` | 消息类型注册与发送入口（站内信/邮件/短信多通道分发） |

- 配置项：渠道开关与凭据在「系统设置 → 通知设置」（值级加密存储）；两层可达性过滤（渠道开关 × 用户订阅）。
- 深入：[notification-channels.md](notification-channels.md)。

### 1.11 配置体系

```
config.yml（config.py）→ 同名环境变量 → 代码默认值   ← server/conf/ 装载
数据库态 SysConfig（管理页可改，热更新）             ← common/core/config/
个人级 UserConfig（真实个人行优先，缺席继承系统级）
```

| 我要加…… | 放在 |
|---|---|
| 一个部署期配置（重启生效） | `config_example.yml` + `server/conf/defaults.py`（两处同名键） |
| 一个运行期配置（管理页可改） | `common/core/config/system_conf.py` 注册 property + `loadjson/systemconfig.json` 种子 + `settings/`（系统设置 app）管理页表单 |
| 一个个人配置 | `common/core/config/user_conf.py` + 个人设置页 |

- 取值链细节与配置速查表（键 ↔ 环境变量 ↔ 默认值 ↔ 生效方式）见 [../ops/deployment.md](../ops/deployment.md) §9。
- 权威源：`server/conf/`、`common/core/config/`。

### 1.12 种子与初始化

| 组件 | 职责 |
|---|---|
| `loadjson/*.json` | 初始种子（菜单/权限点/字典/系统配置/流程模板…… 21 份） |
| `manage.py load_init_json` | 幂等装载：模块裁剪过滤 → 自然键冲突预检（跳过并告警）→ 导入 → 时间戳回填 → 缓存失效 |
| `manage.py dump_init_json` | 反向导出种子 |
| `loaddata loadjson/seed_<app>_<model>.json` | 生成器产出的模块种子（pk 由 uuid5 派生，可重复装载） |
| `utils/init_data.py` | 新库初始化：migrate → load_init_json → 建超管（幂等） |
| `seed_demo_*` 命令族 | 演示数据一键装载 / 卸载（`--clean-only` 对称） |

- 纪律：种子禁引特定环境用户；新权限点要么进生成器种子、要么 `sync_menu_permissions --update-seed` 回写。

### 1.13 管理命令（自检与脚手架）

| 命令 | 一句话 |
|---|---|
| `manage.py doctor` | 八项自检：配置密钥 / 数据库 / Redis / 语言包 / 权限点缺口 / 模块裁剪 / 契约镜像 / 版本一致性，输出问题 + 修复命令，失败非零退出 |
| `manage.py post_upgrade` | 升级后三件套（种子 + 语言包 + 缓存 + 权限缺口扫描），幂等 |
| `manage.py generate_crud <app>.<Model>` | 模型 → 后端四件套 + 前端页面 + 菜单种子（`--dry-run` 预览、`--with-import-export`、`--with-module`、`--parent`） |
| `manage.py generate_module` | 生成 `{app}/modules.py` 可裁剪声明 |
| `manage.py sync_menu_permissions` | 权限点缺口补齐（`--dry-run` 只报告、`--update-seed` 回写种子） |
| `manage.py sync_model_field` | 同步字段权限树（模型字段变更后） |
| `manage.py modules` | 模块清单与配置预演 |
| `manage.py module remove <id>` | 模块硬裁剪（归档可回滚） |
| `manage.py start/stop/status/restart` | 自研进程管理（启动前自动 migrate / 收集静态 / 编译语言包 / 自检） |

### 1.14 SDK（对外能力的唯一入口）

| SDK | 用途 |
|---|---|
| `common/sdk/ai/chat.py` | `chat(messages, **overrides)` / `chat_stream(...)`（OpenAI 兼容，凭据取激活档案） |
| `common/sdk/im/` | 钉钉 / 企微 / 飞书消息发送（token 缓存） |
| `common/sdk/sms/` | 短信发送 |

业务调用 AI/IM/短信一律走 SDK，不要自己拼 HTTP。

## 二、前端组件（xadmin-client）

### 2.1 RePlusPage 体系（列表页一体化组件）

**组成**（`src/components/RePlusPage/`）：页面组件 + 取数/列装配/表单/按钮四组内部 hook + 内置子组件（AddOrEdit / ImportData / ExportData / ChangeHistoryDialog / ReRecycleBin / ButtonOperation…）。

**基本用法**（页面 = 薄壳 + hook）：

```vue
<!-- views/crm/customer/index.vue -->
<script lang="ts" setup>
import { useCustomer } from "./utils/hook";
defineOptions({ name: "CrmCustomer" });   // 必须唯一：权限码 动作:组件名 的匹配键
const { api, auth } = useCustomer();
</script>
<template>
  <RePlusPage :api="api" :auth="auth" locale-name="crmCustomer" />
</template>
```

| 项 | 清单 |
|---|---|
| 关键 props | `api` / `auth` / `localeName` / `selection` / `operation` / `tableBar` / `immediate` / `isTree` / `recycleBin` / `pagination` / `addOrEditOptions` / `operationButtonsProps` / `tableBarButtonsProps` / 五个 `*Format` 格式化出口 / `beforeSearchSubmit` |
| emits | `rowClick` / `searchComplete` / `selectionChange` / `tableBarClickAction` / `operationClickAction` |
| expose | `dataList` / `searchFields` / `getTableRef` / `getSelectPks` / `getPageColumn` / `handleGetData` / `handleAddOrEdit` |
| 权威源 | `src/components/RePlusPage/src/utils/types.ts`（`RePlusPageProps`） |

**格式化出口**（定制渲染的主入口；按下列执行顺序，后执行的覆盖先执行的）：

| 出口 | 时机 |
|---|---|
| `searchResultFormat(result)` | 列表响应后、渲染前（改行数据） |
| `listColumnsFormat` / `detailColumnsFormat` / `searchColumnsFormat` | 对应列集合装配后 |
| `baseColumnsFormat({listColumns, detailColumns, searchColumns, addOrEditRules, addOrEditColumns, ...})` | 最后统一改写（含表单规则） |

**操作按钮体系**（`ButtonOperation`，权威源 `src/components/RePlusPage/src/components/ButtonOperation/src/types.ts`）：

```ts
:operationButtonsProps="{ showNumber: 5, width: 260, buttons: [{
    text: t('customer.export'), code: 'export',
    show: auth.exportData && 10,            // 数字 = 排序并显示；false = 隐藏
    confirm: { title: t('customer.exportConfirm') },
    onClick: ({ row, loading }) => { /* 回调参数是对象 */ }
}] }"
```

- 默认按钮（编辑 -30 / 删除 -20 / 详情 -10 / 变更历史 -5）与自定义按钮**追加合并**；`show` 传数字决定位置与显隐。
- **坑**：`text` / `show` 的函数签名是 `(row, button)` 位置参数，只有 `onClick` 收对象 `{row, loading}`。

**弹层表单与提交**（`usePlusPageForm` + `handle-dialog.ts`）：

- 默认新增/编辑：`api.create` / `api.partialUpdate`；编辑态先 `detail(pk, {mask: "false"})` 回取脱敏原文。
- 定制：`addOrEditOptions.props.columns/row/formProps`（列解析器 ctx 含 `column / isAdd / formValue`）+ `beforeSubmit`（提交前加工）+ `apiReq`（换接口）。
- 服务端校验失败自动内联到表单项（`src/components/RePlusPage/src/utils/serverErrors.ts`）。

**复用列装配**（非 RePlusPage 页面）：`useBaseColumns(localeName)` 产出六组列/规则，范例 `src/views/settings/components/settings/SettingItem.vue`。

- 依赖：`@pureadmin/table`（PureTable/PureTableBar，必需）、`plus-pro-components`（PlusSearch/PlusForm，必需）、`src/utils/http`、图标方案。
- 扩展点：渲染器注册（§4.2）、`openDialogDrawer`（复用弹层表单能力）、`handleOperation`（请求 + 提示 + 回调的标准封装）。
- 移植说明（去 xadmin 化）：`xadmin-client/docs/metadata-driven-crud.md`。

### 2.2 弹层：ReDialog / ReDrawer

```ts
// 命令式：任意位置调用，不需要在模板挂组件
addDialog({
  title: t("crm.customer.importTitle"),
  width: dialogSize("md"),            // 四档尺寸 sm=480 / md=640 / lg=760 / xl=860
  draggable: true, destroyOnClose: true, closeOnClickModal: false,
  sureBtnLoading: true,               // 异步确认按钮 loading
  contentRenderer: () => h(ImportForm, { onSaved: refresh }),
  beforeSure: async (done, { closeLoading }) => { /* 校验/提交，失败不调 done */ }
});
addDrawer({ title, size: "55%", props: { pk: row.pk }, contentRenderer: () => h(DetailPanel) });
```

| 项 | 说明 |
|---|---|
| 导出 API | `addDialog` / `closeDialog` / `updateDialog` / `closeAllDialog` / `getDialogUid`（ReDrawer 对称） |
| 关键配置 | `title/width/modal/fullscreen/draggable/destroyOnClose/closeOnClickModal`、`props`（内容组件 props）、`hideFooter`、`footerButtons`、`beforeSure` / `beforeCancel`、`open/close` 回调 |
| 内容组件约定 | 通过 `props` 收参；关闭用框架 close 事件（勿声明 `onClose` prop）；提交成功后**先 `done()` 关弹窗再 `await` 刷新列表**（先刷新会滞留） |
| 何时用哪个 | 表单/确认弹窗 → ReDialog；复杂详情/长表单/侧栏编辑 → ReDrawer；一句确认 → `ElMessageBox`（危险操作用 `el-button--danger` + `.catch` 兜底） |
| 权威源 | `src/components/ReDialog/{index.ts,type.ts,size.ts}`、`src/components/ReDrawer/` |

- 项目内已收敛模式（新弹窗照抄）：`addDialog({...}) + components/XxxForm.vue（reactive 表单 + getPayload() 校验）`，范例 `views/integration/knowledge/`、`views/integration/api-app/`。

### 2.3 图标：ReIcon / LocalIcon（全离线）

```ts
import { useRenderIcon } from "@/components/ReIcon/src/hooks";
useRenderIcon(Delete)          // 编译期图标（unplugin-icons：~icons/ep/delete）
useRenderIcon("ep:user")       // 后端元数据下发的图标名 → LocalIcon 懒加载本地图标集
```

| 项 | 说明 |
|---|---|
| 渲染路径 | 字符串名统一走 `LocalIcon`：已注册直接渲染 → 内置集（`ep` / `ri` / `fa-solid`）懒加载本地 chunk → 未知只 DEV 告警，**绝不发在线请求** |
| 扩展方式 | 加内置集：`iconRegistry.ts::SET_LOADERS` 增条目；加随包图标：`offlineIcon.ts` 注册（`ep/xxx` 斜杠名给代码、`ep:xxx` 冒号名给服务端元数据，**双形态都注册**） |
| 权威源 | `src/components/ReIcon/src/{iconRegistry.ts,localIcon.ts,hooks.ts,offlineIcon.ts}` |

### 2.4 通用组件清单

| 组件 | 用途 | 关键点 |
|---|---|---|
| `RePlusSearch` | 下拉式表格选择器（el-select 内嵌 RePlusPage） | props：`api`（必需）/ `multiple` / `isTree` / `valueProps`（取值 / 展示字段）/ `listColumnsFormat` 等列格式化出口 |
| `RePureTableBar` | 表格工具栏（列显隐/密度/全屏/刷新） | RePlusPage 已内置，独立页面可单用 |
| `ReAuth`（全局注册为 `Auth`） | 按钮级权限 `<Auth value="create:CrmCustomer">` | 无 `v-auth` 指令 |
| `ReSegmented` | 分段控制器 | `renderBooleanSegmentedOption` 用于 boolean 表单项 |
| `ReCol` / `ReText` | 栅格 / 省略 Tooltip | — |
| `ReCropper` / `RePictureUpload` | 图片裁剪 / 头像裁剪上传 | — |
| `ReMfaConfirm` | 412 敏感操作二次验证弹窗 | `confirmMfa()`，http 层自动接线 |
| `ReSplitPane` | 可拖拽分栏（宽度持久化） | 配合 `src/hooks/useSplitPaneConfig.ts` |
| `ReCountTo` / `ReFlicker` / `ReQrcode` / `ReImageVerify` / `ReSendVerifyCode` / `ReTypeit` / `ReTreeLine` / `ReAnimateSelector` | 数字滚动 / 闪烁点 / 二维码 / 图形验证码 / 验证码倒计时 / 打字机 / 树形连接线 / 动画选择器 | 按需引用 |

### 2.5 请求层：BaseApi / http

```ts
// 1) 零定制：一行
export const bookApi = new BaseApi("/api/demo/book");
// 2) 带自定义动作
class KnowledgeApi extends BaseApi {
  syncRepo = () => this.request<DetailResult>("post", {}, {}, `${this.baseApi}/sync-repo`);
}
```

| 组件 | 职责 |
|---|---|
| `BaseApi` | 方法面 = 后端内建 Action 全集（list/create/retrieve/update/partialUpdate/destroy/batchDestroy/choices/columns/fields/import*/export*/recycle*） |
| `ViewBaseApi` | 单对象视图（无 list） |
| `http`（`src/utils/http/`） | 单例：Token 无感刷新（单飞 + 请求排队）、路由级取消（`skipRouteCancel` 豁免）、错误策略表（412 审批/MFA）、blob 下载、FormData 协议 v1 |
| `listRows(res)` | `data.results` 唯一拆包点 |
| `fetchAllRows(api)` | 自动翻页拉全量（下拉/树形列表用，防 100 条截断） |

- 契约：响应壳 `code=1000`；HTTP 400 的响应体会被 reject（供表单内联 `errors`）。
- 权威源：`src/api/base.ts`、`src/utils/http/`。

### 2.6 路由与权限

| 项 | 说明 |
|---|---|
| 静态路由 | `src/router/modules/*.ts` 自动收集（`remaining.ts` 不进菜单） |
| 动态路由 | 登录后 `GET /api/system/routes` → `handleAsyncRoutes`；`meta.frameSrc` → iframe 容器；component 字符串按 `/src/views/**` 匹配（未匹配 DEV 报错） |
| meta 约定 | `title/icon/showLink/auths/hiddenTag/dynamicLevel/fixedTag/frameSrc/rank/showParent/extraIcon/activePath/watermark`（路由侧另有 `keepAlive`） |
| 权限判定 | `hasAuth("动作:组件名")`；`getDefaultAuths(instance, ["customAction"])` 一次生成 RePlusPage 的 `auth` 对象；模板 `<Auth value="...">` |
| 约定 | 组件 `name` 与权限码后缀一字不差；无 `v-auth` |
| 权威源 | `src/router/utils/auth.ts`、`src/router/utils/async-routes.ts`、`src/layout/types.ts` |

### 2.7 状态管理（Pinia）

| store | 职责 |
|---|---|
| `pure-user` | 用户信息 / Token / 登出 / WS 消息处理（含桌面通知分派） |
| `pure-permission` | 动态菜单、按钮权限（`permissionAuths`）、keepAlive 缓存清单 |
| `pure-multiTags` | 多标签页（打开/关闭/缓存写盘） |
| `pure-app` / `pure-setting` / `pure-epTheme` | 布局 / 框架设置 / 主题色 |
| `pure-site-config` | 站点设置（实时保存、即时生效） |

- 约定：业务页面状态优先**组件本地 state**；需要跨页共享才进 store（新增 store 走 `modules/` + `useXxxStoreHook`）。
- 权威源：`src/store/modules/`（七个模块）。

### 2.8 工具库（`src/utils/`）

| 工具 | 用途 |
|---|---|
| `src/utils/dict.ts` | `useDict(code)` / `getDictItems(code)` 字典消费；`dictTagProps` / `statusTagProps` 状态标签统一入口 |
| `src/utils/aes.ts` | 请求体加密（v2 WebCrypto，自动回退旧格式） |
| `src/utils/sse.ts` | SSE 流式消费（fetch + ReadableStream，`parseSseBuffer` 纯函数） |
| `src/utils/websocket.ts` | WS 封装（心跳/重连） |
| `src/utils/download.ts` / `src/utils/message.ts` | 下载 / 消息提示（含读屏播报） |
| `src/utils/watermark.ts` / `src/utils/tree.ts` / `src/utils/form.ts` | 水印 / 树工具 / 表单序列化（FormData v1） |

- 权威源：`src/utils/dict.ts`、`src/utils/http/`、`src/utils/`。

### 2.9 指令与全局能力

| 指令 | 用途 |
|---|---|
| `v-copy` | 点击/指定事件复制 |
| `v-longpress` | 长按触发 |
| `v-ripple` | 水波纹 |
| `v-loading` | Element Plus 加载（`src/plugins/elementPlus.ts` 注册） |

新增全局组件登记点：`src/plugins/elementPlus.ts`（新增 `el-*` 用法**必须登记**，有单测比对清单）。

- 权威源：`src/directives/`、`src/plugins/elementPlus.ts`。

### 2.10 国际化（locale）

- 唯一文件：`locales/zh-CN.yaml`（源语言，eager）+ `locales/en.yaml`（懒加载），**必须成对补 key**（守护测试 `src/tests/locale-keys.spec.ts` 双向比对）。
- 列 label 回退链：`{localeName}.{key}` → `commonLabels.*` → 后端 gettext label（后端字段名在前端补 key 即可覆盖）。
- 菜单标题用 `menus.xxx` key；yaml 重复 key 会白屏。
- 权威源：`locales/zh-CN.yaml`、`locales/en.yaml`、`src/tests/locale-keys.spec.ts`。

## 三、工程化设施

| 设施 | 说明 |
|---|---|
| 契约与类型 | 服务端 `docs/schema/`（真源）→ 前端 `contract/schema`（`pnpm sync:contract` 一键镜像 + `gen:metadata-types` 生成类型）；CI `check:contract` 防绕过 |
| 版本一致性 | `pnpm check:version`（tag ↔ `server/const.py` ↔ client `package.json`）；`doctor` 同源自检 |
| 服务端门禁 | pytest（2800+，sqlite+FakeRedis 零外部依赖）/ ruff / 跨 app import / 文件行数 500 / 缓存键 / makemigrations / 文档事实（`check_doc_facts.py`） |
| 前端门禁 | `typecheck`（strict 全仓单轨零错误，2026-09-26 起双轨合一）/ eslint（`no-explicit-any` error）/ prettier / stylelint / vitest / 文件行数 500 / bundle-size（+15KB 预算）/ 契约 |
| E2E | Playwright 双浏览器（chromium+webkit）+ 专项（smoke / visual / perf / a11y / csp）；纪律见 `xadmin-client/e2e/README.md`（**改后端必须 `test:e2e:fresh`**） |
| 覆盖率 | 服务端 CI 门禁 `--cov-fail-under=85`（`.github/workflows/test.yml`）；前端 vitest 覆盖率阈值（含 registry / renders 等关键文件） |

## 四、依赖关系与扩展点速查

### 4.1 "要做什么 → 用哪个组件"

| 我要…… | 用 |
|---|---|
| 出一个标准列表页 | `BaseModelSet` + `BaseModelSerializer` + `BaseFilterSet` + `<RePlusPage>` |
| 加一个按钮触发接口 | 后端 `@action` + 权限点；前端 `BaseApi` 子类方法 + `operationButtonsProps.buttons` |
| 换单元格/表单控件 | `*Format` 出口（页面级）或渲染器注册（全局级） |
| 弹一个表单/详情 | `addDialog` / `addDrawer` / `openDialogDrawer` |
| 选一个用户/部门/角色 | `api-search-user` 等（表单）或 `RePlusSearch`（独立选择器）或联想（大表搜索） |
| 大表关联字段搜索 | `PkMultipleFilter` + `SuggestionsAction` + 前端自动 `SuggestSelect` |
| 定时跑一件事 | `@register_as_period_task(interval=..., module=...)` |
| 发一条多通道通知 | 消息类实例 `.publish(is_async=True)`（新渠道加 `backends/<name>.py`） |
| 让业务走审批 | `approval_flow.engine.create_instance(biz_type=..., biz_id=...)` + 终态信号 |
| 对外投递事件 | `system/utils/webhook.py::emit_webhook_event`（事件先登记 `EVENT_CATALOG`） |
| 枚举文案可运营 | `DictChoiceField` + 字典页维护 |
| 让功能可裁剪 | `{app}/modules.py`（`generate_module`）+ `config.yml` 的 `MODULE_*` |
| 升级后收尾 | `manage.py post_upgrade` / `doctor` |

### 4.2 扩展点总表

| 扩展点 | 位置 | 时机/约束 | 参考实现 |
|---|---|---|---|
| 新 `input_type` 渲染 | 后端 `drf/metadata.py::get_field_type`（isinstance 分支）+ 前端 `RePlusPage/src/utils/registry.ts` 三注册函数 | 前端注册必须早于页面首渲染 | `renderers-*.tsx`；配对守护 `renderers-pairing.spec.ts` |
| `api-search-*` 组件 | `registerApiSearchComponents` | 同上 | `src/views/system/apiSearch.ts` |
| 联想 fetcher | `registerSuggestFetcher` | 同上 | 同上 |
| 列/表单覆盖 | `*Format` props（页面级） | 页面内 | `src/views/system/user/utils/useUserColumnFormats.tsx` |
| 弹窗表单复用 | `openDialogDrawer` | 页面级 | `useUserColumnFormats.tsx::handleRoleRules` |
| 内置图标集扩展 | `iconRegistry.ts::SET_LOADERS` / `offlineIcon.ts` | 离线约束：不得回退在线 | 双形态注册 |
| 全局 `el-*` 组件 | `src/plugins/elementPlus.ts` | 有单测比对清单 | — |
| 值级加密 | `common/base/utils.py::signer` | 敏感字段入库前 | webhook secret / AI api_key |
| 新增缓存类 | `common/cache/storage.py::RedisCacheBase` | 键名过 `check_cache_keys.py` | — |
| 周期任务 | `@register_as_period_task(module=...)` | module 归属可裁剪 | `system/tasks/` |
| 通知渠道 | `notifications/backends/<name>.py`（模块级 `backend`） | 渠道枚举补 `BACKEND` | `notifications/backends/email.py` |
| 通知消息类型 | `@register_message` + `register_backend_msg` | 渲染映射补齐各渠道 | `notifications/notifications.py` |
| Webhook 事件 | `EVENT_CATALOG` 登记 + `emit_webhook_event` | 事件契约守护测试 | `system/utils/webhook.py` |
| 审批业务绑定 | `create_instance(biz_type, biz_id)` + 监听 `approval_instance_finished` | 终态信号在 `system/signal.py` | 请假业务 `approval/utils/leave.py` |
| 可裁剪模块 | `{app}/modules.py`（`ModuleSpec`） | `generate_module` 生成 | `common/core/modules/registry.py` |
| 配置键 | 部署期 `config_example.yml`+`defaults.py`；运行期 `system_conf.py`+种子 | 两处同名；种子守护测试 | — |
| 中间件 | `MIDDLEWARE` 插入 | 开关用 `MiddlewareNotUsed` | `server/middleware.py` |
| 自定义渲染器（SSE 等） | `common/drf/renders/` + ViewSet `get_renderers()` | **ViewSet 必须覆写 `get_renderers`**（装饰器只对 `@api_view` 生效） | `message/views.py::ChatAiViewSet` |
| 数据源非 ORM 的 ViewSet | 自带 `batch_destroy` | 不能依赖 QuerySet 能力 | `SecurityBlockIpViewSet` |

### 4.3 前后端咬合点（改一侧必看另一侧）

| 咬合点 | 约定 | 破坏后果 | 防护 |
|---|---|---|---|
| 元数据协议 | `search-columns` / `search-fields` JSON Schema | 表格空列 / 表单缺域（**静默**） | 契约镜像 + 守护测试 + DEV 警示条 |
| 权限码 | `{action}:{ViewSetName}` ↔ 组件 `name` | 页面/按钮不渲染（**静默**） | 种子守护测试 + `doctor` |
| 统一响应 | `code=1000` 成功壳 | 页面无提示/误提示 | 契约 + 错误策略表 |
| FormData v1 | 点分键序列化（`AxiosMultiPartParser` 反向还原） | 文件/嵌套字段提交失败 | 协议守护测试 |
| 路由生成 | 菜单 `component` 字符串 ↔ `src/views/**` 路径 | 空白路由 | DEV 报错 |
| 契约变更流程 | 先改 `docs/schema` → `pnpm sync:contract` → 提交生成类型 | CI 红 | `check:contract` |

## 五、相关文档

| 文档 | 内容 |
|---|---|
| [framework-cookbook.md](framework-cookbook.md) | ViewSet 选型、Action↔BaseApi 对照、覆写红线 |
| [metadata-protocol.md](metadata-protocol.md) | 元数据协议规范 |
| [方案选型与对比.md](方案选型与对比.md) | 组件/方案的特点、适用场景与对比 |
| [../guide/recipes.md](../guide/recipes.md) | 典型扩展流程处方集（按任务索引） |
| [模块化与功能裁剪.md](模块化与功能裁剪.md) | 模块清单、裁剪矩阵、CLI |
| [../../common/README.md](../../common/README.md) | 内核目录地图与边界规则 |
