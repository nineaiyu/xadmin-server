# xadmin 5.0.0 发布清单（工作区级台账）

> 本文件是**本次发版的执行清单与验证台账**；2026-10-14 由工作区根迁入
> `xadmin-server/docs/plans/`，与工作区级台账同区、纳入版本保存。
> 逐项证据在「验证状态」一节；未完成项显式标注，不粉饰。
>
> 状态行：2026-10-14 全量复核通过（发布前最后一批验证）；**五仓提交已完成并推送（工作区同步 0 未推送），
> `v5.0.0` tag 待打**。tag 前另有同日批次：演示管理员字段权限完善 + 全面安全排查（见 `DEV-TASKS.md` 状态快照）。

## 一、发布结论

**可以发布**——五个仓库的全部改动已冻结，本日两次全量后端回归（其一为发布前复核）
均 `exit=0`，全量门禁与全量 E2E 通过；E2E 的 3 个用例在 4 路并行负载下失败，
**已逐个隔离复跑证伪**（`6 passed` + `1 passed`，均为并行负载瞬态，非回归）。

发布要点：

- 5.0 是**大版本**：迁移链收敛为初始迁移 + 存量库**账本认领**（4.2.5 起可原地升级，更早的库清库重建）；
- 平台版本号三方一致 `5.0.0`（`server/const.py` ↔ `package.json` ↔ 发布 tag）；
- 五个仓库同批发布（server / client / installer / web / docs），web 仓补齐 AGENTS.md 后五仓规范齐全。

## 二、本次改动清单（工作区当前状态）

| 仓库 | 改动 | 关键内容 |
|------|------|----------|
| `xadmin-server` | 156 文件 vs HEAD（发布批次 66 + 叙事/编号清理批 ~90） | 迁移链合并（14 app → 初始迁移 + 7 个 `0002_initial`）；`upgrade_ledger` 命令 + `upgrade_check` 新判定 + `post_upgrade` 接线；安装器形态的账本认领；种子重生成（权限点 0 缺口、模型字段树 +157、清软删历史行、删含口令哈希的孤儿种子）；`CHANGELOG.md`；`docs/ops/upgrade-5.0.md`；`ADR-093`；README 补 5.0 版本横幅；演示模式完善（`init_data --with-demo` 输出演示账号）；`AGENTS.md` 重写；**对标叙事与台账编号清理**（代码/测试/脚本/CI/种子注释中的对标归属与任务编号全部去除，`docs/plans` 对标任务书移入工作区回收站） |
| `xadmin-client` | 146 文件 vs HEAD（发布批次 7 + 叙事/编号清理批 ~139） | 版本号 5.0.0（`package.json` / `public/platform-config.json`）；路径口径（`ops/`）；`AGENTS.md` 重写；发布说明配置；**对标叙事与台账编号清理**（源码注释 / 样式 / 组件 / E2E 注释 / 仓内文档统一去除对标归属与任务编号，保留全部语义与数值） |
| `xadmin-installer` | 4 文件 | 升级基线 `v3.10.11` → `v4.2.5`；账本认领接入升级链路；`WITH_DEMO_DATA=1` 演示模式；`ops/init_data.py` 路径修正（**原 `utils/init_data.py` 不存在，安装到此步必失败**）；新增 `AGENTS.md` |
| `xadmin-web` | 2 文件 | 发布说明配置（升级提示）；**新增 `AGENTS.md`**（CSP 三处同源 / 安全头继承 / 运行期反代解析等红线） |
| `xadmin-docs` | 10 文件 | 升级页重写为 5.0 口径（账本认领 + 升级要点）；`ops/` 路径口径；新增 `AGENTS.md` |
| 工作区（工作区根 + 本仓 `docs/plans/`） | 6 项 | `README.md`（工作区总览）与 `AGENTS.md`（AI 协作总规范，五仓就近生效）留在工作区根；本清单与 `DEV-TASKS.md` / `长期开发计划-2026Q4起.md` 于 2026-10-14 由工作区根迁入本仓 `docs/plans/`（纳入版本保存）；已交付一次性工作文档（vben 对标任务书 / 参考截图）归档至 `_delete/workspace-working-docs-20261014/`；修正失效的历史归档引用 |

发布前复核批新增的收尾修复：

- `system/utils/platform/migration_ledger.py` 移除失效的 `type: ignore`（mypy `unused-ignore`，全仓 864 文件 0 错）；
- **对标叙事清理**：两仓源码 / 样式 / 组件 / 测试 / 脚本 / CI / 种子注释中的对标归属说明全部改写为自足的语义描述
  （保留全部取值与行为口径，如时长、尺寸、曲线名），`xadmin-client/docs` 布局与可访问性文档同步改写；
  `docs/plans` 的对标任务书移入工作区回收站 `_delete/workspace-working-docs-20261014/`
  （结论已收敛进代码与 E2E 守护），`docs/plans/README.md` 同步「去向登记」；
