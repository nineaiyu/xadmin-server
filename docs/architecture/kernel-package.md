# 框架内核独立分发包（xadmin-common）

> **定位**：`common`（框架内核，可复用层）以 uv 工作区成员形态发布为**独立分发包**
> `xadmin-common`；宿主引用后导入名仍是 `common`。本文回答"包在哪、怎么用、宿主必须提供
> 什么、边界在哪"。包内文档见 `packages/xadmin-common/common/README.md`（内核目录地图）。

## 一、发行形态与工作区布局

```
xadmin-server/                      # uv 工作区根 + 服务端本体（package = false）
├── pyproject.toml                  # [tool.uv.workspace] members = ["packages/*"]
│                                   # dependencies 含 xadmin-common（sources: workspace + editable）
├── packages/
│   └── xadmin-common/
│       ├── pyproject.toml          # 分发元数据（hatchling；wheel 只收 common 包本体）
│       ├── README.md               # 分发视角说明（安装 / 接线 / 边界）
│       └── common/                 # 框架内核源码（导入包名 = common）
└── （业务 app：system / identity / approval / …）
```

| 动作 | 命令 |
|---|---|
| 本地开发（工作区内消费） | `uv sync --all-groups`（成员以 **editable** 安装，改 `common/` 源码即时生效） |
| 构建分发包 | `uv build --package xadmin-common`（wheel + sdist → `dist/`） |
| 导出依赖产物 | `uv export … --no-emit-workspace …`（工作区成员为路径依赖，pip 产物剔除） |
| 宿主安装 | `uv add xadmin-common`（或 `pip install xadmin-common`，可选 `[storage]` extra） |

**依赖与版本**：分发名 `xadmin-common` 的 packaging 版本独立演进（首版 0.1.0），平台版本
仍是 `server/const.py` 的 `VERSION`；内核第三方依赖声明在成员 `pyproject.toml`（范围约束，
工作区内实际锁定版本由 `uv.lock` 决定）。三方一致性（pyproject ↔ 产物 ↔ uv.lock）与工作区
骨架由 `tests/unit/test_dependency_manifest.py` 守护。

## 二、边界与消费面

- 内核**禁止** import 业务 app 与 `server`（工程层）：`scripts/check_cross_app_imports.py`
  扫描 `packages/xadmin-common/common/` 强制；
- 内核消费宿主业务能力的唯一出口是 `common/contracts.py`（声明式契约面 + 注入制：宿主
  `AppConfig.ready()` 注册或 `xadmin.contracts` entry point 装配）；
- 宿主侧接线：`INSTALLED_APPS` 加 `"common"`；按 §三 提供 settings；需要菜单/系统配置等
  业务契约时注册提供方；
- 内核变更影响全部宿主：提交前跑全量 pytest（而非只跑改动面）；行数/缓存键/跨 app 门禁的
  扫描路径已同步到 `packages/xadmin-common/common/`。

## 三、内核 settings 契约

内核读取的每个 Django settings 键都在 `packages/xadmin-common/common/settings_contract.py`
登记（**单一事实源**）：本表由 `tests/unit/common/test_settings_contract.py` 与该模块锁步
（键 / 类型 / 缺省三列逐一对照，双向防漂移）。

- **缺省列 = 必给**：代码内为直读（`settings.KEY`），缺失即读取方报错；宿主必须提供。
- **缺省列为具体值**：代码内为 `getattr(settings, "KEY", <缺省>)`，缺失即按该值工作。
- **类型**列是文档/评审口径，不作为运行期校验。
- 新增读取必须同步登记契约面（守护测试会拦未登记读取与失效登记）。

