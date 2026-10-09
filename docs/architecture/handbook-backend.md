# 组件手册 · 后端组件（xadmin-server）

> 本文为《组件手册》后端分册（§一）。全景图、工程化设施与扩展点速查见
> [component-handbook.md](component-handbook.md)；前端组件见 [handbook-frontend.md](handbook-frontend.md)。

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
| `signer`（值级加密） | HKDF+AES-GCM（`v3:` 前缀），用于 JSON 值内的敏感键 | `packages/xadmin-common/common/base/utils.py::signer.encrypt/decrypt`（webhook secret / AI api_key 同款） |
| `upload_directory_path` | 统一上传路径 `{app}/{model}/{creator}/{pk}/{uuid5}.{ext}` | `upload_to=upload_directory_path` |

- 依赖：仅依赖 `django.db`；`DbAuditModel.dept_belong` 关联 `system.DeptInfo`（数据权限的归属字段）。
- 配置项：无需配置；`Meta.ordering` 必须给默认排序（列表分页依赖，缺失有告警）。
- 扩展点：新增通用字段时在 `packages/xadmin-common/common/core/models.py` 加抽象基类（内核），**不要**改 `DbAuditModel` 既有字段语义。
- 权威源：`packages/xadmin-common/common/core/models.py`、`packages/xadmin-common/common/fields/char.py`、`packages/xadmin-common/common/base/utils.py`。

### 1.2 序列化器与字段形态（`BaseModelSerializer`）

| 能力 | 说明 |
|---|---|
| 字段权限裁剪 | 按请求用户的字段白名单直接移除 `self.fields`（元数据同步消失），无需业务代码处理 |
| `Meta.fields` / `table_fields` | `fields` = 接口字段；`table_fields` = 列表默认列与顺序（`index+1` 即元数据 `table_show`） |
| `id` → `pk` | 自动转换，无需声明 |
| `Meta.tabs` | `TabsColumn("分组名", ["字段", ...])` 表单分栏 |
| action 级序列化器 | ViewSet 类属性 `{action}_serializer_class`（如 `list_serializer_class`） |

常用字段形态（均在 `packages/xadmin-common/common/core/fields.py`；`system/serializers/fields.py` 仅为兼容别名）：

| 字段 | 元数据 `input_type` | 用途 |
|---|---|---|
| `LabeledChoiceField` | `labeled_choice`（值 `{value,label,color?}`） | 枚举下拉；`DictChoiceField` 是其字典驱动子类 |
| `LabeledMultipleChoiceField` | `labeled_multiple_choice` | 多选枚举 |
| `DictChoiceField(dict_code=..., fallback_choices=..., merge_fallback=...)` | 同上（走字典） | 枚举文案可运营化（字典页在线维护，不发版） |
| `BasePrimaryKeyRelatedField`（`attrs` + `format`） | `object_related_field` | 外键下拉；`attrs: ["pk"]` 输出 `{pk,label,format 渲染结果}` |
| `ManyRelatedField` | `m2m_related_field` | 多对多 |
| `input_wrapper(serializers.SerializerMethodField)(read_only=True, input_type="boolean")` | 自定义 | 只读展示字段 / 自定义渲染类型的注入口 |
| `PkMultipleFilter(input_type="api-search-user")` | 见 §1.4 | 搜索区的远程选择/多选 |

- 依赖：`packages/xadmin-common/common/core/fields.py`、`packages/xadmin-common/common/drf/metadata.py`（`input_type` 判定，**必须 isinstance**）。
- 配置项：`extra_kwargs`（`input_type` / `attrs` / `format` / `required`）；`Meta.fields_unexport`（导入导出忽略列）。
- 扩展点：自定义 JSON 校验走 `validate()`；新增字段类型 → 先在 `packages/xadmin-common/common/drf/metadata.py::get_field_type` 加 isinstance 分支，再走前端四通道（§4.2）。
- 权威源：`packages/xadmin-common/common/core/serializers.py`、`packages/xadmin-common/common/core/fields.py`；协议见 [metadata-protocol.md](metadata-protocol.md)。

### 1.3 ViewSet 体系（`packages/xadmin-common/common/core/modelset/`）

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

- 依赖：`packages/xadmin-common/common/core/modelset/` 各模块 + `packages/xadmin-common/common/core/response.py`。
- 配置项（类属性）：`queryset` / `serializer_class` / `filterset_class` / `pagination_class` / `ordering_fields` / `select_related_fields` / `prefetch_related_fields` / `{action}_serializer_class`。
- 扩展点：覆写点表与红线见 [framework-cookbook.md](framework-cookbook.md) §四；自定义动作 `@action` + `@extend_schema` + `ApiResponse`。
- 权威源：`packages/xadmin-common/common/core/modelset/viewsets.py`、`packages/xadmin-common/common/core/modelset/base.py`。

### 1.4 过滤与搜索

| 组件 | 职责 | 使用方式 |
|---|---|---|
| `BaseFilterSet` | 预置 `pk` / `created_time` / `updated_time` / `creator` / `dept_belong` 等常用过滤器 | `class BookFilter(BaseFilterSet): ...` |
| `PkMultipleFilter(input_type=...)` | 关联字段搜索（默认多选 pk；`input_type="input"` 变输入框、`"api-search-user"` 变远程选择） | FilterSet 类属性 |
| `BaseDataPermissionFilter` | **数据权限全局挂载点**（默认 filter_backends 已含，勿绕过） | 不改即可；自定义查询走 `get_filter_queryset` |
| 联想（`SuggestionsAction`） | 大表关联字段的远程候选（候选集与写入校验同源） | ViewSet 声明 `suggestion_fields = ("delegate",)`，前端自动升级 `SuggestSelect` |

