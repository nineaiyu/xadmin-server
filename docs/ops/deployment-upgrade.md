# 部署与运维手册 · 升级与回滚（deployment-upgrade）

> 本文为《部署与运维手册》子页（§6 升级与回滚）。
> 概览（本地开发与排查）见 [deployment.md](deployment.md)；容器部署见 [deployment-docker.md](deployment-docker.md)。
> 存量库能否原地升级的判定与两条流程见 [upgrade-stock.md](upgrade-stock.md)。

## 6. 升级与回滚

> **跨版本升级先体检**：执行 `python manage.py upgrade_check` 判定库能否原地升级
> （旧版链路的库需清库重建，不适用本节流程）——判定口径、两条流程与常见问题见
> [upgrade-stock.md](upgrade-stock.md)。

### 6.1 升级流程

1. **备份先行**：确认最近一次 `db-backup` 产出完好（或手动 `pg_dump` 一次）；
2. **读变更说明**：Release Notes 中「升级注意」段落（破坏性迁移、新增必配项）；
3. **拉取新镜像/代码**：`docker compose pull`（或 `git pull` + 重建）；
4. **单实例迁移**：迁移只能执行一次（避免 DDL 互相锁）。生产形态推荐用一次性服务先迁移，
   成功后再起 web/worker：

   ```bash
   # 生产 overlay（代码烘焙进镜像）
   docker compose -f docker-compose.yml -f docker-compose.prod.yml build server
   docker compose -f docker-compose.yml -f docker-compose.prod.yml run --rm migrate
   docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
   ```

   > **prod overlay 的配置注入边界（2026-09-30 冒烟实测确认）**：生产形态 config.yml
   > 不进镜像（构建期清空），容器配置只认环境变量——而 compose 仅向应用容器透传
   > `DB_PASSWORD` / `REDIS_PASSWORD` / `DEBUG`。`SECRET_KEY`（生产必填）与
   > `ALLOWED_HOSTS`（DEBUG=false 时强制校验，缺失时连 healthcheck 都会 400）等
   > 其余键，需以临时 overlay 给应用容器补 `environment:` 声明并写入 `.env`
   > （列表型配置用 JSON 数组，如 `ALLOWED_HOSTS=["xadmin.example.com"]`，
   > env 值经 json.loads 解析）。docker compose run 单次动作可直接 `-e SECRET_KEY=...`。

   多副本 / 滚动发布必须同时给 web 侧配 `AUTO_MIGRATE=false`（web 容器不再在启动时迁移，
   由上述 `migrate` 服务承担）；单副本默认 `AUTO_MIGRATE=true`，行为与既有版本一致。
   源码挂载形态（开发 compose）亦可手动 `python manage.py migrate` 或
   `docker compose run --rm -e AUTO_MIGRATE=false server migrate`。
5. **滚动重启**：`docker compose up -d` 逐服务重建，观察 healthz 四项全 `true` 再继续；
6. **验证**：登录冒烟（登录 → 菜单加载 → 任一列表页 → 一次导入导出）。

> 多副本 / 横向扩展（迁移一次性、beat 单例、nginx 多后端轮询、扩缩容与回退）见
> [scale-out.md](scale-out.md)；**要求零停机**（发布期间不断连）改用
> [blue-green.md](blue-green.md) 的叠加滚动发布（前置：`stop_grace_period` + gunicorn `--graceful-timeout` + nginx `resolve`）。

> **2026-10-08（框架内核独立分发包）升级注意**：源码目录 `common/` 迁至
> `packages/xadmin-common/common/`（uv 工作区成员，分发名 `xadmin-common`；导入名仍是
> `common`，接口/迁移/命令/权限点均无变化）。升级动作按部署形态二选一：① **源码挂载形态**
> ——`entrypoint.sh` 已注入 `PYTHONPATH=/data/xadmin-server/packages/xadmin-common`，
> **重启容器即生效，无需重建镜像**（镜像 ENV `Dockerfile` / `Dockerfile-base` /
> `Dockerfile-dev` 同口径已同步）；② **镜像形态**——需重建应用镜像（旧的烘焙镜像里没有
> 新目录）。本地 uv 开发 `uv sync --all-groups` 后由 editable 安装提供 `common`；
> pip 路径需补 `pip install --no-deps -e ./packages/xadmin-common`（见 §1.1）。
> 细节与 settings 契约表见 [architecture/kernel-package.md](../architecture/kernel-package.md)；
> 内核包独立版本、私有源发版与宿主升级口径见 [kernel-release.md](kernel-release.md)。
> **内核包 0.2.0 起**：settings 读取统一走契约访问器（`kernel_setting` /
> `kernel_required_setting`），多数此前「必给」的键有了内核缺省——本工程（xadmin-server）
> 的配置项一个都不用改（相关键照常提供，行为不变），省略非必给键只对二开宿主生效。

