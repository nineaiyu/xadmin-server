# 升级常见问题（二开 / 运维 FAQ）

> 定位：面向**二次开发者与运维使用者**的升级常见问题，每条 = 问题 → 现象 / 原因 → 处置 → 相关文件 / 命令。
>
> 与相邻文档的分工（**先读它们，再回本文查「常问」**）：
>
> - 「我的库能不能原地升级 / 升级前体检 / 什么时候必须清库重建」——判定口径与两条流程的权威出处是
>   [ops/upgrade-stock.md](../ops/upgrade-stock.md)（含 `upgrade_check` 四种结论与处置表）；
> - 升级步骤、滚动重启、回滚操作见 [ops/deployment-upgrade.md §6](../ops/deployment-upgrade.md)；
> - 升级过程中遇到的**报错**（启动退出 / DB / Redis / 502 / 权限不生效等）见 [troubleshooting.md](troubleshooting.md)；
> - 升级后文案不翻译、按钮消失、媒体 404 等「升级特有」条目收录在本文。
>
> **一句话前提**：升级前先跑 `python manage.py upgrade_check`（只读，退出码 0 = 可继续 / 1 = 需人工介入）。
> 库不满足原地升级条件时，无论本文哪一条都不适用——按 [ops/upgrade-stock.md §五](../ops/upgrade-stock.md) 清库重建。

## 一、升级前

### 1. 我的库能直接 `migrate` 吗？

- **口径**：以 `upgrade_check` 的结论为准——`up-to-date`（无需迁移）/ `needs-migrate`（标准升级）
  可继续；`legacy-chain`（旧版链路）/ `schema-drift`（表结构漂移）需人工介入（通常清库重建）。
- **不要凭「库里有数据 / 之前升过」自行判断**：2026-10 经历过三轮清库重建式迁移整理，旧链库的迁移记录与当前代码链
  不再对应，直接 `migrate` 会造成状态错乱。
- **相关**：[ops/upgrade-stock.md](../ops/upgrade-stock.md) §二 / §三、`system/management/commands/upgrade_check.py`。

### 2. 能不能只升级前端，或只升级后端？

- **不能**：前后端版本必须一致（发布 tag 门禁强校验）；数据库能否升级由 §1 独立判定。
- **处置**：按发布说明整批升级；自定义前端改动请先合入你自己的分支再一起构建部署。

### 3. 升级要停机吗？

- **默认会有短暂影响**：compose 单副本滚动重建会断开在途连接（重启 3–10 秒内 502 / 断连属预期）；
  离线安装器升级是**停机迁移**。提前公告窗口与影响面。
- **要求零停机**：改用 [ops/blue-green.md](../ops/blue-green.md) 的叠加滚动发布（发布前记录旧 image id 供回滚）。
- **相关**：[ops/release-checklist.md](../ops/release-checklist.md) §0「发布窗口公告」。

## 二、升级执行

### 4. 生产形态下改 `config.yml` 怎么不生效？

- **原因**：生产镜像**不烘焙 `config.yml`**，容器配置只认环境变量，且 compose 只透传少数键
  （`DB_PASSWORD` / `REDIS_PASSWORD` / `DEBUG`）；`SECRET_KEY`（生产必填）、`ALLOWED_HOSTS` 等需在 overlay 的
  `environment:` 显式声明（列表 / 字典型用 JSON 串）。
- **处置**：把需要的键加进生产 overlay 的 `environment:`（或 `.env`），重建应用容器；
  `docker compose run` 单次动作可临时 `-e KEY=...`。
- **相关**：[ops/deployment-upgrade.md §6.1](../ops/deployment-upgrade.md)。

### 5. 多副本 / 横向扩展下的迁移注意什么？

- **迁移只跑一次**（避免 DDL 互相锁）：web 侧配 `AUTO_MIGRATE=false`，由一次性 `migrate` 服务承担；
  单副本默认 `AUTO_MIGRATE=true`，行为与既有版本一致。
- **beat 必须单例**；扩容与回退步骤见 [ops/scale-out.md](../ops/scale-out.md)。
- **相关**：[ops/deployment-upgrade.md §6.1](../ops/deployment-upgrade.md)。

### 6. 一次标准升级的完整动作序列

```bash
# 1) 备份先行（异地副本 + 媒体目录）
bash ops/db_backup.sh

# 2) 先体检（拉新版本后）
python manage.py upgrade_check          # 预期 needs-migrate

# 3) 单实例迁移（生产 overlay 形态：一次性 migrate 服务）
docker compose -f docker-compose.yml -f docker-compose.prod.yml build server
docker compose -f docker-compose.yml -f docker-compose.prod.yml run --rm migrate
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d

# 4) 升级后补全（种子 + 语言包 + 缓存失效 + 权限点缺口扫描，幂等）
python manage.py post_upgrade

# 5) 验证
python manage.py upgrade_check          # 预期 up-to-date
```

- **相关**：[ops/deployment-upgrade.md §6.1](../ops/deployment-upgrade.md)、`system/management/commands/post_upgrade.py`。

