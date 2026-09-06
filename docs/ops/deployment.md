# 部署与运维手册

> 本文档沉淀常用部署方式与运维要点（T6.2 本地化，外站 https://docs.dvcloud.xin/ 降级为补充资料）。
> 常见故障的「现象 → 定位 → 处置」速查见 [runbook.md](runbook.md)。
> 适用于 xadmin-server 4.2.5+（含队列拆分与健康检查增强）。

## 1. 本地开发

### 1.1 环境准备

```shell
# Python 3.13 虚拟环境
python3.13 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
```

依赖服务：PostgreSQL（或 SQLite）+ Redis。本地快速起 Redis：

```shell
docker run -d --name xadmin-redis -p 6379:6379 redis:7.4
```

### 1.2 配置与初始化

```shell
cp config_example.yml config.yml   # 按需修改（sqlite 本地开发：DB_ENGINE: sqlite3）
python manage.py migrate
python utils/init_data.py          # 初始数据 + 超管账号（仅库为空时生效）
```

生产环境必须设置 `SECRET_KEY`，否则服务拒绝启动（DEBUG 关闭时强制校验）。

- 超管初始密码：通过环境变量 `XADMIN_ADMIN_PASSWORD` 显式注入；未设置时 `init_data` 会随机生成强密码并**仅在初始化输出中打印一次**（首次登录后立即修改）。历史版本的默认密码 `xAdminPwd!` 已移除，升级不影响已存在的账号。

### 1.3 启动服务

```shell
python manage.py start all         # web(gunicorn+flower) + task(default/heavy worker + beat)
python manage.py start web         # 仅 web
python manage.py start task        # 仅任务（worker + beat，一个进程组）
python manage.py status            # 查看服务状态
python manage.py stop              # 停止
```

单服务粒度（容器编排推荐）：

```shell
python manage.py start gunicorn        # API 服务
python manage.py start flower          # 任务监控（/api/flower/）
python manage.py start celery_default  # 默认队列 worker（轻量任务）
python manage.py start celery_heavy    # heavy 队列 worker（导入/导出/批量任务）
python manage.py start beat            # 定时任务调度
```

## 2. Celery 队列划分

| 队列 | 承载内容 | worker |
|------|----------|--------|
| `celery`（默认） | 邮件/短信/站内信/周期清理等轻量任务 | `start celery_default` |
| `heavy` | 导入/导出/批量删除后台任务（`background_task_view_set_job`） | `start celery_heavy` |

- 路由配置：`server/settings/libs.py` 的 `CELERY_TASK_ROUTES`，新增重任务在此加一行即可。
- 健康检查：`utils/check_celery.sh [celery|heavy]`（依赖 worker 心跳文件，文件位于 `tempfile.gettempdir()`）。

## 3. Docker 部署

```shell
docker compose up -d
```

- 密码策略：compose 对 postgres 提供与 `config.yml` 对齐的默认密码兜底（`${DB_PASSWORD:-KGzKjZpWBp4R4RSa}`），本地开发开箱即用；**生产部署必须**通过环境变量或 `.env` 覆盖 `DB_PASSWORD` / `REDIS_PASSWORD` 为随机值（`config.yml` 中同步修改），否则使用默认密码等于裸奔。
- 服务拓扑（另含 `db-backup` 定时备份服务，见 §3.1）：

| 服务 | 说明 | 健康检查 |
|------|------|----------|
| nginx | 统一入口（8896） | - |
| server | API（gunicorn + flower） | HTTP `/api/common/api/health` |
| celery-worker | 默认队列 worker | 心跳文件（celery 队列） |
| celery-heavy | heavy 队列 worker | 心跳文件（heavy 队列） |
| celery-beat | 定时调度 | 进程探活 |
| db-backup | 每日 pg_dump 备份，滚动保留 7 天 | 日志（`docker logs xadmin-db-backup`） |
| postgresql / redis | 存储与 broker | 内置 |

### 3.1 数据库备份与恢复

- 备份：`db-backup` 服务每日自动执行 `pg_dump | gzip`，产出 `${VOLUME_DIR}/xadmin-db-backups/<库名>_<时间戳>.sql.gz`，滚动保留 7 天（`KEEP_DAYS` 可调）。
- 手动备份：`docker exec xadmin-db-backup bash -c 'source /utils/db_backup.sh'` 不可用（脚本为常驻循环），直接执行：
  `docker exec xadmin-postgresql pg_dump -U server -d xadmin | gzip > backup_$(date +%Y%m%d).sql.gz`
- 恢复（宿主机执行，会**清空重建**目标库，请先确认）：

```shell
sh utils/db_restore.sh ../xadmin-db-backups/xadmin_20260904_205752.sql.gz xadmin
```

- 建议定期将 `xadmin-db-backups` 目录同步到异地/对象存储；恢复流程至少每季度演练一次（RTO 目标 ≤30 分钟，见半年规划 P5）。

生产 `config.yml` 建议：

```yaml
ALLOWED_HOSTS:            # 必配，否则 Host 头校验拒绝
  - xadmin.example.com
CORS_ALLOWED_ORIGINS:     # 跨域部署时配置；nginx 同源反代无需配置
  - https://xadmin.example.com
```

## 4. 可观测性

### 4.1 健康检查

`GET /api/common/api/health`（免认证）：

```json
{
  "status": true,            // 核心依赖（DB+Redis）是否健康，供 LB/K8s 探针使用
  "db_status": true,
  "redis_status": true,
  "celery_status": false,    // 是否有在线 worker（inspect ping，1s 超时）
  "db_time": 0.001, "redis_time": 0.002, "celery_time": 0.9
}
```

