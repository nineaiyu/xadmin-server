# 4.x → 5.x 存量升级操作手册（清库重建 + 数据回灌）

> **一句话**：5.0 起按域切分了表名（用户 / 文件 / 日志 / 任务分别落到 `identity_*` /
> `file_*` / `audit_*` / `task_*`），**四域切分之前**的库（表名仍是 `system_userinfo` 等）
> 无法原地升级。官方路径是：**备份 → 清库重建 → `legacy_export` / `legacy_import` 回灌 → 验收**。
>
> 判定方式：`python manage.py upgrade_check` 结论为 `legacy-chain-pre-split`（详见
> [upgrade-stock.md](upgrade-stock.md) §二）。等价链（四域切分之后）的库请走
> [upgrade-stock.md](upgrade-stock.md) §四 的账本认领流程，不需要本文。

## 一、适用范围

| 场景 | 是否适用本文 |
|------|--------------|
| 4.2.5 及更早（`system_userinfo` 等旧表名）升级到 5.x | ✅ 按本文执行 |
| 四域切分之后、5.0 之前的 4.x 库 | ❌ 走 [upgrade-5.0.md](upgrade-5.0.md)（账本认领） |
| 5.x 之间的升级 | ❌ 走 [deployment-upgrade.md](deployment-upgrade.md) §6 |

**代价先说清**：库是**重建**的——自增主键、UUID、`created_time` 等由新库重新生成；
业务语义数据（组织、账号、权限、配置、附件、公告）按自然键搬过去。日志、任务历史、
导出记录不搬（见 §八）。

## 二、升级前准备

1. **停机**：`./xadmin.sh stop`（安装器形态）或 `docker compose stop server celery-worker
   celery-heavy celery-beat`（compose 形态）。
2. **备份三件套**（回滚唯一退路）：

   ```bash
   # ① 数据库
   pg_dump -U <DB_USER> -d <DB_DATABASE> | gzip > backup-$(date +%F_%H%M).sql.gz
   # ② 媒体与数据目录（附件物理文件在里面）
   tar czf data-$(date +%F_%H%M).tar.gz -C <VOLUME_DIR>/server data
   # ③ 配置文件（SECRET_KEY / 数据库口令）
   cp <CONFIG_FILE> config-backup-$(date +%F_%H%M).conf
   ```

3. **确认判定**（在旧库上执行）：`python manage.py upgrade_check`
   - `legacy-chain-pre-split` → 继续本文；
   - 其他结论 → 按 `upgrade-stock.md` 对应处置。

## 三、导出（在旧库环境执行）

导出走**独立脚本** `ops/legacy_export.py`（只依赖 `psycopg` / `psycopg2`，不依赖旧版本代码
里有没有这条命令）：把新版本仓库里的该文件拷到旧库容器或任意能连库的机器上执行。

```bash
python ops/legacy_export.py \
    --dsn "postgresql://<DB_USER>:<DB_PASSWORD>@<DB_HOST>:5432/<DB_DATABASE>" \
    --output /tmp/xadmin-legacy \
    --media-root <VOLUME_DIR>/server/data/upload      # 附件根目录（容器内一般 /data/xadmin-server/data/upload）
```

- `--include users,depts`：只导出部分实体（默认全部）；
- 产物：`export.json`（业务数据）+ `files/`（附件与用户头像等物理文件，按原存储路径组织）；
- 末尾会打印每个实体的行数与附件拷贝/缺失数量。**附件缺失只导出记录**（导入侧会跳过该记录）。

> 同版本自检可以用 `python manage.py legacy_export --output <dir>`（走模型、按当前结构导出），
> 真实升级请用上面的独立脚本。

## 四、清库重建（新库）

```bash
# 1) 建全新库（示例：PostgreSQL）
psql -U <DB_USER> -c "drop database if exists <DB_DATABASE>;" -c "create database <DB_DATABASE>;"

# 2) 迁移
python manage.py migrate

# 3) 先建超级管理员，再灌种子（种子文件引用了 creator_id=1，空库直接灌会外键失败）
python manage.py shell -c "from identity.models import UserInfo; u,_=UserInfo.objects.get_or_create(username='xadmin'); u.is_superuser=True; u.is_staff=True; u.set_password('<新密码>'); u.save()"
python manage.py load_init_json            # 菜单 / 权限点 / 内置角色 / 字典 / 系统配置
python manage.py post_upgrade              # 幂等补全（种子收尾 / 语言包 / 缓存）
```

容器形态把上述命令换成 `docker exec -i xadmin-server python manage.py <命令>`。