## 三、升级后「东西不见了 / 报错」

### 7. 我 fork 的前端产物 / 自定义页面去哪了？

- **事实**：平台后端升级**不会**改动你的前端仓库内容；前端是独立仓（`xadmin-client`），页面与产物由你自己的构建流程产出。
  升级后**前端需重新构建并部署**（部分版本会改菜单 / 权限点 / 接口，前后端不同批发布会缺入口）。
- **二开纪律**：不要直接改框架内核目录（`packages/xadmin-common/common/`、`system/` 等）——那是升级冲突的源头；
  业务功能放**独立 app** 并登记进 `config.yml` 的 `XADMIN_APPS`（见 [first-module-30min.md](first-module-30min.md)）。
- **相关**：[guide/plugin-development.md](plugin-development.md)、[ops/deployment-upgrade.md §6](../ops/deployment-upgrade.md)。

### 8. 升级后新菜单 / 新权限点 / 新按钮不显示

- **原因**：新权限点未入库（非超管 `hasAuth` 为假 → 入口直接不渲染）；或改完种子没重启进程。
- **处置**：`python manage.py post_upgrade`（= `load_init_json` + 语言包 + 缓存失效 + 权限扫描）→ 重启容器；
  仅需补权限点时可改用 `python manage.py sync_menu_permissions`（接口 403 的即时修复）。
- **相关**：[troubleshooting.md](troubleshooting.md) §12、`system/management/commands/load_init_json.py`。

### 9. 升级后中文界面变英文

- **原因**：`.mo` 语言包未编译（编译产物不入库）。
- **处置**：`python manage.py compilemessages`（`post_upgrade` 已包含）；容器启动时 `entrypoint.sh` 会按需自动编译，
  但 bind mount 目录无写权限时只告警 → 仍回退英文，需修目录属主。

### 10. 升级后个别按钮消失（权限点改名 / 页签拆权限点）

- **形态一（改名）**：权限点改名但 pk 未变时，`post_upgrade` 灌种子后**存量角色授权自动延续**；
  过渡窗口内（前后端未同批发布）按钮会短暂隐藏，属预期。
- **形态二（拆权限点）**：从宿主页面拆出的独立权限位（如「水印设置」页签的
  `retrieve/partialUpdate:SettingWatermark`）——**存量自定义角色升级后默认看不到该页签**，
  需在「系统管理 → 权限管理 → 角色管理」显式勾选（管理员角色自动获得）。
- **相关**：[guide/menu-maintenance.md](menu-maintenance.md) §6、[ops/deployment-upgrade.md §6.1](../ops/deployment-upgrade.md)。

### 11. 升级后媒体文件直链失效 / 图片 404

- **原因**：媒体文件改为**经应用鉴权**（`/media/` 不再匿名直链），并要求前端重新构建部署；
  生产若启用零拷贝直出，还需 nginx 声明同名 `internal` location。
- **处置**：确认前端已重新构建部署；生产按需配置 `MEDIA_X_ACCEL_PREFIX`（推荐 `/_protected_media`）
  与 nginx 的 `internal` location；`/media/` 兜底仍可用。
- **相关**：[ops/deployment-docker.md §3.2](../ops/deployment-docker.md)、[ops/storage.md](../ops/storage.md)。

### 12. 升级后 OAuth / OIDC / Webhook 报「无法连接身份提供方 / 出站被拒」

- **原因**：出站请求（OAuth / OIDC / Webhook / AI base_url / MCP）走统一守卫——私网 / 环回 / link-local 地址默认拒绝。
- **处置**：内网自建 IdP 或内网 Webhook 接收端，在「系统管理 → 系统配置」登记 `OUTBOUND_ALLOWED_HOSTS`；
  公网地址无需配置。已保存的**私网 IP 字面量**会被写入侧拒绝，请改为域名或登记后重存。
- **相关**：[ops/deployment-upgrade.md §6.1](../ops/deployment-upgrade.md)。

### 13. 升级后旧设置行还在，会不会有影响？

- **事实**：`load_init_json` **只新增 / 更新，不删除**既有数据。被废弃的设置行（如早前的
  `SECURITY_REGISTER_BY_BASIC_ENABLED`）不再被读取，**保留无影响，可留可删**；菜单同理
  （历史失效菜单保留不影响功能，需干净可手工删除该行）。
- **相关**：`system/management/commands/load_init_json.py`。

### 14. 框架内核改成独立分发包（`common/` 目录迁移）后怎么升级？

- **源码挂载形态**：`entrypoint.sh` 已注入 `PYTHONPATH=.../packages/xadmin-common`，**重启容器即生效，无需重建镜像**
  （导入名仍是 `common`，接口 / 迁移 / 命令 / 权限点均无变化）。
- **镜像形态**：旧烘焙镜像里没有新目录，需**重建应用镜像**。
- **本地 uv 开发**：`uv sync --all-groups` 后由 editable 安装提供 `common`。
- **相关**：[architecture/kernel-package.md](../architecture/kernel-package.md)、[ops/kernel-release.md](../ops/kernel-release.md)。