| 键 | 类型 | 缺省 | 用途 | 消费（内核模块，相对 `common/`） |
|---|---|---|---|---|
| `SECRET_KEY` | str | 必给 | Django 密钥；内核用于字段级加密派生材料与签名（base/utils.py::signer、fields/char.py） | `base/utils.py`、`fields/char.py` |
| `FIELD_ENCRYPTION_KEY` | str | 必给 | 字段级加密主密钥（AESCipherV3 根材料；空 = 回落到 SECRET_KEY 派生并告警） | `base/utils.py` |
| `FIELD_ENCRYPTION_LEGACY_KEYS` | list[str] | 必给 | 字段级加密历史密钥（轮换期解密旧密文） | `base/utils.py` |
| `SECURITY_AES_V1_DECRYPT_ENABLED` | bool | True | 是否允许解密 AES v1（Salted__）旧格式请求体；关闭即 fail-closed | `base/utils.py` |
| `AUTH_USER_MODEL` | str | 必给 | 用户模型标签（内核模型基类/权限组件解析用户模型用） | `core/models.py` |
| `ALLOWED_HOSTS` | list[str] | None | 允许主机（CSP 上报的站内域名判定；Django 安全基建） | `api/csp.py`、`swagger/views.py` |
| `SECURITY_LOGIN_LIMIT_TIME` | int | 必给 | 登录锁定提示中的锁定时长（分钟，文案展示用） | `swagger/views.py` |
| `VERIFY_CODE_TTL` | int | 必给 | 验证码有效期（秒） | `utils/verify_code.py` |
| `VERIFY_CODE_LIMIT` | int | 必给 | 验证码单目标发送次数上限（限流窗口内） | `utils/verify_code.py` |
| `VERIFY_CODE_LENGTH` | int | 必给 | 验证码位数 | `utils/verify_code.py` |
| `VERIFY_CODE_UPPER_CASE` | bool | 必给 | 验证码字符集是否含大写字母 | `utils/verify_code.py` |
| `VERIFY_CODE_LOWER_CASE` | bool | 必给 | 验证码字符集是否含小写字母 | `utils/verify_code.py` |
| `VERIFY_CODE_DIGIT_CASE` | bool | 必给 | 验证码字符集是否含数字 | `utils/verify_code.py` |
| `PERMISSION_WHITE_URL` | list[str] | 必给 | 免鉴权 URL 白名单（method+path 正则；含登录/验证码/文档等公开端点） | `core/permission.py`、`core/utils.py` |
| `PERMISSION_SHOW_PREFIX` | list[str] | 必给 | 需要展示按钮级权限码的 URL 前缀正则（路由收集时附带 auths） | `core/utils.py` |
| `PERMISSION_DATA_ENABLED` | bool | 必给 | 数据权限总开关（关闭即所有查询不做行级裁剪） | `core/filter.py` |
| `PERMISSION_DATA_AUTH_APPS` | list[str] | 必给 | 纳入数据权限编译的 app 白名单（其余 app 不注册模型维度） | `core/utils.py` |
| `PERMISSION_FIELD_ENABLED` | bool | 必给 | 字段权限总开关（序列化时按角色×菜单裁剪字段） | `core/controlled_lookup.py`、`core/fields_related.py`、`core/permission.py`、`core/serializers.py` |
| `BASE_DIR` | str | 必给 | 工程根目录（进程管理命令定位项目/静态资源用） | `management/commands/services/hands.py` |
| `PROJECT_DIR` | str | 必给 | 工程根目录（启动自检读取版本/配置） | `startup.py` |
| `DATA_DIR` | str | 必给 | 运行期数据目录（IP 归属地库等运行期文件） | `management/commands/services/hands.py`、`utils/ip/geoip/utils.py`、`utils/ip/ipip/utils.py` |
| `MEDIA_ROOT` | str | 必给 | 上传文件根目录（本地存储后端与文件链路） | `storage/backend.py`、`storage/utils.py` |
| `MEDIA_URL` | str | 必给 | 媒体 URL 前缀（存储后端拼接访问地址） | `storage/backend.py` |
| `MEDIA_X_ACCEL_PREFIX` | str | '' | nginx X-Accel-Redirect 内网前缀（空 = 由 Django 直接回文件流） | `utils/media.py` |
| `FILE_UPLOAD_SIZE` | int | 必给 | 单文件上传大小上限（字节） | `core/modelset/upload.py` |
| `EXPORT_MAX_LIMIT` | int | 必给 | 同步导出最大行数（超过引导异步导出） | `drf/renders/base.py` |
| `CACHE_KEY_TEMPLATE` | dict[str, str] | 必给 | 缓存键模板表（token 黑名单 / 待办状态等跨组件共享键名） | `cache/channel.py`、`cache/storage.py` |
| `SIMPLE_JWT` | dict[str, Any] | 必给 | simplejwt 配置（内核按 ACCESS/REFRESH 生命周期管理黑名单缓存 TTL） | `cache/storage.py` |
| `REST_FRAMEWORK` | dict[str, Any] | 必给 | DRF 配置（内核读取认证/渲染等全局项） | `drf/renders/base.py`、`utils/request.py` |
| `CELERY_BROKER_URL` | str | '' | celery broker 地址（指标端点脱敏展示用） | `metrics.py` |
| `CELERY_TASK_ALWAYS_EAGER` | bool | False | 任务是否同步执行（异步导出/导入在 eager 环境直接落结果） | `core/modelset/import_export/export_actions.py`、`core/modelset/import_export/import_actions.py` |
| `CELERY_LOG_DIR` | str | 必给 | 任务日志目录（任务日志文件落盘根） | `celery/utils.py` |
| `CELERY_HEAVY_POOL` | str | 必给 | 重任务队列执行池类型（进程管理命令拉起 worker 用） | `management/commands/services/services/celery_heavy.py` |
| `CELERY_HEAVY_CONCURRENCY` | int | 必给 | 重任务队列并发度（进程管理命令拉起 worker 用） | `management/commands/services/services/celery_heavy.py` |
| `CELERY_FLOWER_HOST` | str | 必给 | Flower 监听地址（进程管理命令拉起 Flower 用） | `celery/flower.py` |
| `CELERY_FLOWER_PORT` | int | 必给 | Flower 监听端口 | `celery/flower.py` |
| `CELERY_FLOWER_AUTH` | str | 必给 | Flower Basic Auth（user:pass，空 = 不启用） | `celery/flower.py` |
| `EMAIL_HOST_USER` | str | 必给 | 发件账号（系统邮件通知发件人兜底） | `tasks.py` |
| `EMAIL_FROM` | str | 必给 | 发件显示名（系统邮件通知；空 = 回落 EMAIL_HOST_USER） | `tasks.py` |
| `EMAIL_SUBJECT_PREFIX` | str | 必给 | 系统邮件主题前缀 | `tasks.py` |
| `SECURITY_MONITOR_CPU_PERCENT_MAX` | float | 必给 | CPU 使用率告警阈值（%） | `notifications.py` |
| `SECURITY_MONITOR_MEMORY_USED_MAX` | float | 必给 | 内存使用率告警阈值（%） | `notifications.py` |
| `SECURITY_MONITOR_DISK_USED_MAX` | float | 必给 | 磁盘使用率告警阈值（%） | `notifications.py` |
| `SECURITY_MONITOR_CPU_LOAD_MAX` | float | 必给 | CPU 负载告警阈值（load1） | `notifications.py` |
| `METRICS_ENABLED` | bool | False | Prometheus 指标端点开关（关闭即 404 / no-op） | `api/metrics.py` |
| `METRICS_TOKEN` | str | '' | 指标端点令牌（空 = 端点拒访） | `api/metrics.py` |
| `HEALTH_CHECK_SKIP_CELERY` | bool | False | 健康检查是否跳过 celery 探针（测试/离线环境用） | `utils/health.py` |
| `MODULE_PRESET` | str | 'full' | 模块发行预设（core / standard / full；代码内默认常量 DEFAULT_PRESET = full） | `core/modules/registry.py` |
| `MODULE_ENABLE` | list[str] \| tuple[str, ...] | () | 显式启用模块清单（预设之外的加白） | `core/modules/registry.py` |
| `MODULE_DISABLE` | list[str] \| tuple[str, ...] | () | 显式停用模块清单（预设之内的裁剪） | `core/modules/registry.py` |
| `ROOT_URLCONF` | str | 必给 | 根 URLConf（路由收集/OpenAPI 生成用） | `core/utils.py` |
| `ROUTE_IGNORE_URL` | list[str] | 必给 | 路由收集忽略表（正则，不纳入权限点/路由树） | `core/utils.py` |
| `XADMIN_APPS` | list[str] | 必给 | 二开 app 注册表（路由/WS/周期任务自动发现） | `core/utils.py` |
| `DB_PREFIX` | str | 必给 | 物理表前缀（多环境共库时的表空间隔离） | `core/db/prefix.py` |
| `DEBUG` | bool | False | 调试模式（媒体文件直出、开发态分支） | `base/decorators.py`、`management/commands/services/services/flower.py`、`utils/media.py` |
| `DEBUG_DEV` | bool | False | 开发态增强开关（额外诊断输出/演示能力） | `base/decorators.py`、`core/exception.py`、`signal_handlers.py` |
| `API_LOG_ENABLE` | bool | None | 操作日志开关（中间件写 OperationLog） | `core/middleware.py` |
| `API_LOG_METHODS` | set[str] \| list[str] | None | 纳入操作日志的 HTTP 方法 | `core/middleware.py` |
| `API_LOG_IGNORE` | dict[str, list[str]] | None | 操作日志忽略表（model label / path → 方法清单） | `core/middleware.py` |
| `API_MODEL_MAP` | dict[str, str] | 必给 | 操作日志模块名映射（path → 展示名，缺省回落模型 label） | `core/middleware.py` |
| `ATOMIC_REQUESTS_SKIP_READ_ACTIONS` | bool | True | 纯读请求（GET/HEAD）免 ATOMIC_REQUESTS 事务 | `core/atomic_read.py` |
| `RECYCLE_BIN_RETENTION_DAYS` | int | 30 | 回收站保留天数（清理任务与回收站列表倒计时） | `core/modelset/recycle.py`、`tasks.py` |
| `LANGUAGE_CODE` | str | 必给 | 语言码（IP 归属地本地化、时间格式） | `utils/ip/geoip/utils.py`、`utils/ip/utils.py` |
| `DEFAULT_CHARSET` | str | 必给 | 默认字符集（axios 表单解析回退编码） | `drf/parsers/axios_form_data.py` |

