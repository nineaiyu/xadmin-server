# AGENTS.md — xadmin-server（后端）

> 继承工作区根 `../AGENTS.md`（环境、测试门禁、安全禁区、验证口径都在那里）；
> 本文件只写**后端仓库特有的约定**。规则优先级：用户当前指令 > 本文件 > 工作区根。

## 一、仓库地图

| 目录 | 内容 |
|------|------|
| `server/` | 工程装配：`settings/`（base / storage / csp / custom…）、`const.py`（`VERSION` 版本事实源）、`asgi.py`、`conf/`（config.yml 加载） |
| `system/` | 平台域：用户 / 角色 / 权限 / 菜单 / 配置 / 字典 / 模块 / 监控 / 代码生成；内部按 `views/`（user·auth·admin·platform·task·open·search）与 `utils/`（identity·file·audit·task·platform·preview）分域 |
| `identity/` `file/` `audit/` `task/` | 由 system 拆出的四域 app（用户与组织 / 文件 / 审计与脱敏 / 任务与 webhook） |
| `dataset/` | 数据分析与表单：数据集 / 仪表盘 / 大屏 / 报表 / 动态表单与提交 |
| `approval/` | 审批流引擎（定义 / 版本 / 实例 / 节点任务 / 委托 / 规则） |
| `ai/` | AI 平台：档案 / 聊天记录 / 知识库与分块 / 用量 / MCP |
| `message/` `notifications/` `mfa/` `captcha/` `settings/` | 聊天室 / 消息通知 / 多因子 / 验证码 / 系统设置模型 |
| `integrations/` `devtools/` `demo/` `demo_seed/` | 第三方适配 / 代码生成器 / demo 官方示例 / 演示数据命令（条件注册） |
| `packages/xadmin-common/` | 框架内核分发包 `xadmin-common`（导入名仍是 `common`）：ORM 基类、DRF 基座、元数据、缓存、通知内核、设置契约 |
| `loadjson/` | **平台共享层种子**（目录菜单 / 平台级页面与权限点 / 角色 / 站点配置 / 跨域字典 / 内置流程定义…），由 `load_init_json` 导入 |
| `<app>/seeds/` | **应用层种子**：`manifest.json` 自声明 `depends` / `url_prefixes` / `config_keys` / `dict_codes`，菜单与权限点、字段白名单、字段树、配置、字典按归属落在这里 |
| `ops/` | 运维脚本（`init_data.py` / `dev_up.sh` / `db_backup.sh` / `test_env_reset.sh` / nginx 配置…） |
| `scripts/` | 门禁与守护脚本（`check_*.py` 全家桶、`e2e_seed.py`、`release_kernel.py`…；统一索引在 `docs/ci-gates.md`） |
| `locale/` | i18n 译文（`locale/zh/LC_MESSAGES/django.po`；新增 `_()` 文案必须补 zh） |
| `examples/` | 二开插件样例（`examples/plugins`，配 `docs/guide/plugin-development.md`） |
| `loadtest/` | k6 性能基线（配 CI `perf.yml` 与 `settings_loadtest.py`） |
| `docs/` | 文档中心（`docs/README.md` 是索引；`docs/schema/` 是接口契约真源；`docs/plans/` 含工作区台账与 2026-10 审查分册） |
| `tests/` | `unit/` + `integration/`，按 app 分目录（档位基座 `tests/settings_base.py`，real / pg / e2e 三档均继承它） |

权威文档入口是 `docs/README.md`；高频必读：`docs/ci-gates.md`（全部门禁统一索引，**新增门禁必须先在那登记**）、
`docs/guide/first-module-30min.md`（二开主线 `generate_crud`）、`docs/guide/recipes.md`（23 条扩展处方）、
`docs/guide/secondary-dev-quickref.md`（二开速查）、`docs/dev-pitfalls.md`（「没报错但没效果」先查）、
`docs/architecture/handbook-backend.md`（后端组件手册）、`docs/guide/menu-maintenance.md`
（菜单种子口径与 menu-permissions 对账白名单登记处）、`docs/architecture/kernel-package.md`（内核包布局与宿主接线）。

## 二、常用命令

```bash
uv sync --all-groups                            # 依赖（唯一入口：pyproject.toml + uv.lock）
docker compose up -d                            # 开发栈（nginx:8896）
docker restart xadmin-server xadmin-celery-worker xadmin-celery-heavy   # 改后端后必做（约 5s；改 celery 任务/配置时加 xadmin-celery-beat）
docker restart -t 3 xadmin-server xadmin-celery-worker xadmin-celery-heavy   # 长任务场景：限制本次停止最多等 3s
docker compose restart server celery-worker celery-heavy celery-beat   # 生产语义：按 compose 宽限期优雅停机
python manage.py migrate                        # 迁移
python manage.py makemigrations --check --dry-run   # 迁移漂移门禁
python manage.py load_init_json                 # 灌默认数据（幂等）
python manage.py sync_menu_permissions --dry-run    # 权限点缺口扫描（期望「缺口 0 条」）
python manage.py sync_menu_permissions --update-seed # 把变更回写种子文件
python manage.py sync_field_permissions --update-seed # 新增字段后：字段树 + 新增字段白名单 + 种子（不动历史裁剪）
python manage.py sync_field_permissions --dry-run     # 预览本次新增字段会补到哪些角色白名单
python manage.py seed_demo_all [--reset]        # 演示数据（演示账号 admin/admin123）
python manage.py post_upgrade                   # 升级后补全（幂等）
python manage.py doctor                         # 上线前自检
python manage.py generate_crud                  # 二开代码生成主线（配套 docs/guide/first-module-30min.md）
python manage.py modules / module               # 模块裁剪 CLI（ModuleSpec 的命令入口）
python manage.py upgrade_check / upgrade_ledger # 存量库升级判定 / 迁移账本认领
```

