# common —— 框架内核（kernel）

> 定位：**不依赖任何业务 app 的可复用层**。工程装配（`server/`）与业务 app（`system` /
> `settings` / `message` / …）依赖这里；**反向依赖（本包 import 业务 app）被
> `scripts/check_cross_app_imports.py` 静态门禁禁止**（CI lint 强制）。
>
> 用法速查（ViewSet 选型 / 覆写点 / 前端契约）见
> [framework-cookbook.md](../docs/architecture/framework-cookbook.md)；
> 本文回答"内核里有什么、边界在哪、扩展走哪条路"。

## 一、目录地图

| 目录 / 模块 | 职责 | 二开关键入口 |
|---|---|---|
| `core/models.py` | 模型基类：`DbUuidModel`（UUID 主键）/ `DbBaseModel` / `DbAuditModel`（审计字段）/ `DbCharModel` / `SoftDeleteModel`（软删三通道）/ `AutoCleanFileMixin` | 业务模型继承 `DbAuditModel`（或 `SoftDeleteModel`） |
| `core/modelset/` | CRUD ViewSet 的 Action Mixin（base/crud/batch/metadata/import_export/recycle/upload/cache/suggest/viewsets 十模块） | `BaseModelSet` 等组合基类（`viewsets.py`）；覆写红线见 cookbook §四 |
| `core/serializers.py` | `BaseModelSerializer`：字段权限裁剪 / 动态字段 / 关联形态（attrs+format）/ `table_fields` | 业务序列化器唯一基类 |
| `core/filter.py` + `core/data_scope/` | 数据权限：16 种规则编译 → 查询集过滤（`BaseDataPermissionFilter`） | 查询集过滤统一走 `get_filter_queryset`，勿手写裸 filter |
| `core/permission.py` | API/菜单权限（method+path 正则 ↔ 权限点）、白名单、字段权限挂载 | 权限码约定 `{action}:{ViewSetName}` |
| `core/fields.py` | 内核字段：`BasePrimaryKeyRelatedField` / `LabeledChoiceField` / `LabeledMultipleChoiceField` / `input_wrapper` | 字段形态（下拉 / 表格选择器 / Tab 列） |
| `core/response.py` | `ApiResponse`：统一响应壳（`code=1000` 成功），协议冻结于 `docs/schema/` | 业务视图一律返回 `ApiResponse` |
| `core/pagination.py` / `core/throttle.py` / `core/auth.py` / `core/exception.py` / `core/middleware.py` / `core/routers.py` | 分页（`DynamicPageNumber`）/ 限流 / 认证（JWT+PAT）/ 异常归一 / 请求中间件 / 路由器 | — |
| `core/config/` | 配置体系：`SysConfig`（config.yml → DB 热更新）/ `UserConfig`（个人配置）/ `system_conf` 属性注册表 | 新配置键在 `system_conf.py` 注册 property |
| `core/modules/` | 功能模块注册表与六层裁剪（路由 404 / WS 通道 / 菜单 / 周期任务 / 种子 / 缓存） | 内置 `MODULES`（registry.py）+ `{app}/modules.py` 扩展点（scaffold.py 渲染模板） |
| `core/db/` | 连接健康检查（半开连接快速失败参数） | — |
| `core/approval.py` / `core/import_mapping.py` / `core/task_request.py` / `core/validators.py` / `core/utils.py` | 审批协议挂载点 / 导入映射契约 / 异步任务请求上下文 / 校验器 / 通用工具 | — |
| `drf/` | DRF 层扩展：`metadata.py`（`get_field_type` 的 `input_type` 判定，**必须 isinstance**）/ parsers / renders（含 SSE）/ const | 元数据判定新增类型走 isinstance 分支 |
| `fields/` | 模型字段：`char.py`（加密 Char）/ `image.py` / `utils.py` | — |
| `base/` | `magic.py`（magic 缓存响应 / 分布式锁 / 限次调用 / SQL 计数 / 临时禁用信号）；`utils.py`（**字段级加密 signer**：HKDF+AES-GCM 的 `v3:` 前缀格式、ModelChoice 工具） | 模型密钥类字段 / webhook secret / AI api_key 用 signer |
| `cache/` | `storage.py`（`RedisCacheBase` 与各业务缓存类）/ `lock.py` / `state.py` / `channel.py` / `redis.py` | 新缓存类继承 `RedisCacheBase`，键名过 `scripts/check_cache_keys.py` 门禁 |
| `celery/` | 任务基建：`decorator.py`（`register_as_period_task` 周期任务注册）/ 失败处理 / 心跳 / 日志 / Flower / 指标 | 周期任务：`@register_as_period_task(..., module="模块id")` |
| `sdk/` | 对外服务 SDK：`ai/`（chat / chat_stream，OpenAI 兼容）/ `im/`（钉钉·企微·飞书发送）/ `sms/` | 业务调用 AI/IM/短信的唯一入口 |
| `utils/` | 通用工具：logger / health（探活）/ token / verify_code / sanitize（HTML 清洗）/ country / timezone / file / request / connection / ip / pending / random | `get_logger(__name__)` 统一日志 |
| `api/` | 内核自带只读端点：health / countries / 资源缓存 / CSP 上报 / metrics / 备份与运维告警 | 基础设施端点（AllowAny + 非事务豁免） |
| `decorators/` / `swagger/` / `templates/` / `management/` | 装饰器 / OpenAPI 扩展 / 内核模板 / 管理命令（`generate_crud` 生成器、`manage.py start/stop/status` 进程管理） | — |
| 顶层模块 | `models.py` / `serializers.py` / `signals.py` / `signal_handlers.py` / `startup.py` / `tasks.py` / `notifications.py`（通知后端注册表）/ `metrics.py` / `db.py` / `local.py` / `backup_alert.py` / `ops_alert.py` / `apps.py` | `apps.py::ready` 完成信号与周期任务装配 |