**读取约定**：新代码推荐走契约面的读取助手——`kernel_setting("KEY")`（按缺省回落）与
`kernel_required_setting("KEY")`（缺失报 `ImproperlyConfigured` 并带用途提示）；既有直读点
保持原状（本阶段只落契约与守护，不做全量访问器改造）。

**混合读取形态**：`DEBUG` / `DEBUG_DEV` / `ALLOWED_HOSTS` 同时存在直读点与缺省读取点
（部署必给、开发态判定允许回落），由守护测试显式白名单化，防止新增键无意引入混合语义。

## 四、宿主对接与升级注记

- **Django 内置键**（`SECRET_KEY` / `AUTH_USER_MODEL` / `ALLOWED_HOSTS` / `BASE_DIR` /
  `MEDIA_ROOT` / `MEDIA_URL` / `EMAIL_HOST_USER` / `EMAIL_SUBJECT_PREFIX` / `DEBUG` /
  `API_MODEL_MAP` / `LANGUAGE_CODE` / `DEFAULT_CHARSET` 等）由 Django 或宿主 settings
  装配提供；契约表把它们与"内核自定义键"一并列出，便于二开项目对照自查。
- **旧部署升级**（源码目录由 `common/` 迁至 `packages/xadmin-common/common/`）：
  - 容器：`entrypoint.sh` 已注入 `PYTHONPATH=/data/xadmin-server/packages/xadmin-common`，
    镜像 ENV 同口径（`Dockerfile-base` / `Dockerfile-dev` / `Dockerfile`）——**bind mount
    部署重启容器即生效；镜像部署需重建镜像**；
  - 本地：`uv sync --all-groups` 后由 editable 安装提供 `common` 包（不再依赖仓库根同名目录）；
  - 文档/门禁/测试对内核路径的引用已同步（`ruff.toml` / `.coveragerc` / `scripts/check_*`）；
  - 完整的升级步骤见 [ops/deployment.md](../ops/deployment.md) §6.1「2026-10-08（框架内核独立分发包）升级注意」。