- `status=false`：服务不可用，应告警/摘除节点
- `celery_status=false`：异步任务（导入导出、通知）不可用，但 API 服务仍正常

### 4.2 日志与请求 ID

- 每个请求由 `RequestMiddleware` 生成 `request_uuid`；上游网关可传 `X-Request-Id` 头透传（自动清洗，≤64 字符），响应头会回写 `X-Request-Id`。
- 所有日志行携带 `[requestUuid] [requestUser]`，API 错误响应体含 `requestId`，可按 ID 串联「用户反馈 → 接口日志 → 异常堆栈」。
- 日志文件：`data/logs/server.log`（按天轮转）、`drf_exception.log`、`unexpected_exception.log`。

### 4.3 任务失败告警

- worker 任务失败经 `task_failure` 信号触发 `TaskFailureMessage`，通过站内信 + 邮件通知超管。
- 同一任务 60 秒节流，防止失败风暴；通知任务自身的失败不再递归告警。

## 5. 常见问题排查

| 现象 | 原因与处理 |
|------|-----------|
| 登录页提示"当前服务器不允许登录" | 多为登录接口限流（默认 `login: 50/h`，GET/POST 共享额度）；检查是否被自动化/共享出口打满，可在 `config.yml` 的 `DEFAULT_THROTTLE_RATES` 调整 |
| `db_status: false` 但数据库正常 | 确认 `config.yml` 数据库连接项；4.2.5 起健康检查不再依赖 Monitor 表 |
| 导入/导出无响应 | 检查 heavy worker 是否在线（`celery_status`、flower 面板）；无 heavy worker 时任务滞留队列 |
| flower 无法访问 | flower 随 web 容器启动（`start web`），认证取 `CELERY_FLOWER_AUTH` 配置；未配置认证时仅允许绑定 127.0.0.1，绑定其他地址启动会被拒绝（见 [security-review.md](../security-review.md)） |
| 服务启动即退出 | `SECRET_KEY` 未设置（非 DEBUG 强制校验）；查看 `data/logs/` |

更多场景（登录锁定、WebSocket 不通、导入导出积压、权限不生效、磁盘占满、备份恢复、migrate 卡住、CVE 响应等）见 [runbook.md](runbook.md)。

## 6. 升级与回滚

### 6.1 升级流程

1. **备份先行**：确认最近一次 `db-backup` 产出完好（或手动 `pg_dump` 一次）；
2. **读变更说明**：Release Notes 中「升级注意」段落（破坏性迁移、新增必配项）；
3. **拉取新镜像/代码**：`docker compose pull`（或 `git pull` + 重建）；
4. **单实例迁移**：`python manage.py migrate`——多副本部署时保证只有一个实例执行迁移（其余实例先缩容），避免 DDL 互相锁；
5. **滚动重启**：`docker compose up -d` 逐服务重建，观察 healthz 四项全 `true` 再继续；
6. **验证**：登录冒烟（登录 → 菜单加载 → 任一列表页 → 一次导入导出）。

> 历史版本注意：4.2.5 起 compose 不再内置数据库/Redis 默认密码，升级前先在 `.env` 配置 `DB_PASSWORD`、`REDIS_PASSWORD`；队列拆分后首次升级，`docker compose up -d` 会新增 `celery-worker`/`celery-heavy`/`celery-beat` 三个容器并移除旧 `celery` 容器。

### 6.2 回滚

- 镜像回滚：`docker compose` 中把镜像 tag 固定到上一版本 `up -d`（Release 附件中的镜像 tag 见 release 页面）；
- 数据库回滚：**Django 迁移原则上不做反向回滚**——先恢复服务到旧版本运行，数据问题走 [runbook.md §12](runbook.md) 备份恢复（清空重建，RTO ≤30 分钟）；仅当上一版本明确依赖旧表结构且新迁移破坏读兼容时，才评估 `migrate <app> <旧迁移号>`；
- 升级失败快速止损顺序：服务回滚 → 确认 healthz → 数据恢复（最后手段）。

### 6.3 镜像与供应链

- 发布镜像经 trivy 扫描（HIGH/CRITICAL 阻断）并随 release 附 CycloneDX SBOM（T5.5），升级前可在 release 页面核对 SBOM 变更；
- base 镜像由 `build-base-image.yml` 自动构建回写，基础层 CVE 修复通过重建 base 镜像消化。

## 7. 国产化适配要点

| 组件 | 说明 |
|------|------|
| CPU 架构 | 镜像已多架构构建（linux/amd64 + linux/arm64），鲲鹏/飞腾等 ARM 环境直接拉取 |
| 操作系统 | 银河麒麟/统信 UOS 等可运行 arm64 容器环境直接使用；宿主机直装需 Python 3.12+ 与对应系统依赖（psycopg2/mysqlclient 编译链） |
| 数据库 | 默认 PostgreSQL（openGauss 兼容 PG 协议，`DB_ENGINE: postgresql` 尝试接入）；人大金仓/达梦需替换 Django 后端驱动并回归迁移文件 |
| 中间件 | Redis 兼容版本即可（缓存/broker 用途，无特殊命令依赖） |
| 验证清单 | 迁移全量通过 → 登录/验证码/图片处理（Pillow/GeoIP 库）→ 导入导出（openpyxl）→ WebSocket → 定时任务 |

> 国产化数据库替换涉及迁移文件与第三方库兼容性，属大变更：先建独立分支跑全量门禁（pytest + E2E），并登记 ADR 后再合入。
