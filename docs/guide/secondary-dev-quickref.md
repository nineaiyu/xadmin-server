# 二开速查（场景 → 权威入口）

> 面向二次开发者的**按场景索引**：只登记"去哪查、改哪里、跑哪个命令"，不复制正文。
> 第一次走全流程请先读 [first-module-30min.md](first-module-30min.md)；本文是上手之后的速查页。

## 1. 门禁清单（改代码前先知道会被什么拦）

- **服务端**：全部 CI 门禁的统一登记处是 [ci-gates.md](../ci-gates.md)
  （每条门禁的守护语义 / 所在 workflow / 本地复跑命令；新增门禁必须先去那里登记）。
- **客户端**（xadmin-client 仓）：
  - `package.json` scripts 中的门禁类命令：`pnpm lint`（eslint + prettier + stylelint）、
    `pnpm lint:prettier:check`、`pnpm typecheck`（strict 单轨）、`pnpm check:as-unknown`、
    `pnpm check:i18n`、`pnpm check:bundle-size`、`pnpm test:coverage`、
    `pnpm check:contract-usage`、`pnpm check:contract`、`pnpm check:menu-permissions`、
    `pnpm check:version`；
  - 汇总执行面是客户端仓 `.github/workflows/lint-code.yml`：另有 file-length 门禁
    （`scripts/check-file-length.mjs`）、契约类型生成比对（`pnpm gen:metadata-types` 后
    `git diff --exit-code -- src/api/types`）、以及就地复用服务端 `scripts/` 的 docs 三门禁。
- **文档门禁**（本仓 `scripts/`，改动 `docs/` 后必须全绿）：
  `check_doc_index.py`（索引登记）/ `check_doc_facts.py`（事实防漂移）/
  `check_doc_paths.py`（引用路径可达）/ `check_tutorial_mirror.py`（教程命令与代码一致）。

## 2. 模块注册（新业务 app 如何接入）

- 注册链：`config.yml` 的 `XADMIN_APPS` → `CONFIG`（`server/conf/`，缺省空清单见
  `server/conf/defaults.py`）→ `server/settings/base.py` → `server/settings/apps.py`
  的 `build_installed_apps` 装进 `INSTALLED_APPS`；
- 路由注入：`server/urls.py` 调 `common/core/utils.py` 的 `auto_register_app_url`，
  收集各 app 自带 `config.py::URLPATTERNS`（缺 config.py 时打告警不崩溃，支持
  「先注册、后生成」顺序）；WS 路由放 app 自带 `routing.py` 自动收集；
- 最短路径：`generate_crud --register-app` 自动写入 `XADMIN_APPS`（幂等）；
  全流程见 [first-module-30min.md](first-module-30min.md)；
- 先例：`demo` app（官方示例，四件套 + 审批 / 二次确认演示，随框架同步演进）；
  演示数据与菜单注入命令族在 `demo_seed/management/commands/`（`seed_demo_all` 编排，
  DEBUG 或启用 demo 时注册）。

## 3. 契约同步（search-columns / search-fields / 元数据类型）

- 真源：服务端 `docs/schema/`；客户端镜像 `xadmin-client` 仓 `contract/schema/`，
  生成类型落在 `src/api/types/`；
- 写（人工同步）：客户端 `pnpm sync:contract`（拷镜像 + `pnpm gen:metadata-types`）；
  读（CI 校验）：`pnpm check:contract`；
- 路径约定：脚本默认**两仓同工作区**（`../xadmin-server`），可用 `XADMIN_SERVER_DIR`
  覆盖；`check-contract-sync.mjs` / `check-menu-permissions.mjs` 同口径，
  服务端未检出时校验类脚本跳过、同步类脚本显式失败；
- 生成物纪律：类型重生成后必须提交（CI 比对 `src/api/types`），且 schema 生成物必须有
  import 消费方（`pnpm check:contract-usage`）。

## 4. 审批接入（先裁决引擎，再接回写）

- 裁决入口：`approval/README.md` 的**双审批决策树**——高危操作拦截接
  approvals（`@ApprovalRequired()` + 412 重放），业务单流转接 approval-flows
  （`create_instance` + 终态回写），判据与对照表同文；
- 终态回写注册制：业务 app 在自身 `config.py` 声明 `APPROVAL_BIZ_SYNCERS`
  （biz_type → 同步器导入路径），注册表按声明收集，新增回写零核心文件改动；
  先例：`demo/config.py`、`dataset/config.py`；守护测试 `tests/unit/approval/test_biz_sync.py`；
- 任务化步骤：[recipes.md](recipes.md) R17（业务接入审批流：提交 / 回写 / 流转三步）。

## 5. 生成器使用（generate_crud）

- CLI：`python manage.py generate_crud <app>.<Model>`（`--dry-run` 预览；常用参数
  `--register-app` / `--with-import-export` / `--with-module` / `--bootstrap --grant-to <角色code>`；
  产物输出根可用 `--output` 与 `--frontend-root` 重定向，默认当前仓与同级客户端仓）；
  参数全集见 `--help`；
- GUI：管理页「代码生成器」（端点 `system/views/admin/codegen.py`），与 CLI 同一引擎、
  同一字段计划口径（模型清单 / 字段编辑 / 预览打包下载，不落盘不写库）；
- 全流程（建 app → 生成 → 菜单授权 → `doctor` 自检）：
  [first-module-30min.md](first-module-30min.md)；产物默认值与门禁结论见同文
  「生成物自带的门禁与默认值」一节。
