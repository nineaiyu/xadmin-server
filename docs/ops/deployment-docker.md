# 部署与运维手册 · 容器部署与运行（deployment-docker）

> 本文为《部署与运维手册》子页：§2 Celery 队列划分 / §3 Docker 部署（备份恢复与容量）/
> §4 可观测性 / §7 国产化适配要点。
> 概览（本地开发与排查）见 [deployment.md](deployment.md)；升级与回滚见 [deployment-upgrade.md](deployment-upgrade.md)；
> 配置速查表见 [config-reference.md](config-reference.md)；安全响应头见 [deployment-csp.md](deployment-csp.md)。

## 2. Celery 队列划分

| 队列           | 承载内容                                           | worker                 |
|--------------|------------------------------------------------|------------------------|
| `celery`（默认） | 邮件/短信/站内信/周期清理等轻量任务                            | `start celery_default` |
| `heavy`      | 导入/导出/批量删除后台任务（`background_task_view_set_job`）、Office 转 PDF 预览（`convert_office_preview_task`） | `start celery_heavy`   |

- 路由配置：`server/settings/libs.py` 的 `CELERY_TASK_ROUTES`，新增重任务在此加一行即可。
- 健康检查：`ops/check_celery.sh [celery|heavy]`（依赖 worker 心跳文件，文件位于 `tempfile.gettempdir()`）。

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
# 开发/体验形态（源码 bind mount，改代码重启容器即生效）
docker compose up -d

# 生产形态（代码烘焙进镜像、非 root、无源码挂载；需 docker compose >= 2.24.4）
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

> 生产形态把应用数据目录 bind mount 到宿主 `./data`（与 db-backup 媒体备份同源），
> 首次启用需 `chown 1001:1001 ./data`；改业务代码后需重新 build 镜像。

- 密码策略：compose 不内置任何密码，`DB_PASSWORD` / `REDIS_PASSWORD` 未提供时直接拒绝启动
  （`${DB_PASSWORD:?}`）。`config.yml` 是唯一定义处：`dev_up.sh` / `dev_down.sh` 每次运行都会把同名键的值
  自动同步到 `.env`（脚本维护的派生缓存，供绕过脚本直接操作 compose 的命令使用），你只需编辑 `config.yml`。
  **生产部署必须**使用随机值，复用任何公开示例密码等于裸奔。
- **PostgreSQL 镜像要求（2026-10-02 起，ADR-074）**：`postgresql` / `db-backup` 使用
  **pgvector 变体镜像**（`registry.cn-beijing.aliyuncs.com/nineaiyu/pgvector:pg17`）——AI 知识库
  向量检索依赖 `vector` 扩展（ADR-074）；同 PG17 大版本，数据目录兼容，原地换镜像重启即可。
  外置数据库需自行安装 pgvector 扩展。**旧 `postgres:17` 镜像（无 vector 扩展）上禁止执行
  `ai/migrations/0005`**：扩展缺失时迁移的 DDL 会告警跳过、状态照登记，向量通道不可用且后续无法回填。
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

- 后端地址解析（2026-09-21 修复）：内置 nginx（stream）与页面层反代（`xadmin-web/xadmin-api-conf`）均以
  Docker 内嵌 DNS + 变量形式**运行期**解析 `server:8896`（`resolver 127.0.0.11 valid=10s ipv6=off`）——
  nginx 先于 server 启动不再 `[emerg] host not found` 启动失败，server 容器重建换 IP 后也**无需重启 nginx**
  （10s 内自动跟随）。排查步骤见 [runbook.md](runbook.md) §17。

### 3.1 数据库备份与恢复

> 2026-09-08 收口：异地副本、媒体目录、RPO 6h 三项已落地（下期规划 N1/L1），
> 详见 [backup-drill-2027-03.md](backup-drill-2027-03.md)。

- 备份：`db-backup` 服务每 **6 小时**（`BACKUP_INTERVAL`，原 24h）自动执行 `pg_dump | gzip`，产出
  `${VOLUME_DIR}/xadmin-db-backups/<库名>_<时间戳>.sql.gz`，滚动保留 7 天（`KEEP_DAYS`）。
  每个包附带 `.sha256` 校验和，落盘即做 `gzip -t` 完整性校验，损坏包不落正式名。
- 媒体目录：`BACKUP_MEDIA=true`（默认）时把 `./data/upload` 打包为同名 `.media.tar.gz` 一并备份。
- 异地副本：`BACKUP_REMOTE_TYPE` 支持 `local`（独立磁盘/NFS 挂载点）、`rsync`（远端主机）、`rclone`（云对象存储）；
  留空表示未启用。**生产必须指向与源库不同故障域的存储**，否则同盘故障仍会双丢。
  - **生产强制（`BACKUP_REMOTE_REQUIRED`）**：生产 overlay（`docker-compose.prod.yml`）默认置 `1` ——
    脚本在启动自检发现「异地副本未配置」时记 ERROR + 走备份告警通道上报，并在**单次模式**
    （`BACKUP_ONCE=1`，演练/外部 cron）以退出码 1 暴露给调度侧；**常驻循环仍照常做本地备份**
    （本地备份永远优先，不会因缺异地配置停备）。确无第二故障域可用的部署显式
    `BACKUP_REMOTE_REQUIRED=0` 关闭，并在发布清单留痕。基础栈（开发形态）默认 `0`，行为不变。