> **PG 大版本**：4.x 的 PG 数据目录是 16，5.0 起镜像是 `pgvector:pg17`，需先按
> [deployment-upgrade.md](deployment-upgrade.md) 的「PostgreSQL 大版本」段用
> `pg_dump` / `pg_restore` 跨版本迁移数据目录（安装器升级流程已内置该步骤）。

## 五、回灌（在新库执行）

```bash
python manage.py legacy_import --input /tmp/xadmin-legacy --dry-run   # 先空跑核对
python manage.py legacy_import --input /tmp/xadmin-legacy
```

- **幂等**：按自然键 upsert，重复执行只更新不重复建；同名记录若在回收站（软删）会被恢复；
- **引用缺失会告警不中断**：例如旧库角色绑定了新库种子中已不存在的权限点，会按
  `roles.menu=<权限码>: N` 列出（多为跨版本权限点调整），核对后可忽略；
- **单实体失败只跳过该实体**（其余实体与收尾步骤照常执行），错误在末尾统一列出；
- `--include` 可只导入部分实体。

## 六、验收清单

| # | 检查项 | 命令 / 位置 | 期望 |
|---|--------|-------------|------|
| 1 | 迁移与账本 | `python manage.py upgrade_check` | `up-to-date`（无待应用 / 无游离记录） |
| 2 | 权限点缺口 | `python manage.py sync_menu_permissions --dry-run` | 缺口 0 |
| 3 | 组织与账号 | 用户 / 部门 / 角色列表页 | 层级、数量、禁用状态与旧库一致 |
| 4 | 角色授权 | 角色管理 → 菜单授权 | 授权项数量与旧库一致 |
| 5 | 数据权限 | 用户 / 部门的「数据权限」 | 规则（rules）内容一致 |
| 6 | 系统配置 | 系统管理 → 系统配置 | 自定义配置键值一致 |
| 7 | 个人配置 | 对应账号登录后看个人偏好 | 与旧库一致 |
| 8 | 附件 | 文件管理 + 下载 | 记录与物理文件可下载（缺失文件已在导入时告警） |
| 9 | 公告 | 我的通知 | 公告正文、目标（用户/部门/角色）、已读状态、附件一致 |
| 10 | 登录 | 用**旧密码**登录 | 成功（密码哈希原样搬运） |
| 11 | 健康检查 | `GET /api/common/api/health` | `status` / `db_status` / `redis_status` / `storage_status` 为 `true`（未起 worker 时 `celery_status` 为 false 属正常；首次请求较慢，超时给足 10s） |
| 12 | 冒烟 | 登录 → 菜单 → 列表页 → 导入导出 | 无 403/404/500 |

## 七、回滚

1. 停服；
2. 恢复数据库：`psql -U <DB_USER> -d <DB_DATABASE> < backup-*.sql.gz`（或安装器 `restore_db`）；
3. 恢复媒体目录与配置（`tar xzf data-*.tar.gz` / `cp config-backup-*.conf <CONFIG_FILE>`）；
4. 镜像 / 版本回退到升级前，起服验证。

## 八、搬运范围

| 搬运 | 不搬运 |
|------|--------|
| 部门（含层级 / 停用 / 部门角色与数据权限） | 登录日志、操作日志 |
| 角色（含停用）与其菜单授权 | 导出记录、任务执行历史 |
| 用户（含密码哈希、状态、加入时间、超管标记、多角色、数据权限、头像） | 临时文件（`is_tmp`）与上传分片 |
| 数据权限（rules JSON）、系统配置（JSON 与标量）、用户个人配置 | 会话 / 令牌 / 缓存 |
| 系统设置（`Setting`）；**加密项只搬元信息**，取值需重新填写 | 菜单与权限点（由 `load_init_json` 种子提供） |
| 字段权限（角色 × 菜单 × 字段树）、用户 / 系统消息订阅 | 性能监控、验证码等临时数据 |
| 附件记录与物理文件 | 二开 app 的表默认不搬（用 `--extra-tables auto` 整表搬运，见文末附录） |
| 公告（正文 / 目标用户·部门·角色 / 附件 / 已读状态） | |

## 九、实测记录（2026-10-17，虚拟机 CentOS 7 / Docker 24）