## 三、代码约定

1. **ViewSet 分层**：`BaseViewSet` 家族 + Action Mixin（列表能力按需混入：`SearchFieldsAction`、
   `SearchColumnsAction`、`BatchDestroyAction`、`RecycleBinAction`、`ExportAction`…）。
   有 `table_fields` 或 `filterset_class` 的视图集**必须**混入这两个 Action，否则前端列元数据缺失
   （表现为「有数据但单元格为空」）。
2. **序列化器**：业务模型用 `BaseModelSerializer`；`upload` 关联写路径的空值必须 None 安全。
   字典驱动字段（`DictChoiceField` / `LabeledChoiceField`）写入路径要测（历史缺陷：choices 反向映射写坏）。
3. **模型**：`DbAuditModel` / `DbUuidModel` 基类；需要回收站用软删模型；`Meta.ordering` 影响列表默认序；
   `select_related` + `prefetch_related` 是列表性能默认要求。
4. **权限点是数据不是代码**：新增接口 → **所属应用**的 `seeds/menu.json`（权限点，`动作:组件名`）+
   `seeds/menumeta.json`（前端路由 / 按钮元数据）；平台级目录/跨域页面才落 `loadjson/`。
   `sync_menu_permissions --update-seed` 按归属回写各层，改完用 `--dry-run` 验证缺口 0；
   外部/新应用还可 `dump_init_json --split-apps --apps <app>` 直接生成整层种子。

5. **契约**：改接口响应结构 → 先改 `docs/schema/*.json` → 再改代码 → 前端 `pnpm sync:contract`。
6. **错误码**：新增错误码先在 `docs/exception-handling.md` 登记；业务失败用 `ApiResponse` 的
   `code != 1000` + `detail`（前端依赖 `detail` 展示）。
7. **i18n**：`_()` 文案必须补 `locale/zh/LC_MESSAGES/django.po` 译文（zh 门禁：无空译文 / 无 fuzzy / 无重复）。
8. **行数门禁**：单文件 ≤ 500 行、单函数 ≤ 100 行；超限就按域拆分（本项目已拆分过多轮，照既有模式走）。
9. **缓存**：缓存键必须单文件前缀 + 登记（`scripts/check_cache_keys.py --strict`）。
10. **模块裁剪**：新增模块入口要在 `ModuleSpec` 声明（含 `ws_routes` 归属），否则裁剪预设会漏。

## 四、迁移纪律（5.0 起）

- 当前各业务 app 的基线是**初始迁移**（`0001_initial`，跨 app 循环依赖处另有 `0002_initial`），
  此后按常规线性演进（如 `system/migrations/0002_datapermission_soft_delete.py`）。
- **不要修改已发布的初始迁移**；有结构变更就 `makemigrations` 生成新迁移（常规线性演进）。
- 迁移里无法自动生成的 DDL（如 `CREATE EXTENSION pg_trgm` / `vector`）以 `RunPython` 放在链首，
  失败只告警不阻断（检索能力降级）。
- 存量库升级走账本认领：`upgrade_ledger --apply`（只写 `django_migrations`，不执行 DDL）→ `migrate`；
  白名单在 `system/utils/platform/migration_ledger.py`。
- 改了种子（平台层 `loadjson/` 或应用层 `<app>/seeds/`）后：先 `load_init_json --check`
  （0 error），再 `load_init_json` 复灌 + 重启容器（种子文件改动不会自动生效）。新增应用
  必须先进 `config.yml` 的 `XADMIN_APPS` 清单（`server/settings/base.py` 装配），否则其
  `seeds/` 会被静默忽略（`doctor` 会告警）。

## 五、测试

```bash
docker compose -f compose.test.yml up -d      # 测试栈（PG 55433 / Redis 56379）
uv run pytest -n auto                          # 全量（真 PG + 真 Redis，pytest.ini 缺省 settings_real）
uv run pytest tests/unit/system/test_xxx.py -q # 单文件
uv run pytest tests/unit/system/test_xxx.py::TestY::test_z -q   # 单用例
bash ops/test_env_reset.sh                     # 重置测试容器（数据脏了时）
```

- 测试档：`tests/settings_real.py`（缺省，真 PG + 真 Redis）、`settings_pg`（nightly 兜底）、
  `settings_e2e`（E2E 用，独立库 `xadmin_e2e`）。