- **独立盘迁移（只改宿主路径，容器内路径不变）**：归档与异地副本各有一个宿主路径变量，
  迁移独立盘/NFS 时不需要改归档器、恢复脚本或 compose 结构：

```shell
# WAL 归档卷 → 第二块盘（新库/迁移场景先拷存量归档，再重建容器）
PITR_ARCHIVE_DIR=/mnt/pitr-archive docker compose up -d postgresql

# local 型异地副本 → 独立盘/NFS
BACKUP_REMOTE_DIR=/mnt/backup-remote docker compose up -d db-backup
```

  两者未设置时分别回落 `${VOLUME_DIR}/xadmin-postgresql/archive` 与
  `${VOLUME_DIR}/xadmin-db-backups-remote`；迁移日期回填 [pitr.md](pitr.md) §6。
- 手动/单次备份（脚本已支持单次模式，无需再手写 pg_dump）：

```shell
docker exec -e BACKUP_ONCE=1 xadmin-db-backup bash /ops/db_backup.sh
```

- 恢复（宿主机执行，会**清空重建**目标库，请先确认）：

```shell
sh ops/db_restore.sh ../xadmin-db-backups/xadmin_20260904_205752.sql.gz xadmin
# 演练/自动化：YES_I_KNOW=1 跳过交互确认；RESTORE_MEDIA=1 同时解包媒体目录
YES_I_KNOW=1 RESTORE_MEDIA=1 sh ops/db_restore.sh <备份包> xadmin_restore_test
```

- WAL 归档（PITR）：**2026-09-16 已启用**——`archive_mode=on` + `archive_timeout=60`
  （RPO 1 分钟），gzip 压缩归档至 `${VOLUME_DIR}/xadmin-postgresql/archive`；
  归档链路巡检（失败态 + 积压滞留）已并入 `db-backup` 每轮检查，
  时间点回放演练见 [pitr.md](pitr.md)。

- 一键演练（备份 → 异地校验 → 恢复验证库 → 逐表行数对比 → 输出报告，约 2s）：

