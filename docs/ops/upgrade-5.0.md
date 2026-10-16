# 升级到 5.0（4.x → 5.0 升级指南）

> **一句话结论**：**四域切分之后**的 4.x 库（表名为 `identity_userinfo` / `file_uploadfile` /
> `audit_operationlog` 等）**可直接原地升级**——新版本先由
> `python manage.py upgrade_ledger --apply` 认领迁移账本（不执行 DDL），再
> `python manage.py migrate`，最后 `python manage.py post_upgrade` 补全种子/文案/缓存。
> **四域切分之前**的库（表名仍是 `system_userinfo` 等）按 [upgrade-stock.md §五](upgrade-stock.md)
> 清库重建并用 `legacy_export` / `legacy_import` 回灌——逐步命令与验收清单见
> [upgrade-4x-to-5x.md](upgrade-4x-to-5x.md)（`upgrade_check` 会判为 `legacy-chain-pre-split`
> 并拦下，不要按版本号自行判断）。
>
> 判据一次说清：**升级前先跑** `python manage.py upgrade_check`，按它的结论行事即可。

## 一、5.0 的关键变化

| 变化 | 说明 | 对升级的影响 |
|------|------|--------------|
| **迁移链合并** | 全部业务 app 的分散迁移收敛为初始迁移（每 app `0001_initial`，跨 app 循环依赖处为 `0001_initial` + `0002_initial`），**与四域切分后的 4.x 链逐行等价**（`pg_dump --schema-only` 归一化比对）；四域切分之前（`system_*` 表名）的库不等价 | 等价链的库结构无需变更、认领账本即可；切分前的库需清库重建 + 回灌 |
| **迁移账本认领** | 新增 `manage.py upgrade_ledger`（`--apply` 认领）+ `upgrade_check` 新增 `legacy-chain-reconcilable` 判定 | 4.2.5 库升级只需一条命令，不再需要清库重建 |
| **演示模式** | 演示账号 `admin/admin123` 挂「演示模式」角色（破坏性写操作已裁剪）；安装器支持 `WITH_DEMO_DATA=1` 一键灌演示数据 | 体验/演示环境用；生产环境不启用 |
| **版本号口径** | 平台版本 `server/const.py` 与 `xadmin-client/package.json` 同步为 `5.0.0`（发布 tag 门禁三方校验） | 前后端必须同批发布 |
| 其余功能变更 | 见 [CHANGELOG.md](../../CHANGELOG.md) 与各 ADR | 升级后执行 `post_upgrade` 即可（幂等） |

> **为什么可以直接认领**：5.0 的合并链只改变「迁移文件的组织方式」，不改变表结构。
> 认领命令在写入前会做两件校验：① 游离迁移记录全部命中 4.2.5 链白名单
> （`system/utils/platform/migration_ledger.py` 的 `LEGACY_4X_CHAIN`）；
> ② 表结构体检无缺表/缺列（按模型逐列比对）。任一不满足即拒绝写入（退出码 1）。

## 二、升级前检查清单

```bash
# 1. 备份（升级唯一退路）
bash ops/db_backup.sh          # 或安装器：./xadmin.sh backup_db

# 2. 体检：确认当前库的判定
docker exec -i xadmin-server python manage.py upgrade_check        # 旧版本执行也可
#   或本机源码形态：python manage.py upgrade_check

# 3. 记录当前版本号，便于回滚
docker exec -i xadmin-server python manage.py shell -c "from server.const import VERSION; print(VERSION)"
```

| `upgrade_check` 结论 | 含义 | 下一步 |
|----------------------|------|--------|
| `up-to-date` | 已是当前链 | 直接升级（`migrate` 无动作） |
| `needs-migrate` | 有未应用迁移 | 常规 `migrate` |
| `legacy-chain-reconcilable` | 四域切分后的 4.x 链的库，账本待认领 | 按第三节走（**本指南主场景**） |
| `legacy-chain-pre-split` | 四域切分之前的库（旧表名 `system_userinfo` 等） | 清库重建 + 回灌（[upgrade-stock.md](upgrade-stock.md) §五、§七） |
| `schema-drift` / `legacy-chain` | 结构漂移 / 更早版本链路 | 按 [upgrade-stock.md §五](upgrade-stock.md) 清库重建 |

## 三、升级步骤

### 3.1 安装器（推荐）

```bash
cd /path/to/xadmin-installer          # 安装器脚本目录
./xadmin.sh upgrade v5.0.0
```