### 15. 升级前要不要先读 Release Notes 的「升级注意」段落？

- **要**：破坏性变更（媒体鉴权 / 接口签名 / 权限点改名 / 出站守卫）与**新增必配项**都写在里面，
  不看会在升级后才发现功能异常。[ops/deployment-upgrade.md §6.1](../ops/deployment-upgrade.md) 按版本逐条沉淀了这些「升级注意」，
  可作历史参照。

### 16. 升级后前端菜单指向的页面 404

- **原因**：菜单 `component`（组件目录）或 URL 与前端实际的页面结构不一致——URL 与组件目录可能**独立演化**
  （历史口径），版本演进中新页面若不按规范登记就会漂移。
- **处置**：按 [guide/menu-maintenance.md](menu-maintenance.md) 的基本口径核对；确需偏离时在前端仓登记为例外
  （双向对账门禁会拦未登记项）。

### 17. 升级后新增的定时任务没生效 / 周期任务列表里没有它

- **原因**：周期任务随 app 代码注册（`register_as_period_task`，随 app autodiscover 自动注册）；
  **beat 进程不热加载**，且所属 app 若被模块裁剪掉也不会注册。
- **处置**：重启 `celery-beat`（以及 worker）；确认任务所在 app 已登记进 `XADMIN_APPS` 且未被 `MODULE_*` 裁掉。

### 18. 升级后报数据库连接数打满（too many connections）

- **原因**：PostgreSQL 下默认启用 psycopg3 服务端连接池，容量需满足
  `GUNICORN_MAX_WORKER × DB_POOL_MAX_SIZE + celery 子进程数 × DB_POOL_MAX_SIZE < PG max_connections`。
- **处置**：按公式核算并调小 `DB_POOL_MAX_SIZE` 或 worker 数，或调大 PG `max_connections`；
  回退旧行为可设 `DB_POOL: false`（仅 `DB_ENGINE=postgresql` 生效）。
- **相关**：[ops/config-reference.md §9.2](../ops/config-reference.md)、[ops/deployment-docker.md §3.3](../ops/deployment-docker.md)。

## 四、回滚

### 19. 升级失败怎么回滚？

- **镜像回滚**：把镜像 tag 固定到上一版本 `up -d`；
- **数据库回滚**：Django 迁移**原则上不做反向回滚**——先把服务回到旧版本运行，数据问题走备份恢复
  （清空重建，RTO ≤ 30 分钟）；仅当上一版本明确依赖旧表结构时，才评估 `migrate <app> <旧迁移号>`；
- **止损顺序**：服务回滚 → 确认 healthz 四项全 `true` → 数据恢复（最后手段）。
- **相关**：[ops/deployment-upgrade.md §6.2](../ops/deployment-upgrade.md)、[ops/runbook.md §12](../ops/runbook.md)。

### 20. `migrate` 报 `Table ... already exists` 或卡住

- **报「已存在」**：库不是当前链（或上次迁移中途失败）。按 [ops/upgrade-stock.md](../ops/upgrade-stock.md) §七
  的处置（`upgrade_check` 判定 `legacy-chain` / `schema-drift` → 清库重建；`needs-migrate` → 查并发迁移实例）。
- **卡住**：多为 PG 锁等待（`select * from pg_stat_activity where wait_event is not null`）；杀掉挂起的 DDL 会话后重试。
- **相关**：[ops/runbook.md §13](../ops/runbook.md)。

## 五、升级与二开边界

### 21. 平台升级会覆盖我改过的框架代码吗？

- **事实**：升级以「拉取 / 替换发布版本」为准——直接改框架内核目录（`packages/xadmin-common/common/`、`system/` 等）
  的改动会在下次升级冲突或丢失。
- **处置**：二开一律放**独立 app** 或自己的前端页面目录，通过配置与扩展点接入
  （`config.py::URLPATTERNS` / `APPROVAL_BIZ_SYNCERS`、`register_contract` / entry points 等），不改内核。
- **相关**：[guide/plugin-development.md](plugin-development.md)、[guide/first-module-30min.md](first-module-30min.md)、
  [troubleshooting.md](troubleshooting.md) §6。

## 六、升级后验收清单

| 步骤 | 命令 / 入口 | 期望 |
|------|-------------|------|
| 迁移状态 | `python manage.py upgrade_check` | `up-to-date` |
| 权限点覆盖 | `python manage.py post_upgrade`（末步扫描） | 「权限点完整覆盖」 |
| 环境体检 | `python manage.py doctor` | 无缺口 |
| 健康检查 | `GET /api/common/api/health` | 四项全 `true` |
| 登录冒烟 | 登录 → 菜单加载 → 任一列表页 | 无 401 循环、无空白 |
| 关键业务抽查 | 一次导入导出 / 表单提交 / 审批 | 正常流转 |

> 发布窗口逐项核对的完整清单见 [ops/release-checklist.md](../ops/release-checklist.md) §0；
> 升级过程中遇到具体报错，回 [troubleshooting.md](troubleshooting.md) 按现象定位。