```shell
bash ops/backup_drill.sh
BACKUP_REMOTE_DIR=../xadmin-db-backups-remote bash ops/backup_drill.sh   # 含异地副本校验
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
- [ ] 演练：bash ops/backup_drill.sh 全项 PASS（尤其「逐表行数一致」不得为 0 表）
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

### 3.2 受鉴权媒体与出站请求（2026-09-27）

媒体文件（`/media/`）统一经应用鉴权，不存在匿名直链：

- nginx 侧 `/media/` 转发后端（Django 校验 Cookie JWT 或 session，匿名 403）；
- 配置 `MEDIA_X_ACCEL_PREFIX: /_protected_media` 后，鉴权通过由 `X-Accel-Redirect`
  内转到 nginx 的 `internal` 位置零拷贝直出（`xadmin-web/default.conf` 已内置该位置）；
  未配置（默认空）时由应用进程输出文件——功能一致，仅性能差异；
- `DEBUG=true` 恒由应用输出（开发 / E2E 直连无 nginx）；
- 细粒度文件授权与访问审计仍在受鉴权 `download` / `preview` 端点。

出站请求（Webhook 投递、AI base_url）默认拒绝私网 / 环回 / link-local 目标（SSRF 防护）：

- IP 字面量与可解析域名在执行侧严格校验；Webhook 投递固定解析结果连接（防 DNS rebinding）；
- 内网 Webhook 接收端须在「系统管理 → 系统配置」登记 `OUTBOUND_ALLOWED_HOSTS`
  （逗号分隔域名 / IP），白名单是私网目标的唯一放行途径；
- AI 服务地址允许私网 / 环回（内网自建推理、本地联调），但拒绝云元数据与 link-local 地址。

### 3.3 Web 层容量规划：worker 数与 DB 连接（2026-10-01，容量立项）

默认 `GUNICORN_MAX_WORKER: 4` 面向低配 / 最小化部署；生产按核数与峰值负载在 `config.yml` 上调（改后按 §6.1 重启 web 容器生效）。

**worker 数是首要容量杠杆（ASGI 形态实测）**：同步执行段（同步中间件链 + DRF 同步视图）在单个 worker 进程内受 GIL 约束，单 worker 吞吐存在封顶——加并发只会拉长排队尾（P95 恶化、吞吐持平），扩 worker 才是线性扩容。固定环境实测（[performance-baseline.md §3.1](performance-baseline.md) 同款环境，元数据 fields 端点 20 VU）：

| worker 数 | 端点吞吐膝点 | 20 VU P95（目标 <60ms） | 出处 |
|---|---|---|---|
| 4（默认） | ~780 rps（≈195 rps/worker） | 117.62ms（排队尾，默认容量下不可达） | `docs/metrics-perf-history.md` 2026-10-01 深度定位 |
| 8 | 未触顶（20 VU 需求内） | **26.5ms 达标**、吞吐 1209 rps | 同上（单轮验证，详见容量立项） |

参照：columns 端点 4-worker 膝点 ~1100 rps、复杂列表（book）~2000+——接口越重膝点越低，容量按**最重高频接口**核算。完整证据链与达标杠杆见 [ASGI 同步段容量立项](../plans/ASGI同步段容量立项-2026.10.md)。

**容量指引**：

- worker 数起步：`峰值 RPS ÷ ~150 rps/worker`（保守值，按重接口口径），且不超过 CPU 核数（每 worker 一个事件循环 + 同步线程段，核不足时排队在 CPU）；上调后以监控（§4）复看 P95 与错误率；
- 内存：以部署后单 worker 进程实际 RSS 为准预留（容器 limit = worker 数 × RSS + 余量）；
- **DB 连接联动核算（必查）**：`GUNICORN_MAX_WORKER × DB_POOL_MAX_SIZE + celery 子进程数 × DB_POOL_MAX_SIZE < PG max_connections`。默认池 2-8：4 worker = 4×8+2×8=48，8 worker = 8×8+2×8=80，均低于 compose 默认 200；继续扩 worker 时同步核对（池配置见 §6.1 TD-25/ADR-006 注意项）；
- 低配边界（1-2 核 / 小内存 VPS）：维持默认 4，优先观察监控再动容量，不盲调。


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

### 4.4 上线冒烟（AI + 审批）

```bash
docker exec xadmin-server sh -c "cd /data/xadmin-server && python scripts/smoke_ai_approval.py"
docker exec xadmin-server sh -c "cd /data/xadmin-server && python scripts/smoke_ai_approval.py --user demo_staff --approver demo_lead --admin isummer"
```

走**运行库 + 真实 HTTP + 普通用户 JWT**（非测试库/超管 APIClient），覆盖单测视角看不到的
权限点命中、白名单语义与角色授权问题（历史缺陷：运行库权限点正则是旧版导致普通用户全 403，
单测全绿也发现不了）。检查项：AI（status/工具目录/文档问答/历史）、审批（发起 → 两级通过 → 终态）、
转交与管理视角（转交归属转移 → 管理视角可见 → 转交后通过）。退出码 0 = 全部通过。


## 7. 国产化适配要点

| 组件     | 说明                                                                                           |
|--------|----------------------------------------------------------------------------------------------|
| CPU 架构 | 镜像已多架构构建（linux/amd64 + linux/arm64），鲲鹏/飞腾等 ARM 环境直接拉取                                        |
| 操作系统   | 银河麒麟/统信 UOS 等可运行 arm64 容器环境直接使用；宿主机直装需 Python 3.14+ 与对应系统依赖（psycopg2/mysqlclient 编译链）        |
| 数据库    | 默认 PostgreSQL（openGauss 兼容 PG 协议，`DB_ENGINE: postgresql` 尝试接入）；人大金仓/达梦需替换 Django 后端驱动并回归迁移文件 |
| 中间件    | Redis 兼容版本即可（缓存/broker 用途，无特殊命令依赖）                                                           |
| 验证清单   | 迁移全量通过 → 登录/验证码/图片处理（Pillow/GeoIP 库）→ 导入导出（openpyxl）→ WebSocket → 定时任务                       |

> **老 ARM CPU 的 wheel 兼容**：`cryptography` 47.0+ 的 aarch64 manylinux wheel 使用了更激进的
> CPU 基线，在部分较老的 ARM 主机（含 2026-09 前后的 ARM 虚拟机）上 import 即触发
> `Illegal instruction (core dumped)`（现象：容器反复 Restarting (132)）。此类环境构建镜像前，
> 在服务器侧把 `pyproject.toml` 中的 `cryptography` 调整为 `==46.0.7`，并同步把
> `pyopenssl` 调整为 `==26.0.0`、`service-identity` 调整为 `==24.2.0`（三者对 cryptography
> 的版本约束互斥），随后执行 `uv lock` 刷新 `uv.lock`——容器构建以 `uv sync --locked` 安装依赖，
> 改 `requirements*.txt` 已不影响镜像内容。该适配仅作用于构建上下文，属服务器本地改动，不要提交回仓库。

> 国产化数据库替换涉及迁移文件与第三方库兼容性，属大变更：先建独立分支跑全量门禁（pytest + E2E），并登记 ADR 后再合入。