| 项 | 结果 |
|----|------|
| 旧库规模 | 10 部门（含停用）/ 7 角色（含停用）/ 11 用户（含禁用与超管）/ 13 数据权限 / 8 系统配置 / 2 个人配置 / 4 附件 / 3 公告 / 5 条日志 |
| 导出 | `export.json` + 附件 2 个（另 2 条记录物理文件缺失，按设计只导出记录） |
| 重建 + 回灌 | 用户 10 新建、部门 6 新建、角色 3 新建、个人配置 2、公告 3、公告附件关联与已读齐全 |
| 关联校验 | 用户→部门→角色→数据权限逐条一致（含多层部门 `D111→D110→D100`、双角色、禁用角色）；角色菜单授权 14 / 5 / 2 与旧库一致 |
| 登录 | 用旧库密码（PBKDF2 哈希）在新库登录成功 |
| 幂等 | 复跑全部为「更新」，无重复行 |
| 账本 | `upgrade_check` = `up-to-date`（86 迁移 / 0 待应用 / 0 游离）；`sync_menu_permissions --dry-run` 缺口 0 |
| 服务冒烟 | 5.0 应用（gunicorn 4 worker）连重建库启动，`/api/common/api/health`：`status` / `db_status` / `redis_status` / `storage_status` 全 `true`（`celery_status=false`，未起 worker） |
| 引用缺失 | 16 处：附件物理文件缺失（旧库文件已丢失）+ 旧库角色绑定的权限点在新库已调整（预期，核对后忽略） |
| 补齐说明 | 本轮并补「系统设置 / 字段权限 / 用户·系统消息订阅」三类实体的搬运（含加密设置的特殊处置），守护测试增至 20 例 |

## 附：做过二次开发（自定义 app / 自定义字段）的升级

框架自带的数据按前文回灌即可；**二开部分**按下表处理：

| 你的改动 | 处理方式 |
|----------|----------|
| 自己写的 app（自带模型与迁移） | 导出时加 `--extra-tables auto`（自动识别非框架表），导入时**整表原样写回**（保留主键） |
| 指向框架对象的外键（`user_id` / `dept_id` / `role_id` 等） | **无需处理**：回灌沿用旧库主键（`UserInfo` 自增、部门/角色 UUID），外键值与外部系统保存的 ID 都保持有效 |
| 在框架模型上新增的字段 | 需要你自己的迁移处理：5.0 把 `system` 域拆成了 `identity` / `file` / `audit` / `task`，原迁移里的 `app_label` 与表名要一起改（如 `system.UserInfo` → `identity.UserInfo`、`system_userinfo` → `identity_userinfo`） |
| 二开表里的文件字段 | 当前工具只搬框架附件（`UploadFile`）的物理文件；二开表的文件请手工同步媒体目录 |
| 改了框架自带的 app 名 / 表名 | 属于深度定制：表名对不上时导入会**告警跳过**（不会写错数据），需要你自行扩展映射 |

用法（导出侧）：

```bash
python ops/legacy_export.py --dsn "postgresql://..." --output /tmp/xadmin-legacy \
    --media-root <VOLUME_DIR>/server/data/upload --extra-tables auto
```

> `--extra-tables auto` 会列出识别到的二开表；也可以显式写表名（逗号分隔）。
> 导入侧（`manage.py legacy_import`）自动处理 `export.json` 里的 `extra_tables` 段：
> 目标库存在同名表就整表写回（主键相同的行跳过），不存在则告警跳过。
>
> 回灌结束会**自动重置自增序列**（按原值写入主键后必须把序列推到最大值，否则新建记录会撞主键）。


## 十、常见问题

| 现象 | 原因与处置 |
|------|-----------|
| `upgrade_check` 报 `legacy-chain-pre-split` | 预期：库停留在四域切分之前，按本文升级 |
| 导入后提示 `files.missing=...` | 旧库里该附件的物理文件已丢失（记录存在、文件不在），已跳过该记录；不影响其他数据 |
| 导入后提示 `roles.menu=xxx: N` | 旧库角色绑定的权限点在新库种子中已调整/移除，属跨版本正常差异；如需保留请在角色管理重新勾选 |
| 用旧密码登录失败 | 确认导出包含 `users` 实体且回灌时未手工改过密码；哈希原样搬运，PBKDF2 / argon2 都支持 |
| 回灌提示「加密设置 N 项未搬运取值」 | 密文由旧库 `SECRET_KEY` 签发，换库后解密失败属预期：记录已建（含分类/开关），取值请在设置页重新填写 |
| 回灌后数据都在，但 ID / 时间戳变了 | 预期：库是重建的，主键与时间戳由新库生成；请按业务自然键（用户名 / 部门编码 / 角色编码）核对 |
| 想要「真·原地升级」（保留原 UUID 与时间戳） | 需要额外的跨版本数据搬迁迁移（表重命名 + 补列 + 数据映射），属独立立项，当前未提供 |
| 附件在旧库是远程存储（S3 等） | `--media-root` 会失效：先按 [storage.md](storage.md) 把文件取回本地目录，或导出后手工补齐 `files/` 目录 |