## 二、边界规则（什么放内核、什么放业务 app）

1. **内核 = 被两个以上 app 复用、且不含业务语义**：通用模型基类、ViewSet Mixin、字段、
   缓存基建、任务基建、SDK、纯工具。判据：把代码挪到使用方 app 里，它是否还能工作且不反向 import？
2. **业务 app 持有业务语义**：模型/接口/权限点/菜单/种子/迁移，以及围绕它们的 `services` 契约层。
   跨 app 调用一律走 `<app>.services`（`check_cross_app_imports.py` 扫描坏味道）。
3. **工程层只装配**：`server/` 负责 settings 拼装、总路由、celery 装配、中间件链；
   内核不 import `server`（`server.const.CONFIG` 的读取通过参数注入或在 settings 层完成后下发）。
4. **依赖方向**：业务 → 工程 → 内核；禁止内核 import 业务、禁止业务 import 业务（services 除外）。

## 三、内核 API 备忘（五件事的最短路径）

| 主题 | 入口 | 机制文档 |
|---|---|---|
| 权限（API/数据/字段 + 应用级授权） | `core/permission.py`、`core/filter.py`、`core/serializers.py::get_allow_fields`、`system/utils/api_grant.py` | [permission.md](../docs/architecture/permission.md) |
| 元数据（search-columns / search-fields / choices） | `core/modelset/metadata.py` + `drf/metadata.py`；协议 Schema 见 [docs/schema/](../docs/schema/README.md)，规范见 [元数据协议规范](../docs/architecture/metadata-protocol.md) | 契约测试 `tests/unit/common/test_metadata_schema.py` |
| 响应与异常 | `core/response.py::ApiResponse`（`code=1000` 成功） | [exception-handling.md](../docs/exception-handling.md) |
| 缓存 | `cache/storage.py::RedisCacheBase`（键登记过 `check_cache_keys.py`）；失效走信号，别手工散落 delete | [cache.md](../docs/architecture/cache.md) |
| 模块裁剪 | `core/modules/`（`ModuleSpec` 声明 → preset/enable/disable 解析 → 六层裁剪） | [模块化与功能裁剪.md](../docs/architecture/模块化与功能裁剪.md) |

## 四、扩展纪律（改内核前必读）

1. **新增 `input_type`**：服务端 `drf/metadata.py::get_field_type` 用 isinstance 判定 + 客户端
   四通道渲染器登记，完整清单见 cookbook「新增 input_type 检查清单」；
2. **改响应壳 / WS 帧 / 路由载荷**：先改 `docs/schema/`（破坏性契约变更需评审）再补守护测试；
3. **新增缓存键**：`scripts/check_cache_keys.py --strict` 门禁（单文件前缀，冲突即失败）；
4. **覆写点必须附守护测试**（cookbook §四覆写红线第 4 条）；
5. **行数门禁**：单文件 ≤ 500 行（`scripts/check_file_length.py`），超限先拆分；
6. 内核变更默认影响全部业务 app——提交前跑全量 pytest（2600+）而不是只跑改动面。
