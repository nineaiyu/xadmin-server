# 部署与运维手册 · 配置速查表（config-reference）

> 本文为《部署与运维手册》子页（§9 配置速查表）。
> 概览（本地开发与排查）见 [deployment.md](deployment.md)；容器部署见 [deployment-docker.md](deployment-docker.md)。

## 9. 配置速查表

> 覆盖「启动必须知道」的进程级配置项。默认值以 `config_example.yml`（未创建 `config.yml` 时的兜底配置）
> 为准，与 `server/conf/defaults.py` 的代码兜底一致；两处不一致的项在下表单独标注。

**取值优先级**：`config.yml` / `config.py` 的值 > **同名环境变量** > 代码兜底默认值。
配置文件里写了空值（如 `SECRET_KEY:`）等同未赋值，环境变量仍可生效。
环境变量按兜底默认值的类型自动转换（bool / 数字 / 列表与字典用 JSON 串）。
容器形态下 compose 未声明 `environment` 透传，需在 compose override 的 `environment` 段
或 `docker compose exec -e KEY=value` 显式传入。

### 9.1 启动与安全

| 配置键 | 环境变量 | 默认值 | 必填 | 说明 |
|---|---|---|---|---|
| `SECRET_KEY` | 同名 | 空 → 自动生成并持久化 `data/.secret_key` | 生产必填 | 同时用于 JWT 签名与字段级加密；多实例必须一致，丢失会导致登录态失效与已加密数据不可解 |
| `SECRET_KEY_AUTO_GENERATE` | 同名 | `false` | 否 | 显式 `true` 强制自动生成；回落 `config_example.yml` 或 `DEBUG=true` 时自动生效 |
| `DEBUG` | 同名 | `false` | 否 | 开发开启；开启后 Host 校验放行、WebSocket 走 daphne |
| `DEBUG_DEV` | 同名 | `false` | 否 | 输出 SQL 日志 |
| `ALLOWED_HOSTS` | 同名（JSON 数组） | `[]` | 生产必填 | DEBUG 下默认放行所有 Host |
| `TRUSTED_PROXY_IPS` | 同名（JSON 数组） | `[]` | HTTP 反代场景必填 | 仅代理会注入 `X-Forwarded-For` 时可配；纯 TCP 代理勿配 |
| `SECURITY_HTTPS_ENABLED` | 同名 | `false` | 否 | 开启后强制 Secure Cookie + HSTS |
| `SECURITY_HTTPS_REDIRECT_ENABLED` | 同名 | `false` | 否 | 要求代理传递 `X-Forwarded-Proto` |
| `CORS_ALLOW_ALL_ORIGINS` / `CORS_ALLOWED_ORIGINS` | 同名 | `false` / `[]` | 跨域部署必填 | 同源（nginx 反代）部署无需配置 |
| `LANGUAGE_CODE` / `TIME_ZONE` | 同名 | `zh-hans` / `Asia/Shanghai` | 否 | |
| `MEDIA_X_ACCEL_PREFIX` | 同名 | 空 | 否 | 受鉴权媒体的 nginx 内部重定向前缀；生产推荐 `/_protected_media`（需 nginx 声明同名前缀的 `internal` location，见 [§3.2](#32-受鉴权媒体与出站请求2026-09-27)） |

### 9.2 数据库

| 配置键 | 环境变量 | 默认值 | 必填 | 说明 |
|---|---|---|---|---|
| `DB_ENGINE` | 同名 | `postgresql` | 否 | 取值 `sqlite3` / `mysql` / `oracle` / `postgresql` / `vastbase` |
| `DB_HOST` | 同名 | `postgresql`（compose 服务名） | 否 | **非 Docker 本地开发改 `127.0.0.1`** |
| `DB_PORT` | 同名 | `5432` | 否 | |
| `DB_USER` / `DB_DATABASE` | 同名 | `server` / `xadmin` | 否 | |
| `DB_PASSWORD` | 同名 | 空（compose 强制注入，见 `.env.example`） | 生产必填 | 生产必须改为随机值，`config.yml` 与 compose `.env` 同步 |
| `DB_POOL` | 同名 | `true` | 否 | 仅 `DB_ENGINE=postgresql` 生效（psycopg3 服务端连接池） |
| `DB_POOL_MIN_SIZE` / `DB_POOL_MAX_SIZE` | 同名 | `2` / `8` | 否 | 容量核算：`GUNICORN_MAX_WORKER × MAX_SIZE + celery 子进程数 × MAX_SIZE` 应小于 PG `max_connections` |

本地非 Docker 的三种连法（SQLite / 本机 PG / 本机 MySQL）见 `config_example.yml` 数据库段的注释块。

### 9.3 Redis

| 配置键 | 环境变量 | 默认值 | 必填 | 说明 |
|---|---|---|---|---|
| `REDIS_HOST` | 同名 | `redis`（compose 服务名） | 否 | 非 Docker 本地开发改 `127.0.0.1` |
| `REDIS_PORT` | 同名 | `6379` | 否 | |
| `REDIS_PASSWORD` | 同名 | 空（compose 强制注入，见 `.env.example`） | 生产必填 | |
| `DEFAULT_CACHE_ID` / `CHANNEL_LAYERS_CACHE_ID` / `CELERY_BROKER_CACHE_ID` | 同名 | `1` / `2` / `3` | 否 | 缓存 / WebSocket / broker 三库分离 |

### 9.4 服务与任务

| 配置键 | 环境变量 | 默认值 | 必填 | 说明 |
|---|---|---|---|---|
| `HTTP_BIND_HOST` / `HTTP_LISTEN_PORT` | 同名 | `0.0.0.0` / `8896` | 否 | |
| `GUNICORN_MAX_WORKER` | 同名 | `4` | 否 | API worker 数；容量规划与实测数据见 §3.3 |
| `CELERY_WORKER_COUNT` | 同名 | `4` | 否 | 默认队列 worker 并发（模板与代码兜底已对齐） |
| `CELERY_HEAVY_POOL` / `CELERY_HEAVY_CONCURRENCY` | 同名 | `threads` / `4` | 否 | heavy 队列（导入/导出/批量）worker |
| `CELERY_FLOWER_HOST` / `CELERY_FLOWER_PORT` | 同名 | `127.0.0.1` / `5566` | 否 | |
| `CELERY_FLOWER_AUTH` | 同名 | 空 | 非本机访问必填 | 未配置时 Flower 仅允许绑定 `127.0.0.1`，绑其他地址拒绝启动 |

### 9.5 模块裁剪与应用登记

| 配置键 | 环境变量 | 默认值 | 必填 | 说明 |
|---|---|---|---|---|
| `MODULE_PRESET` | 同名 | `full` | 否 | `core` / `standard` / `full`，详见[模块化与功能裁剪](../architecture/模块化与功能裁剪.md) |
| `MODULE_ENABLE` / `MODULE_DISABLE` | 同名（JSON 数组） | `[]` | 否 | 在预设基础上增删模块 |
| `XADMIN_APPS` | 同名（JSON 数组） | `["demo"]` | 否 | 新业务 app 必须登记（参与 migrate 与路由注入）；**代码兜底值为 `[]`**（模板为开发友好开启 demo） |

### 9.6 可观测与调试

| 配置键 | 环境变量 | 默认值 | 必填 | 说明 |
|---|---|---|---|---|
| `LOG_LEVEL` / `LOG_FORMAT` / `LOG_BACKUP_COUNT` | 同名 | `WARNING` / `text` / `30` | 否 | 日志级别 / `text` 或 `json` / 按天滚动保留天数（0 = 不清理） |
| `SENTRY_DSN` | 同名 | 空 | 否 | 空 = 完全不初始化 |
| `SENTRY_ENVIRONMENT` / `SENTRY_TRACES_SAMPLE_RATE` | 同名 | `production` / `0.0` | 否 | |
| `METRICS_ENABLED` / `METRICS_TOKEN` | 同名 | `true` / 空 | 否 | 默认开启（ADR-082 转正）；抓取仍需配置 token（`Authorization: Bearer <token>`），未配置时端点 403 |
| `BASIC_AUTH_ENABLED` | 同名 | `false` | 否 | base64 明文凭证，仅本地调试开启 |
| `SILK_ENABLED` | 同名 | `false` | 否 | 仅 `DEBUG` / `DEBUG_DEV` 生效，需装 `requirements-dev.txt` |

### 9.7 初始化专用（非 Django 配置）

| 名称 | 来源 | 默认值 | 说明 |
|---|---|---|---|
| `XADMIN_ADMIN_PASSWORD` | 环境变量（`init_data.py` 专用） | 随机生成 | 优先级：命令行 `--admin-password` > 该环境变量 > 随机生成（仅打印一次） |

### 9.8 生效方式：重启 vs 热更新

| 类别 | 覆盖范围 | 生效方式 |
|---|---|---|
| **进程级配置** | 本表 §9.1–§9.6 全部键 | 进程启动期读取 → 改后**重启**（`python manage.py restart` 或 `docker compose up -d`） |
| **运行期参数** | `SysConfig` 属性键：`FILE_UPLOAD_SIZE` / `PICTURE_UPLOAD_SIZE` / `PAT_RATE_LIMIT` / `CSP_MODE` / `CSP_REPORT_URI` / `OAUTH_PROVIDERS` / `SCIM_*` / `AUDIT_DIFF_MODELS` / `OUTBOUND_ALLOWED_HOSTS` / 审批相关 / 文件预览与保留期 / 导入导出保留期 / `CHAT_HISTORY_DAYS` / `BACKUP_ALERT_TOKEN` / `OPS_ALERT_TOKEN` 等 | 管理页「系统管理 → 系统配置」修改后**即时生效**（保存即失效缓存）。这些键的默认值单源回读 `config.yml`（即上表值可作为初值），有 DB 行时以行值为准 |
| **用户级配置** | `WEB_SITE_CONFIG` / `PUSH_MESSAGE_NOTICE` / `PUSH_CHAT_MESSAGE` | 用户可在「账户设置」页覆盖个人值，即时生效 |

> 完整运行期参数清单与语义见 `loadjson/systemconfig.json`（种子初值）与
> [packages/xadmin-common/common/README.md](../../packages/xadmin-common/common/README.md)；用户级覆盖的读取链路见
> [architecture/cache.md](../architecture/cache.md)。
