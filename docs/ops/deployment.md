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
python utils/init_data.py          # 初始数据 + 超管账号（幂等，可重复执行；升级后建议执行一次）
```

`SECRET_KEY` 取值规则（缺失时）：无任何配置文件（回落 `config_example.yml`）或 `DEBUG=true` 时自动生成并持久化到
`data/.secret_key`（仅限开发/首次体验）；显式配置 `config.yml` 且 DEBUG 关闭时缺失则拒绝启动（生产必须显式配置；
如需强制自动生成可设 `SECRET_KEY_AUTO_GENERATE=true`）。

- 超管初始密码：命令行 `--admin-password` > 环境变量 `XADMIN_ADMIN_PASSWORD` > 随机生成（**仅在初始化输出中打印一次
  **，首次登录后立即修改）。历史版本的默认密码 `xAdminPwd!` 已移除（安装器现会生成随机密码并回写 `config.txt`），
  升级不影响已存在的账号；
- `init_data` 常用参数：`--with-demo`（追加演示数据）、`--skip-ip-db`（离线/内网跳过 IP 库下载）、
  `--admin-password`（显式指定初始密码）。

> 配置项（键 / 环境变量 / 默认值 / 必填 / 生效方式）的完整速查见 [§9 配置速查表](#9-配置速查表)；
> 本地非 Docker 开发的数据库连法见 `config_example.yml` 数据库段注释块。

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

### 1.4 AI 助手知识库（可选）

「集成管理 → AI 助手」是基于仓库文档的问答（ADR-023，回答带引用出处，不触生产数据）。
启用前需要两步：

```shell
python manage.py sync_ai_knowledge   # ① 同步知识库（幂等，秒级，可重复执行）
```

② 在「集成管理 → AI 配置」填写 OpenAI 兼容的 `base_url` / `api_key` / `model`
（DeepSeek、Qwen、Kimi、vLLM、Ollama 等均可）并打开开关；助手页状态区会显示已入库知识块数量。

- **文档来源（双来源，ADR-033）**：
  - 仓库文档：`docs/**/*.md` + 根目录 `README.md` / `CONTRIBUTING.md`——把 markdown 放进 `docs/`
    （或挂载卷覆盖该目录）后重跑 ① 即可（按内容 hash 增量更新，删除的文档同步移除）；
  - 上传文档：管理端「集成管理 → 知识库」页自助上传（选择本地 .md 读取或直接粘贴文本，同名覆盖更新），
    与仓库文档并存参与检索，可预览全文/分块、启停（停用即退出检索）、删除；
- **同步时机**：仓库文档变更后重跑 ①（或管理页「同步仓库文档」按钮）；未同步时助手页会提示知识库为空，问答无召回。

### 1.5 演示数据（可选，开发 / 演示环境）

一键加载可交互的演示数据（组织、审批、请假、表单、通知公告、聊天室、知识库、文件、
Webhook、开放平台应用等），让各功能页面开箱有内容；卸载命令对称可清：

```shell
python manage.py seed_demo_all               # 一键加载全部（幂等，可重复执行）
python manage.py seed_demo_all --reset       # 先彻底清理再加载
python manage.py seed_demo_clean             # 一键卸载（清理演示数据并回滚对内置种子的改写）
```

| 分项命令            | 内容                                                              |
|-----------------|-----------------------------------------------------------------|
| `seed_demo_org`    | 示例组织（研发部/财务部）+ 预置角色（菜单/字段/数据四层权限）+ 场景模板（报销流程、入职登记表） |
| `seed_demo_flows`  | 审批实例（待办/通过/驳回）+ 轻量审批单五态 + 表单提交                  |
| `seed_demo_leave`  | 请假业务闭环（通过/驳回/待审/草稿）                                     |
| `seed_demo_content` | 通知公告、聊天室历史消息、知识库文档、文件中心示例文件、审批委托、Webhook 订阅与投递审计、开放平台应用 |
| `seed_demo_users`  | 批量演示用户（`--count` 控制数量，撑起数据集的趋势与分布）                  |

说明：

- 演示用户（`demo_` 前缀）为不可登录账号（unusable password），**不会**进入正式初始化
  种子（`load_init_json`）；示例账号 `demo_staff` / `demo_lead` / `demo_fin`（seed_demo_org）
  可登录，初始密码 `Demo@2026!`（`--password` 可改）；
- 各命令全部幂等（固定标识 / 固定主键），可重复执行；`--clean-only` 只清理不生成
  （`seed_demo_clean` 的编排入口）；
- `seed_demo_clean` 会回滚对内置种子的改写（流程节点审批人、演示部门负责人、演示版本快照），
  内置定义类数据（loadjson 的示例流程/表单/数据集/看板等）不在卸载范围。

## 2. Celery 队列划分

| 队列           | 承载内容                                           | worker                 |
|--------------|------------------------------------------------|------------------------|
| `celery`（默认） | 邮件/短信/站内信/周期清理等轻量任务                            | `start celery_default` |
| `heavy`      | 导入/导出/批量删除后台任务（`background_task_view_set_job`）、Office 转 PDF 预览（`convert_office_preview_task`） | `start celery_heavy`   |

- 路由配置：`server/settings/libs.py` 的 `CELERY_TASK_ROUTES`，新增重任务在此加一行即可。
- 健康检查：`utils/check_celery.sh [celery|heavy]`（依赖 worker 心跳文件，文件位于 `tempfile.gettempdir()`）。

### 2.1 Office 在线预览（可选依赖 LibreOffice，ADR-013）

docx/xlsx/pptx 等文档预览依赖 **LibreOffice headless** 把源文件转成 PDF：

- 安装（二选一，默认镜像**不内置**以控制体积；未安装时 Office 文件按「不支持预览」降级，其余功能不受影响）：

```shell
# 宿主机（Debian/Ubuntu 系）
apt-get install -y libreoffice --no-install-recommends
# 或在业务镜像追加（Dockerfile 片段）
RUN apt-get update && apt-get install -y --no-install-recommends libreoffice && rm -rf /var/lib/apt/lists/*
```

- 中文/常见字体缺失会导致转换后排版偏差，生产建议同时安装 `fonts-noto-cjk` 等字体包；
- 转换器路径默认自动探测（PATH、macOS 常见安装路径），可用 `FILE_OFFICE_SOFFICE_BIN` 显式指定；
- 相关配置：`FILE_OFFICE_PREVIEW_ENABLED`（总开关，默认开）、`FILE_OFFICE_MAX_BYTES`（默认 20MB，超限不转换）、
  `FILE_OFFICE_CONVERT_TIMEOUT`（默认 60s）、`FILE_OFFICE_WAIT_SECONDS`（请求侧等待窗口，默认 8s）；
- 转换产物与图片预览缓存同目录（`preview_cache/<pk>/office.pdf`），随既有预览缓存清理任务回收；
- 首次预览需等待转换（前端显示「文档转换中」并自动重试），后续命中缓存直接打开。

## 3. Docker 部署

```shell
docker compose up -d
```

- 密码策略：compose 对 postgres 提供与 `config.yml` 对齐的默认密码兜底（`${DB_PASSWORD:-KGzKjZpWBp4R4RSa}`），本地开发开箱即用；
  **生产部署必须**通过环境变量或 `.env` 覆盖 `DB_PASSWORD` / `REDIS_PASSWORD` 为随机值（`config.yml` 中同步修改），否则使用默认密码等于裸奔。
- HTTPS 部署（可选，默认关闭 = HTTP 直连部署零影响）：TLS 终止于反向代理/网关后，在 `config.yml`
  设 `SECURITY_HTTPS_ENABLED: true`，即下发 HSTS 一年（含子域/preload）与 Secure Cookie；
  若还需 Django 侧执行 HTTP→HTTPS 跳转，再开 `SECURITY_HTTPS_REDIRECT_ENABLED: true`
  ——**要求代理正确传递 `X-Forwarded-Proto: https`**，纯 TCP stream 代理（内置 nginx 默认形态）
  下开启会造成重定向循环，保持关闭、由网关侧做跳转。
- 服务拓扑（另含 `db-backup` 定时备份服务，见 §3.1）：

| 服务                 | 说明                     | 健康检查                               |
|--------------------|------------------------|------------------------------------|
| nginx              | 统一入口（8896）             | -                                  |
| server             | API（gunicorn + flower） | HTTP `/api/common/api/health`      |
| celery-worker      | 默认队列 worker            | 心跳文件（celery 队列）                    |
| celery-heavy       | heavy 队列 worker        | 心跳文件（heavy 队列）                     |
| celery-beat        | 定时调度                   | 进程探活                               |
| db-backup          | 每 6h pg_dump 备份 + 媒体目录 + 异地副本，滚动保留 7 天 | 日志（`docker logs xadmin-db-backup`） |
| postgresql / redis | 存储与 broker             | 内置                                 |

### 3.1 数据库备份与恢复

> 2026-09-08 收口：异地副本、媒体目录、RPO 6h 三项已落地（下期规划 N1/L1），
> 详见 [backup-drill-2027-03.md](backup-drill-2027-03.md)。

- 备份：`db-backup` 服务每 **6 小时**（`BACKUP_INTERVAL`，原 24h）自动执行 `pg_dump | gzip`，产出
  `${VOLUME_DIR}/xadmin-db-backups/<库名>_<时间戳>.sql.gz`，滚动保留 7 天（`KEEP_DAYS`）。
  每个包附带 `.sha256` 校验和，落盘即做 `gzip -t` 完整性校验，损坏包不落正式名。
- 媒体目录：`BACKUP_MEDIA=true`（默认）时把 `./data/upload` 打包为同名 `.media.tar.gz` 一并备份。
- 异地副本：`BACKUP_REMOTE_TYPE` 支持 `local`（独立磁盘/NFS 挂载点）、`rsync`（远端主机）、`rclone`（云对象存储）；
  留空表示未启用。**生产必须指向与源库不同故障域的存储**，否则同盘故障仍会双丢。
- 手动/单次备份（脚本已支持单次模式，无需再手写 pg_dump）：

```shell
docker exec -e BACKUP_ONCE=1 xadmin-db-backup bash /utils/db_backup.sh
```

- 恢复（宿主机执行，会**清空重建**目标库，请先确认）：

```shell
sh utils/db_restore.sh ../xadmin-db-backups/xadmin_20260904_205752.sql.gz xadmin
# 演练/自动化：YES_I_KNOW=1 跳过交互确认；RESTORE_MEDIA=1 同时解包媒体目录
YES_I_KNOW=1 RESTORE_MEDIA=1 sh utils/db_restore.sh <备份包> xadmin_restore_test
```

- WAL 归档（PITR）：**2026-09-16 已启用**——`archive_mode=on` + `archive_timeout=60`
  （RPO 1 分钟），gzip 压缩归档至 `${VOLUME_DIR}/xadmin-postgresql/archive`；
  归档链路巡检（失败态 + 积压滞留）已并入 `db-backup` 每轮检查，
  时间点回放演练见 [pitr.md](pitr.md)。

- 一键演练（备份 → 异地校验 → 恢复验证库 → 逐表行数对比 → 输出报告，约 2s）：

```shell
bash utils/backup_drill.sh
BACKUP_REMOTE_DIR=../xadmin-db-backups-remote bash utils/backup_drill.sh   # 含异地副本校验
```

- 演练记录：2026-09-07 首次正式演练通过（RTO 0.88s、52 表逐行一致，见
  [backup-drill-2026-09.md](backup-drill-2026-09.md)）；2026-09-08 异地副本收口演练通过
  （RTO 0.89s、53 表 0 不一致、异地 sha256 一致，见 [backup-drill-2027-03.md](backup-drill-2027-03.md)）。
- **季度演练常态化（N5，2026-09-11 起）**：每季度执行一轮上述检查清单 + `backup_drill.sh`
  全项演练，报告存入 `docs/ops/backup-drill-<年>-<季度>.md`（沿用现有模板：
  范围/方法 → RTO 与一致性 → 异地副本校验 → 遗留缺口）。`backup-drill-reminder.yml`
  workflow 在 3/6/9/12 月 8 日自动开提醒 issue（去重），按清单执行后勾选关闭；
  若 workflow 未生效，请人工按本节清单执行，不得跳过「逐表行数一致」项。
  **2026 Q4 报表（提前于 2026-09-11 执行）见 [backup-drill-2026-Q4.md](backup-drill-2026-Q4.md)**：
  67 表逐表 0 不一致、RTO 0.28s，并一并验收了 S2 失败告警接线。
- **备份/恢复检查清单**（部署验收与季度演练用）：

```markdown
- [ ] db-backup 容器 healthy 且 xadmin-db-backups/ 有当日 .sql.gz（6h 一备，非每日）
- [ ] BACKUP_REMOTE_TYPE/TARGET 已配置，且异地目标位于独立故障域（非同盘目录）
- [ ] 异地副本有当日同名文件且 sha256 与本地一致
- [ ] BACKUP_MEDIA=true 且 .media.tar.gz 随数据库包一起产出（生产有附件时必查）
- [ ] 演练：bash utils/backup_drill.sh 全项 PASS（尤其「逐表行数一致」不得为 0 表）
- [ ] 确认恢复目标库不得指向 xadmin（db_restore.sh 会先 DROP 目标库）
- [ ] 备份与异地同步失败已接入告警（S2：`BACKUP_ALERT_URL` + `BACKUP_ALERT_TOKEN`，
      失败点上报 `POST /api/common/api/backup-alert` → 站内信/邮件通知超管；未配置时仅落 WARN 日志）
```

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

- 每个请求由 `RequestMiddleware` 生成 `request_uuid`；上游网关可传 `X-Request-Id` 头透传（自动清洗，≤64 字符），响应头会回写
  `X-Request-Id`。
- 所有日志行携带 `[requestUuid] [requestUser]`，API 错误响应体含 `requestId`，可按 ID 串联「用户反馈 → 接口日志 → 异常堆栈」。
- 日志文件：`data/logs/server.log`（按天轮转）、`drf_exception.log`、`unexpected_exception.log`。

### 4.3 任务失败告警

- worker 任务失败经 `task_failure` 信号触发 `TaskFailureMessage`，通过站内信 + 邮件通知超管。
- 同一任务 60 秒节流，防止失败风暴；通知任务自身的失败不再递归告警。

## 5. 常见问题排查

| 现象                        | 原因与处理                                                                                                                                        |
|---------------------------|----------------------------------------------------------------------------------------------------------------------------------------------|
| 登录页提示"当前服务器不允许登录"         | 多为登录接口限流（默认 `login: 50/h`，GET/POST 共享额度）；检查是否被自动化/共享出口打满，可在 `config.yml` 的 `DEFAULT_THROTTLE_RATES` 调整                                       |
| `db_status: false` 但数据库正常 | 确认 `config.yml` 数据库连接项；4.2.5 起健康检查不再依赖 Monitor 表                                                                                             |
| 导入/导出无响应                  | 检查 heavy worker 是否在线（`celery_status`、flower 面板）；无 heavy worker 时任务滞留队列                                                                       |
| flower 无法访问               | flower 随 web 容器启动（`start web`），认证取 `CELERY_FLOWER_AUTH` 配置；未配置认证时仅允许绑定 127.0.0.1，绑定其他地址启动会被拒绝（见 [security-review.md](../security-review.md)） |
| 服务启动即退出                   | `SECRET_KEY` 未设置（非 DEBUG 强制校验）；查看 `data/logs/`                                                                                               |

更多场景（登录锁定、WebSocket 不通、导入导出积压、权限不生效、磁盘占满、备份恢复、migrate 卡住、CVE
响应等）见 [runbook.md](runbook.md)。

## 6. 升级与回滚

### 6.1 升级流程

1. **备份先行**：确认最近一次 `db-backup` 产出完好（或手动 `pg_dump` 一次）；
2. **读变更说明**：Release Notes 中「升级注意」段落（破坏性迁移、新增必配项）；
3. **拉取新镜像/代码**：`docker compose pull`（或 `git pull` + 重建）；
4. **单实例迁移**：`python manage.py migrate`——多副本部署时保证只有一个实例执行迁移（其余实例先缩容），避免 DDL 互相锁；
5. **滚动重启**：`docker compose up -d` 逐服务重建，观察 healthz 四项全 `true` 再继续；
6. **验证**：登录冒烟（登录 → 菜单加载 → 任一列表页 → 一次导入导出）。

> 涉及新增菜单/权限点或 gettext 文案的版本，升级后执行：
> `python manage.py post_upgrade`（= 内置种子 `load_init_json` + `compilemessages` + 配置缓存失效 + 权限点缺口扫描，
> 幂等可重跑；**安装器升级流程已自动调用**），随后重启容器——权限点未灌库时非超管角色不会出现新入口（接口 403），
> 文案未编译时中文界面回退英文。仅需补权限点时可改用 `sync_menu_permissions`（二者按版本说明择一）。

> 历史版本注意：compose 内置与 `config.yml` 对齐的数据库/Redis 默认密码兜底（单机自用决策，见 docker-compose.yml 注释）——*
*生产部署必须**通过环境变量或 `.env` 覆盖 `DB_PASSWORD` / `REDIS_PASSWORD` 为随机值，并在 `config.yml` 中同步修改（config.yml
> 为应用运行时唯一定义处）；队列拆分后首次升级，`docker compose up -d` 会新增 `celery-worker`/`celery-heavy`/`celery-beat`
> 三个容器并移除旧 `celery` 容器。

> **PostgreSQL 部署升级注意（TD-25/ADR-006，2026-09-07）**：驱动由 `psycopg2-binary` 切换为 `psycopg[binary,pool]`
> （psycopg3），`DB_ENGINE=postgresql` 时默认启用 Django server 端连接池（`OPTIONS.pool`），连接生命周期由池管理（
`CONN_MAX_AGE`
> 自动归零）。新增可选配置 `DB_POOL`（默认 true）/ `DB_POOL_MIN_SIZE`（2）/ `DB_POOL_MAX_SIZE`（8）；如需回退旧行为设
`DB_POOL: false`。容量核算：`GUNICORN_MAX_WORKER × DB_POOL_MAX_SIZE + celery 子进程数 × DB_POOL_MAX_SIZE` 应小于 PG
`max_connections`。MySQL 部署不受影响。

### 6.2 回滚

- 镜像回滚：`docker compose` 中把镜像 tag 固定到上一版本 `up -d`（Release 附件中的镜像 tag 见 release 页面）；
- 数据库回滚：**Django 迁移原则上不做反向回滚**——先恢复服务到旧版本运行，数据问题走 [runbook.md §12](runbook.md)
  备份恢复（清空重建，RTO ≤30 分钟）；仅当上一版本明确依赖旧表结构且新迁移破坏读兼容时，才评估 `migrate <app> <旧迁移号>`；
- 升级失败快速止损顺序：服务回滚 → 确认 healthz → 数据恢复（最后手段）。

### 6.3 镜像与供应链

- 发布镜像经 trivy 扫描（HIGH/CRITICAL 阻断）并随 release 附 CycloneDX SBOM（T5.5），升级前可在 release 页面核对 SBOM 变更；
- base 镜像由 `build-base-image.yml` 自动构建回写，基础层 CVE 修复通过重建 base 镜像消化。

## 7. 国产化适配要点

| 组件     | 说明                                                                                           |
|--------|----------------------------------------------------------------------------------------------|
| CPU 架构 | 镜像已多架构构建（linux/amd64 + linux/arm64），鲲鹏/飞腾等 ARM 环境直接拉取                                        |
| 操作系统   | 银河麒麟/统信 UOS 等可运行 arm64 容器环境直接使用；宿主机直装需 Python 3.12+ 与对应系统依赖（psycopg2/mysqlclient 编译链）        |
| 数据库    | 默认 PostgreSQL（openGauss 兼容 PG 协议，`DB_ENGINE: postgresql` 尝试接入）；人大金仓/达梦需替换 Django 后端驱动并回归迁移文件 |
| 中间件    | Redis 兼容版本即可（缓存/broker 用途，无特殊命令依赖）                                                           |
| 验证清单   | 迁移全量通过 → 登录/验证码/图片处理（Pillow/GeoIP 库）→ 导入导出（openpyxl）→ WebSocket → 定时任务                       |

> 国产化数据库替换涉及迁移文件与第三方库兼容性，属大变更：先建独立分支跑全量门禁（pytest + E2E），并登记 ADR 后再合入。

## 8. 安全响应头：CSP（S3）

Django 侧由 `django-csp 4.0` 生成策略，运行期模式由系统配置控制（默认**观察期**，不拦截请求）：

| 配置项 | 取值 | 说明 |
|---|---|---|
| `CSP_MODE` | `report-only`（默认）/ `enforce` / `disabled` | report-only 只下发 `Content-Security-Policy-Report-Only`；切 `enforce` 后同一策略串改为强制头 |
| `CSP_REPORT_URI` | 空（默认）/ `/api/common/api/csp-report` | 非空时在策略尾追加 `report-uri`，违规上报落 `data/logs/server.log`（WARNING，60s 同源节流） |

- **上线节奏**：新版先跑 report-only 观察（或按 `pnpm test:e2e:csp` 做隔离验证），在日志里按
  `grep "CSP violation" data/logs/server.log` 统计 `directive=... blocked=...`，确认无业务阻塞
  （图片/字体/WS/预览内嵌均已放开）后再切强制头（Django 侧改 `CSP_MODE` 即时生效；页面层改 nginx 头 + reload）；
- **策略要点**：`default-src 'self'`、`script-src 'self'`、`object-src 'none'`、`frame-ancestors 'self'`；
  按需放开 `worker-src blob:`（version-rocket 的 Blob 轮询 Worker）、`style-src 'unsafe-inline'`
  （Element Plus 注入内联样式）、`img-src data: blob:`、`frame-src blob:`（文档预览内嵌）、
  `connect-src ws: wss:`（WebSocket）——**connect-src 不含任何外部主机**（图标已离线化，见下）；
  `/media/`、`/api/static/`、`/api-docs/` 前缀豁免；
- **前端零在线依赖（离线/内网可用，2026-09-18）**：图标（菜单/路由 meta 的 `ep:*`、代码内的
  `ri/xxx`、图标选择器目录）全部来自**打包产物**——常用图标随包注册
  （`components/ReIcon/src/offlineIcon.ts`），其余按 set 前缀懒加载构建期内置的图标集
  （`src/components/ReIcon/src/iconRegistry.ts` 动态 import `@iconify/json`，产出同源 chunk，
  首屏不加载），**不再请求 api.iconify.design 等在线图标 API**；图标集 chunk 体积：
  ep ≈ 31 KB gzip / ri ≈ 262 KB gzip / fa-solid ≈ 212 KB gzip（按需加载）。
- **前端 SPA 的 CSP**（2026-09-18 已切强制）：SPA 由 nginx 托管时，Django 的响应头不覆盖 HTML 文档，
  需在 nginx 侧下发同一策略串——**三处同源**：`_CSP_DIRECTIVES`（服务端）、
  `xadmin-web/default.conf`（页面层）、`xadmin-client/scripts/csp-page-server.mjs`（隔离验证服务），
  漂移由 `tests/unit/common/test_csp.py::TestCSPPolicySync` 守护：

```nginx
# 页面层（当前生效）：强制头 + 上报到真实端点（/api/common/api/csp-report，注意 common 前缀，
# 写成 /api/csp-report 会 404 导致上报静默丢失）
add_header Content-Security-Policy "default-src 'self'; script-src 'self'; worker-src 'self' blob:; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self' data:; connect-src 'self' ws: wss:; frame-src 'self' blob:; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'self'; report-uri /api/common/api/csp-report" always;
# 回滚：同一策略串改回 Content-Security-Policy-Report-Only 后 nginx -s reload
```

- **切换前的隔离验证**（替代「等真实流量观察零」，测试服不可达时尤其有用）：
  `pnpm build && pnpm test:e2e:csp`——以构建产物 + 强制头 + 真实浏览器扫核心页并断言零违规
  （`/__csp_probe` 负对照证明采集链路有效，同时断言 report-uri 可达 204）；
  跑批默认 `E2E_CSP_TLS=1`：验证服务以 **HTTPS** 提供（openssl 自签 + `ignoreHTTPSErrors`），
  **chromium 与 webkit 双浏览器**均验证零违规（WebKit 在 http 形态拒收 Secure Cookie 无法登录）；
- **注意（http 部署 + 认证 Cookie）**：生产构建的认证 Cookie 带 `Secure`（`src/utils/auth.ts` 的
  `import.meta.env.PROD` 分支），浏览器只在 **loopback（`localhost` / `127.0.0.1`）** 视为可信；
  用 IP 或域名走 http 访问时**所有浏览器都会拒收 Secure Cookie → 登录必然失败**
  （实测 2026-09-20：`http://192.168.0.200/` 登录后 cookie jar 为空、回跳登录页；同地址 https 正常。
  WebKit 在 loopback 下同样拒收，比 Chromium 更严格）；生产形态请按 §3 启用 HTTPS
  （`SECURITY_HTTPS_ENABLED: true`）。隔离验证的 webkit 覆盖已随 TLS 形态补上（2026-09-18），
  但线上 http 形态下 WebKit 用户仍无法登录——**生产/测试服部署应走 HTTPS**。

- 注意：`add_header` 在 nginx 中会**覆盖**继承的同名头，若已有 `X-Frame-Options` 等自定义头，
  请放在同一个 `add_header` 块内统一维护，避免互相覆盖。

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

### 9.2 数据库

| 配置键 | 环境变量 | 默认值 | 必填 | 说明 |
|---|---|---|---|---|
| `DB_ENGINE` | 同名 | `postgresql` | 否 | 取值 `sqlite3` / `mysql` / `oracle` / `postgresql` / `vastbase` |
| `DB_HOST` | 同名 | `postgresql`（compose 服务名） | 否 | **非 Docker 本地开发改 `127.0.0.1`** |
| `DB_PORT` | 同名 | `5432` | 否 | |
| `DB_USER` / `DB_DATABASE` | 同名 | `server` / `xadmin` | 否 | |
| `DB_PASSWORD` | 同名 | 空（compose 兜底 `KGzKjZpWBp4R4RSa`） | 生产必填 | 生产必须改为随机值，`config.yml` 与 compose `.env` 同步 |
| `DB_POOL` | 同名 | `true` | 否 | 仅 `DB_ENGINE=postgresql` 生效（psycopg3 服务端连接池） |
| `DB_POOL_MIN_SIZE` / `DB_POOL_MAX_SIZE` | 同名 | `2` / `8` | 否 | 容量核算：`GUNICORN_MAX_WORKER × MAX_SIZE + celery 子进程数 × MAX_SIZE` 应小于 PG `max_connections` |

本地非 Docker 的三种连法（SQLite / 本机 PG / 本机 MySQL）见 `config_example.yml` 数据库段的注释块。

### 9.3 Redis

| 配置键 | 环境变量 | 默认值 | 必填 | 说明 |
|---|---|---|---|---|
| `REDIS_HOST` | 同名 | `redis`（compose 服务名） | 否 | 非 Docker 本地开发改 `127.0.0.1` |
| `REDIS_PORT` | 同名 | `6379` | 否 | |
| `REDIS_PASSWORD` | 同名 | 空（compose 兜底 `nineven`） | 生产必填 | |
| `DEFAULT_CACHE_ID` / `CHANNEL_LAYERS_CACHE_ID` / `CELERY_BROKER_CACHE_ID` | 同名 | `1` / `2` / `3` | 否 | 缓存 / WebSocket / broker 三库分离 |

### 9.4 服务与任务

| 配置键 | 环境变量 | 默认值 | 必填 | 说明 |
|---|---|---|---|---|
| `HTTP_BIND_HOST` / `HTTP_LISTEN_PORT` | 同名 | `0.0.0.0` / `8896` | 否 | |
| `GUNICORN_MAX_WORKER` | 同名 | `4` | 否 | API worker 数 |
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
| `METRICS_ENABLED` / `METRICS_TOKEN` | 同名 | `false` / 空 | 否 | 启用需同时配置 token（`Authorization: Bearer <token>`） |
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
| **运行期参数** | `SysConfig` 属性键：`FILE_UPLOAD_SIZE` / `PICTURE_UPLOAD_SIZE` / `PAT_RATE_LIMIT` / `CSP_MODE` / `CSP_REPORT_URI` / `OAUTH_PROVIDERS` / `SCIM_*` / `AUDIT_DIFF_MODELS` / 审批相关 / 文件预览与保留期 / 导入导出保留期 / `CHAT_HISTORY_DAYS` / `BACKUP_ALERT_TOKEN` / `OPS_ALERT_TOKEN` 等 | 管理页「系统管理 → 系统配置」修改后**即时生效**（保存即失效缓存）。这些键的默认值单源回读 `config.yml`（即上表值可作为初值），有 DB 行时以行值为准 |
| **用户级配置** | `WEB_SITE_CONFIG` / `PUSH_MESSAGE_NOTICE` / `PUSH_CHAT_MESSAGE` | 用户可在「账户设置」页覆盖个人值，即时生效 |

> 完整运行期参数清单与语义见 `loadjson/systemconfig.json`（种子初值）与
> [common/README.md](../../common/README.md)；用户级覆盖的读取链路见
> [architecture/cache.md](../architecture/cache.md)。