- `pytest.ini` 要点：缺省 `--maxprocesses=8`，**上限 15**（xdist worker 按号各占一个 Redis
  逻辑库，>15 会共库互踩）；`faulthandler_timeout=600` 是卡死自证——任一用例 600s 未推进
  即向 stderr 打印全线程栈后退出，把「无声挂死」变成可定位事件。
- **测试隔离**：`transaction=True` 用例的 DDL 不随回滚清理；用例内动态建模型要
  `apps.all_models.pop` + `clear_cache()`；改 `MEDIA_ROOT` 注意存储委托缓存指纹。
- 断言文案时注意语言环境：CI 在跑 pytest 前会 `compilemessages` 编译 `.mo`（中文生效）；
  本地未装 gettext / 未编译时回退英文——**文案断言用双语写法**（历史踩坑：7 例文案断言挂 CI）。
- **同一测试栈只跑一轮 pytest**：两轮并发会在 `django_db_setup` 里互相 DROP/CREATE
  测试库（DB CPU 打满、Python 无 CPU、SQL 日志只有几行）。`tests/conftest.py` 用 PG
  会话级 advisory lock 串行化，第二轮启动即退出；确需并发设
  `XADMIN_ALLOW_CONCURRENT_PYTEST=1`（不推荐）。
- 新增行为的守护测试是默认要求；缺陷修复先写复现用例。

## 六、门禁清单（提交前逐条跑）

```bash
uv run ruff format --check . && uv run ruff check .
uv run mypy                            # 业务 app 已全量入列，新增告警就地修复
python scripts/check_cross_app_imports.py
python scripts/check_file_length.py && python scripts/check_function_length.py
python scripts/check_cache_keys.py --strict            # 纯本地门禁（CI 不跑）
python scripts/check_ledger_ids.py && python scripts/check_menu_titles.py
python scripts/check_tutorial_mirror.py                # 教程命令/参数与代码一致（example 四篇）
python manage.py makemigrations --check --dry-run      # 迁移漂移（纯本地门禁，CI 不跑）
python manage.py load_init_json --check                # 种子 0 error
python scripts/check_doc_facts.py && python scripts/check_doc_index.py && python scripts/check_doc_paths.py
python scripts/check_adr_status.py && python scripts/check_doc_size.py && python scripts/check_docs_site_nav.py
```

门禁归属三种，别混淆：**① 纯本地**（`check_cache_keys`、迁移漂移——CI 不跑）；**② 本仓 CI**
（`.github/workflows/lint.yml` / `test.yml`：上述 check_*、`uv lock --check`、覆盖率
`--cov-fail-under=85`（`.coveragerc` 同值，有守护测试）、menu-permissions 种子↔前端双向对账
（检出 xadmin-client 跑）、`release_kernel check/build`、`manage.py check`、pip-audit）；
**③ 跨仓 CI**（`check_docs_site_nav.py` / `check_docs_site_links.py` 在 `xadmin-docs` 的
docs-build 里对构建产物跑）。全量清单以 `docs/ci-gates.md` 为准；Trivy 只在镜像构建流水线
（`build-image.yml`）。

## 七、提交纪律

- 只有用户明确要求才 `git commit`；提交信息 `type(scope): 中文描述`（subject 中文开头）。
- 源码注释 / docstring / 测试名 / 种子 JSON 里**不写台账编号**（`ADR-090` 决策编号、
  `T03-01` / `O10-2` / `TG-3` 一类），需要交代来龙去脉时写一句自足的语义说明。
  守护：`python scripts/check_ledger_ids.py`。
- **docstring 首行 = 菜单显示名**：视图类与 `@action` 方法的 docstring 首行必须是精炼的
  名词短语（≤24 字、不带句号），详细说明下沉到空行之后的段落。首行会被权限点同步取作
  `MenuMeta.title`，写长会撑破菜单树与授权选择器。守护：`python scripts/check_menu_titles.py`。
- 改后端运行时代码后必须重启容器再验证，不要只凭单测通过就报「已完成」。
- 本地钩子：`.pre-commit-config.yaml`（ruff）与 `scripts/hooks/pre-commit`（对暂存 py 文件
  format + check），用 `ln -sf ../../scripts/hooks/pre-commit .git/hooks/pre-commit` 启用。

## 八、当前已知功能缺口（2026-10 审查，修复后删除本节）

以下是审查已复核、尚未修复的权限/筛选缺口；改动相邻代码时不要把它们当成「已支持」：

1. **同 path+method 权限点**：设置 basic（GET/PATCH）、操作日志（GET）、任务执行（GET）
   各登记了多个权限码；运行时按 path 建字典会互相覆盖，字段权限上下文可能取错。
   新增共享端点前先读 `packages/xadmin-common/common/core/permission.py` 的
   `get_user_permission`（设置写入已按权限码做字段面隔离，读取仍为 OR 语义）。

完整证据与逐页记录见 `docs/plans/code-review-2026-10.md` 与各分册；修复顺序与验证方式见
`docs/plans/code-review-2026-10-actions.md`。