- 约束：FilterSet 声明的过滤器**必须同时列入 `Meta.fields`**，否则 `search-fields` 不产出。
- 权威源：`packages/xadmin-common/common/core/filter.py`、`packages/xadmin-common/common/core/data_scope/`；设计见 [permission.md](permission.md)。

### 1.5 元数据通道

两个只读端点（由 §1.3 的 Mixin 提供）：

| 端点 | 来源 | 消费方 |
|---|---|---|
| `GET {base}/search-columns` | 序列化器 + `Meta.table_fields` + `Meta.tabs` | 前端列/表单/详情渲染 |
| `GET {base}/search-fields` | `filterset_class` | 前端搜索区 |
| `GET {base}/choices` | `choices_models` 聚合 | 下拉数据源 |

`input_type` 推断链：字段自带 `input_type`（最高）→ `input_type_prefix/suffix` → `packages/xadmin-common/common/drf/metadata.py::get_field_type` 类型判定 → DRF 默认。协议全文（字段语义 / 注册表 / 与字段权限关系 / 失败可见性）见 [metadata-protocol.md](metadata-protocol.md)；性能开关 `?with_meta=1` 把三请求合并为一。

### 1.6 权限组件

| 层 | 组件 | 位置 |
|---|---|---|
| API/菜单权限 | `IsAuthenticated`（白名单 → `get_user_permission` → 菜单 pk 解析） | `packages/xadmin-common/common/core/permission.py` |
| 数据权限 | `get_filter_queryset` + `core/data_scope/`（16 种规则，fail-closed） | `packages/xadmin-common/common/core/filter.py` |
| 字段权限 | `BaseModelSerializer` 自动裁剪 | `packages/xadmin-common/common/core/serializers.py` |
| 应用级授权 | `identity/utils/api_grant.py`（仅 PAT 凭证，只收敛不提权） | 三处挂载 |
| 权限点治理 | `get_view_permissions` / `scan_gaps` / `sync_menu_permissions` / `doctor` | `system/utils/platform/menu.py`、`system/services/permission_sync/` |
| 前端消费 | `hasAuth("动作:组件名")` / `<Auth>` / `usePageAuth` | 见 §2.6 |

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

- 常用上下文：`server/utils.py`（`get_current_request` / `set_current_request`，当前请求与当前用户）；异步任务构造请求用 `packages/xadmin-common/common/core/task_request.py::build_task_request`。
- 扩展点：新增中间件写类后插入 `MIDDLEWARE`（注意响应阶段自内向外）；需要开关时 `raise MiddlewareNotUsed`。范例 `server/middleware.py`。

### 1.9 Celery 任务

| 组件 | 用途 |
|---|---|
| `@shared_task` | 普通异步任务（`{app}/tasks.py`，autodiscover 自动发现） |
| `@register_as_period_task(crontab=… 或 interval=…, name=…, module=…)` | 周期任务声明（`module=` 归属可裁剪模块：模块停用即不注册） |
| `@after_app_ready_start` / `@after_app_shutdown_clean_periodic` | 启动即注册 / 关停清理 |
| `background_task_view_set_job` + `run_view_by_celery_task` | 把 ViewSet action 丢进 heavy 队列跑（批量导入导出用，**无活跃 worker 自动降级同步**） |
| `CELERY_TASK_ROUTES` | 队列路由（default / heavy 双队列，重活加条目） |

- 权威源：`packages/xadmin-common/common/celery/decorator.py`、`packages/xadmin-common/common/tasks.py`；范例 `system/tasks/`（报表分发、清理任务）。

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
数据库态 SysConfig（管理页可改，热更新）             ← packages/xadmin-common/common/core/config/
个人级 UserConfig（真实个人行优先，缺席继承系统级）
```

| 我要加…… | 放在 |
|---|---|
| 一个部署期配置（重启生效） | `config_example.yml` + `server/conf/defaults.py`（两处同名键） |
| 一个运行期配置（管理页可改） | `packages/xadmin-common/common/core/config/system_conf.py` 注册 property + `loadjson/systemconfig.json` 种子 + `settings/`（系统设置 app）管理页表单 |
| 一个个人配置 | `packages/xadmin-common/common/core/config/user_conf.py` + 个人设置页 |

- 取值链细节与配置速查表（键 ↔ 环境变量 ↔ 默认值 ↔ 生效方式）见 [../ops/config-reference.md](../ops/config-reference.md) §9。
- 权威源：`server/conf/`、`packages/xadmin-common/common/core/config/`。

### 1.12 种子与初始化

| 组件 | 职责 |
|---|---|
| `loadjson/*.json` | 初始种子（菜单/权限点/字典/系统配置/流程模板…… 21 份） |
| `manage.py load_init_json` | 幂等装载：模块裁剪过滤 → 自然键冲突预检（跳过并告警）→ 导入 → 时间戳回填 → 缓存失效 |
| `manage.py dump_init_json` | 反向导出种子 |
| `loaddata loadjson/seed_<app>_<model>.json` | 生成器产出的模块种子（pk 由 uuid5 派生，可重复装载） |
| `ops/init_data.py` | 新库初始化：migrate → load_init_json → 建超管（幂等） |
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
| `integrations/sdk/ai/chat.py` | `chat(messages, **overrides)` / `chat_stream(...)`（OpenAI 兼容，凭据取激活档案） |
| `integrations/sdk/im/` | 钉钉 / 企微 / 飞书消息发送（token 缓存） |
| `integrations/sdk/sms/` | 短信发送 |

业务调用 AI/IM/短信一律走 SDK，不要自己拼 HTTP。