安装器会自动完成：升级前体检 → **迁移账本认领** → 备份数据库与配置 → `migrate` →
`post_upgrade`（账本认领 / API 前缀平移 / 种子导入 / 语言包编译 / 缓存失效）→ 清理旧镜像。
升级基线校验：当前版本需 ≥ `v4.2.5`，更早版本会直接中止并提示清库重建路径。

### 3.2 docker compose（手工）

```bash
# 1. 拉取/加载 5.0 镜像（或更新源码后重建）
docker compose pull

# 2. 停服（避免迁移期间写入）
docker compose stop server celery-worker celery-heavy celery-beat

# 3. 认领迁移账本（幂等：全新库与已认领库输出 consistent，不做任何写入）
docker compose run --rm server python manage.py upgrade_ledger --apply

# 4. 执行迁移（预期 No migrations to apply；如有待应用迁移即真迁移）
docker compose run --rm server python manage.py migrate

# 5. 升级后补全（种子 / 语言包 / API 前缀平移 / 缓存）
docker compose run --rm server python manage.py post_upgrade

# 6. 起服并验证
docker compose up -d
```

### 3.3 本机源码形态

```bash
source .venv/bin/activate
python manage.py upgrade_ledger --apply   # 1. 认领账本
python manage.py migrate                  # 2. 迁移
python manage.py post_upgrade             # 3. 补全种子/文案/缓存
```

> 前端需同步发布 5.0 构建产物（`pnpm build` 后部署 `dist`）；前后端版本必须一致。

## 四、验证清单

| 检查项 | 命令 / 位置 | 期望 |
|--------|-------------|------|
| 迁移对账 | `python manage.py upgrade_check` | `up-to-date` |
| 账本对账 | `python manage.py upgrade_ledger` | `consistent` |
| 权限点完整性 | `python manage.py sync_menu_permissions --dry-run` | `缺口 0 条` |
| 健康检查 | `GET /api/common/api/health` | 各项 `true` |
| 登录冒烟 | 浏览器登录 → 菜单加载 → 任一列表页 → 一次导入导出 | 无 403/404/500 |

## 五、回滚

1. 停服：`docker compose stop`（或 `./xadmin.sh stop`）；
2. 恢复数据库：`bash ops/db_restore.sh <备份文件>`（安装器：`./xadmin.sh restore_db <文件>`）；
3. 镜像/版本回退：把 compose 的镜像 tag 固定回 4.2.5（或 `static.env` 回退版本后重新加载镜像）；
4. 起服验证。

> 认领只写 `django_migrations` 表（不执行 DDL），因此**数据结构层面没有需要回滚的变更**；
> 回滚的实质是「回到旧版本代码 + 旧数据」。详见 [deployment-upgrade.md §6.2](deployment-upgrade.md)。

## 六、常见问题

| 现象 | 原因与处置 |
|------|-----------|
| `migrate` 输出 `No migrations to apply` | 正常：等价链（四域切分后的 4.x）→ 5.0 表结构等价，认领后无需 DDL |
| `upgrade_ledger --apply` 报 `unsupported` | 存在白名单之外的游离记录或表结构缺表/缺列：按 [upgrade-stock.md §五](upgrade-stock.md) 清库重建 |
| 认领后仍提示有 `pending` 迁移 | 先 `upgrade_check` 看是否 `schema-drift`；漂移库不要强行认领 |
| 升级后新入口 403 / 菜单缺失 | `python manage.py post_upgrade`（灌种子 + 清缓存）后重启后端 |
| 中文界面回退英文 | `python manage.py compilemessages`（`post_upgrade` 已包含） |
| 升级后用户/文件/日志/任务接口 404 | 存量库未做 API 前缀平移：`python manage.py migrate_api_prefixes --apply`（`post_upgrade` 已包含） |
| `upgrade_check` 报 `legacy-chain-pre-split` | 库停留在四域切分之前（`system_userinfo` 等旧表名），不支持原地升级：备份 → 清库重建 → `legacy_export` / `legacy_import` 回灌（[upgrade-stock.md](upgrade-stock.md) §五、§七） |
| 升级后 PG 容器反复重启、日志报 `database files are incompatible` | 4.x 的 PG 数据目录是 16，而 5.0 起镜像为 `pgvector:pg17`（大版本不兼容）：先用旧镜像 `pg_dump`，再用 pg17 初始化新目录并 `pg_restore`（见 [deployment-upgrade.md](deployment-upgrade.md) 的「PostgreSQL 大版本」段） |
| 想验证「没漏东西」 | `upgrade_check` = `up-to-date` 且 `sync_menu_permissions --dry-run` 缺口 0，即为完整 |