> 涉及新增菜单/权限点或 gettext 文案的版本，升级后执行：
> `python manage.py post_upgrade`（= 内置种子 `load_init_json` + `compilemessages` + 配置缓存失效 + 权限点缺口扫描，
> 幂等可重跑；**安装器升级流程已自动调用**），随后重启容器——权限点未灌库时非超管角色不会出现新入口（接口 403），
> 文案未编译时中文界面回退英文。仅需补权限点时可改用 `sync_menu_permissions`（二者按版本说明择一）。

> **2026-09-27 升级注意**：① 媒体文件不再有匿名直链（`/media/` 经应用鉴权，见 §3.2），
> 前端需重新构建部署；② 菜单种子删除了已失效的「任务中心」菜单（`SystemTaskCenter`，
> 指向不存在的视图）——`load_init_json` 不删除既有数据，存量库如需清理请手工删除该菜单行
> （保留亦不影响，其原为停用状态）；③ 可选新配置 `MEDIA_X_ACCEL_PREFIX`（媒体零拷贝直出）
> 与 `OUTBOUND_ALLOWED_HOSTS`（出站白名单，内网 Webhook 接收端必配）。

> **2026-10-08 升级注意**：① 菜单种子把执行历史「取消/重跑」权限点改名
> `cancel/rerun:SystemTaskCenter` → `cancel/rerun:SystemTaskExecution`（pk 未变，
> `post_upgrade` 灌种子后存量角色授权自动延续，前后端需同批发布——过渡窗口内按钮暂隐）；
> ② 「水印设置」页签拆出独立权限点 `retrieve/partialUpdate:SettingWatermark`
> （与 `SettingBasic` 同端点，仅前端授权粒度拆分）——存量自定义角色升级后水印页签暂不可见，
> 需在角色管理显式勾选新权限点（SystemAdmin 自动获得，口径见
> [menu-maintenance.md §6](../guide/menu-maintenance.md)）；
> ③ 发送验证码端点移除了 **username 表单类型**（无投递通道，回显管线一并下线）——
> 登录/注册页的「用户名」验证码页签消失，账号密码登录/注册仍由 `/login/basic`
> 与邮件/短信验证码通道承载（注册至少需 `EMAIL_ENABLED` / `SMS_ENABLED` 其一）；
> 配置项 `SECURITY_REGISTER_BY_BASIC_ENABLED` 同步移除（存量库中的同名设置行不再被读取，
> 可留可删）。发送端仍接受手工缓存的 username 类 verify_token（兼容存量令牌，
> 登录分支照旧要求密码校验）。前端需重新构建部署；
> ④ **OAuth / OIDC 出站链路并入统一守卫**（`packages/xadmin-common/common/utils/outbound.py`，与 Webhook /
> AI base_url / MCP 同源）：provider 地址在写入侧改为 https 强制 + 地址归属校验
> （IP 字面量拒绝私网 / link-local / 元数据地址，`http://127.0.0.1` 与
> `http://localhost` 例外供本地联调），发送侧改为固定解析连接（私网 / 环回 /
> link-local 拒绝，白名单放行）——**内网自建 IdP（私网 IP 或仅内网可达域名）需在
> 「系统管理 → 系统配置」登记 `OUTBOUND_ALLOWED_HOSTS`，否则登录回调报「无法连接身份
> 提供方」；公网 IdP 无需任何配置**。已保存的私网 IP 字面量地址会被写入侧拒绝，
> 请改为域名或在白名单登记后重存。

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
- base 镜像由 `build-base-image.yml` 自动构建回写（触发路径：`uv.lock` / `pyproject.toml` /
  `requirements.txt` / `Dockerfile-base`；CI 提交新的 base tag 到 `Dockerfile`），基础层 CVE 修复通过重建 base 镜像消化。