- **任务台账编号清理**：代码 / 测试 / 脚本 / CI / 种子注释中的台账编号（批次与字母数字编号）全部去除，
  保留描述性语义（如「弹窗体系收敛」「回归验收」）；测试夹具值（如字典选项 P4/P5）与
  越权矩阵用例编号（M01~M37，与 security-review / ADR-012 交叉引用）按原样保留。

## 三、验证状态

> 下表中「复核批」= 2026-10-14 发布前冻结后的全量重跑（本批次所有改动均在覆盖范围内）。

| 面 | 命令 / 方式 | 结果 |
|----|-------------|------|
| 后端全量测试 | `uv run pytest -n auto`（**5942 用例**，真 PG 55433 + 真 Redis 56379） | ✅ **exit=0**（发布前复核批再次 exit=0；另有一轮出现 `test_online_stats.py` 2 例顺序性失败，隔离复跑通过 = 既有测试隔离问题，非回归） |
| 后端类型 | `uv run mypy`（业务 app 全量入列） | ✅ 864 文件 0 错（复核批修复 1 处 `unused-ignore`） |
| 后端静态门禁 | `ruff format --check` / `ruff check` | ✅ 全绿（迁移文件已按仓库风格格式化） |
| 后端脚本门禁 | 跨 app 导入 / 文件行数 / 函数长度 / 缓存键 `--strict` / makemigrations `--check` | ✅ 全绿（`No changes detected`，复核批重跑） |
| 后端文档门禁 | 事实 / 索引 / 路径 / 教程镜像 / ADR 状态 / 体积 / 站点导航 | ✅ 7/7 通过（30 条事实、87 篇 ADR 一致；复核批含 README 编辑后重跑） |
| 迁移结构等价 | 旧链库 `xadmin_schema_old` vs 新链库 `xadmin_schema_new` 的**数据库目录元数据**比对（表 / 列 / 索引 / 约束 / 序列） | ✅ 全等：126 表 · 1376 列 · 787 索引 · 585 约束 |
| 账本认领（旧链库实测） | 4.2.5 链库 → `upgrade_ledger --apply` → `migrate` → `upgrade_check` | ✅ `No migrations to apply` → `up-to-date`；12 条游离记录认领、7 条补记 |
| 升级通道命令（开发库复核） | `upgrade_check` / `upgrade_ledger`（只读对账） | ✅ `up-to-date` / `consistent`（87 已应用 / 0 待应用 / 0 游离；101 张表 0 缺失） |
| 权限点完整性 | `sync_menu_permissions --dry-run` | ✅ 扫描 511 路由 / 658 权限点，**缺口 0 条**（复核批重跑） |
| 种子回灌 | 全新库 `migrate` + `load_init_json` + `seed_demo_all` | ✅ 菜单 714 / 元数据 740 / 模型字段树 2094 / 角色 7；演示数据全量就绪（开发库复核：菜单 735 / 元数据 761 / 用户 135） |
| 前端单测 | `pnpm test:run` | ✅ 313 文件 / 2152 用例通过（复核批重跑） |
| 前端类型 | `pnpm typecheck`（tsc + vue-tsc） | ✅ 通过（复核批重跑） |
| 前端门禁 | 15 个 `check:*` + `check-file-length` | ✅ 全绿（bundle 在预算内：闭包 447 KB / 基线 452.8 KB；含 `check:version` 三方一致 5.0.0；权限点双向对账 639+19 点） |
| 前端 lint | eslint（`--max-warnings 0`）/ prettier / stylelint | ✅ 全绿（复核批重跑） |
| 安装器语法 | 全量 `bash -n` | ✅ 通过（复核批重跑） |
| 文档站构建 | `pnpm docs:build`（死链即失败） | ✅ 构建通过（复核批重跑） |
| 本地清库重灌 + 健康检查 | 开发库 DROP/CREATE + `init_data --with-demo` + `compilemessages` | ✅ 四项健康 `true`（db/redis/celery/storage）；演示账号 `admin/admin123` 认证通过、挂「演示模式」角色、135 用户 |
| **全量 E2E（双浏览器 · 4 路并行，复核批）** | `E2E_FRONT_PORT=8871 node scripts/e2e-run.mjs parallel` | ⚠️ **690 passed / 2 failed / 1 flaky / 31 skipped**（总用时 20 分钟）→ 3 个负载敏感用例**逐个隔离复跑全绿**（chat + screen-canvas-toolkit chromium `6 passed`、reset-password webkit `1 passed`），确认为并行负载瞬态，非回归 |

E2E 负载瞬态清单（与发版 4 路并行批次一致复现的两项 + 本轮新增一项，均隔离通过）：
`chat.e2e.ts` 私聊送达（chromium）、`screen-canvas-toolkit.e2e.ts` 画布工具集（chromium）、
`reset-password.e2e.ts` 错误验证码（webkit）；flaky 项 `layout-preferences.e2e.ts` 侧栏装饰（webkit）重试已过。
判定口径与既有台账一致（`xadmin-client/e2e/README.md`），不作为发版阻塞项。

**对标叙事 / 台账编号清理批次后复跑（2026-10-14 晚，最终冻结状态）**：

| 面 | 命令 | 结果 |
|----|------|------|
| 后端全量 | `uv run pytest -n auto` | ✅ exit=0（含被改动的测试数据与种子描述字段） |
| 后端静态 | `ruff format --check` / `ruff check` / makemigrations `--check` | ✅ 全绿 |
| 后端文档 | 事实 / 索引 / 路径 / ADR 状态 / 体积 / 站点导航 | ✅ 6/6 全绿 |
| 前端单测 | `pnpm test:run` | ✅ 313 文件 / 2152 用例通过 |
| 前端类型 / lint | `pnpm typecheck` / `pnpm lint`（eslint+prettier+stylelint） | ✅ exit=0 |
| 前端门禁复跑 | `check:version` / `i18n` / `contract` / `contract-usage` / `menu-permissions` / `as-unknown` | ✅ 全绿 |

## 四、发布步骤（打 tag 顺序）

> 只有用户明确指示时才执行 `git commit` / 打 tag。以下为建议顺序与每仓的检查项。

1. **xadmin-server**：`git add -A` → 单条提交 `feat!: 5.0 迁移链合并与累积能力发布`
   （body 分组说明：迁移合并 / 升级通道 / 种子重生成 / 演示模式 / 文档与规范）→ 推送 `dev` → CI 全绿。
2. **xadmin-client**：提交 `feat!: 5.0 版本号与文档同步` → 推送 `dev`。
3. **xadmin-installer**：提交（升级基线 4.2.5 + 账本认领 + 演示模式 + AGENTS.md）→ 推送（默认分支）。
4. **xadmin-web**：提交（发布说明配置 + AGENTS.md）→ 推送。
5. **xadmin-docs**：提交（5.0 升级页重写 + 路径口径 + AGENTS.md）→ 推送。
6. **打 tag `v5.0.0`**（顺序建议：server → client → installer → web → docs；tag 会触发各仓
   release workflow：镜像构建 / 版本门禁 / release notes）。
   - server 的 tag 同时触发 `quick_start.sh` 版本改写与附件上传；
   - installer 的 release 会把 `static.env` 的 `VERSION` 写成 tag 版本。
7. **镜像与分发**：确认 GHCR/Docker Hub 五个镜像（server / web / docs + celery 同镜像）tag 可用，
   镜像同步 workflow 完成国内源镜像同步。

提交注意：五仓均存在「部分暂存 + 未暂存」混合状态（release-config / 文档），**提交前一律
`git add -A` 全量暂存**再 commit，提交后 `git show --stat` + `git status --short` 双核对。

## 五、升级路径（对使用者）

| 当前库来源 | 路径 |
|-----------|------|
| 4.2.5（或已按 4.2.5 链升级） | `./xadmin.sh upgrade v5.0.0`（安装器自动：体检 → 账本认领 → 迁移 → `post_upgrade`）；手工形态见 [升级到 5.0](../ops/upgrade-5.0.md) |
| 更早版本（2026-10 四轮重建之前） | 备份 → 清库重建 → 种子/数据回灌（[存量库通道](../ops/upgrade-stock.md) §五） |
| 全新安装（含演示环境） | `./xadmin.sh install`（演示环境加 `WITH_DEMO_DATA=1`，演示账号 `admin / admin123`） |

## 六、发布后验证（建议按序）

1. `docker exec -i xadmin-server python manage.py upgrade_check` → `up-to-date`；
2. `docker exec -i xadmin-server python manage.py sync_menu_permissions --dry-run` → 缺口 0；
3. `curl https://<地址>/api/common/api/health` → 四项 `true`；
4. 浏览器登录（超管 + 演示账号）→ 菜单加载 → 任一列表页 → 一次导入导出；
5. 升级窗口结束后 24h 内查看错误日志与监控告警（`[ERROR]` 与 `AI:security` 模块）。

## 七、回滚

1. `./xadmin.sh stop`；
2. `./xadmin.sh restore_db <升级前备份>`；
3. `static.env` 版本改回 4.2.5 后 `./xadmin.sh load_image && ./xadmin.sh start`。

> 账本认领只写 `django_migrations`（无 DDL），回滚实质是「旧代码 + 旧数据」。
> 详见 [deployment-upgrade.md §6.2](../ops/deployment-upgrade.md)。

## 八、已知边界与遗留

- 5.0 不含表结构变更（迁移合并的前提）；5.x 若有结构变更按常规新增迁移表达。
- 白名单之外的旧链库不支持自动认领（三轮重建涉及表改名与字段演进，等价性不可保证）。
- 全量 E2E 的既有「负载瞬态」用例（chat 私聊、screen-canvas-toolkit、reset-password 等）若失败，
  按 `xadmin-client/e2e/README.md` 的隔离复跑口径判定，不作为发版阻塞项。
- 发布说明（GH Release）由 release-drafter 按标签生成；五仓已补齐「⚠️ 升级注意」分组
  与「未分类变更」兜底分组，并在模板中加入各仓的升级提示。
- 发版后待办：本清单与工作区回收站中的一次性文档按需归档/清理（见 `DEV-TASKS.md` 附注）。
