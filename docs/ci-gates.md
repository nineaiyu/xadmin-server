# CI 门禁清单（统一登记处）

> 服务端仓库全部 CI 门禁的统一索引：每个门禁守护什么、落在哪个 workflow、如何本地复跑。
> 新增门禁（守护脚本 / 守护测试 / 流水线 job）必须在此登记一行——门禁的存在与位置本身
> 也是需要防漂移的事实。前端仓门禁见 `xadmin-client` 仓内文档，跨仓门禁在表中注明。

## 门禁总览（PR 触发）

| workflow | job / 步骤 | 守护内容 | 守护物 | 本地复跑 |
|---|---|---|---|---|
| `lint.yml` | ruff | 代码风格（format --check + check） | — | `ruff format --check . && ruff check .` |
| `lint.yml` | Cross-app import gate | app 间依赖方向：框架层 `common` 禁止 import `server` 及越权横向依赖（allowlist 只减不增） | `scripts/check_cross_app_imports.py` | `python scripts/check_cross_app_imports.py` |
| `lint-code.yml`（xadmin-client） | Declarative overlay gate | 弹层选型收口：直接手写 `<el-dialog>` / `<el-drawer>` 必须改走 addDialog / addDrawer；存量按文件基线只减不增（理由登记在 `xadmin-client/scripts/declarative-overlays-baseline.json`） | `xadmin-client/scripts/check-declarative-overlays.mjs` | `cd ../xadmin-client && pnpm check:overlays` |
| `lint.yml` | Menu permission reconcile gate | 菜单种子权限点 ↔ 前端消费点双向对账（种子无消费 / 前端无种子 / 页面 URL=目录例外），白名单见 `docs/guide/menu-maintenance.md` §5，脚本归前端仓 | `xadmin-client/scripts/check-menu-permissions.mjs`（本仓检出客户端就地执行） | `XADMIN_SERVER_DIR=.. node ../xadmin-client/scripts/check-menu-permissions.mjs` |
| `lint.yml` | Type check | 全仓 mypy；**common 框架层 + 全部业务 app 启用 strict 子集**（`[[tool.mypy.overrides]]` 逐 app 入列：captcha / demo / devtools / integrations / mfa / server / settings + audit / file / demo_seed / notifications / task / message / dataset / approval / ai / system / identity；2026-10-09 收口，无未推广面） | `pyproject.toml [tool.mypy]` + 守护 `tests/unit/test_mypy_strict_pilot.py` | `mypy` |
| `lint.yml` | File length gate | 单文件 >500 行新增即失败（存量基线只减不增） | `scripts/check_file_length.py` | `python scripts/check_file_length.py` |
| `lint.yml` | Function length gate | 单函数 ≥100 行新增即失败（存量基线只减不增，AST 计行） | `scripts/check_function_length.py` | `python scripts/check_function_length.py` |
| `lint.yml` | Ledger id gate | 源码 / 测试 / 种子 / 配置里禁止出现 `ADR-090`、`T03-01`、`O10-2`、`TG-3`，以及 `LOG-P1-01`、`FE-DATA-P1-04`、`C-03` 一类评审编号（只允许提交信息与 `docs/` 台账本体） | `scripts/check_ledger_ids.py` | `python scripts/check_ledger_ids.py` |
| `lint.yml` | Captcha coverage gate | 匿名写端点必须调用 `require_human` / `check_token_and_captcha`，或在脚本 `EXEMPTIONS` 写明豁免理由（新增匿名接口漏挂人机校验 = CI 红） | `scripts/check_captcha_coverage.py` | `python scripts/check_captcha_coverage.py` |
| `lint.yml` | Captcha clean-cut gate | 旧图片码模式 / `CaptchaStore` / `CaptchaAuth` / `captcha_key` / `captcha_code` 不得复活（统一协议是唯一路径） | `scripts/check_captcha_legacy.py` | `python scripts/check_captcha_legacy.py` |
| `lint.yml` | Captcha asset gate | 背景图库 manifest：许可 / 来源 / 路径穿越 / 扩展名 / sha256 / 2MB 体积；空 files = 程序化兜底 | `scripts/check_captcha_assets.py` | `python scripts/check_captcha_assets.py` |
| `lint.yml` | Menu title gate | 视图类与 `@action` 的 docstring 首行 ≤24 字（首行 = 菜单显示名 / 操作日志 module，详细说明下沉后续段落） | `scripts/check_menu_titles.py` | `python scripts/check_menu_titles.py` |
| `lint.yml` | Doc facts gate | 文档"当前事实"防漂移（版本 / 端口 / workflow 参数等，FACTS 表逐条登记） | `scripts/check_doc_facts.py` | `python scripts/check_doc_facts.py` |
| `lint.yml` | Doc index gate | 文档索引覆盖（防孤儿文档：`docs/` 内容型文档必须登记索引）；若存在 `plans/archive/`：逐篇登记 plans/README，且不得出现在 docs/README 主索引（当前归档区已清空，规则保留） | `scripts/check_doc_index.py` | `python scripts/check_doc_index.py` |
| `lint.yml` | Doc paths gate | 组件手册（主册 + `handbook-backend/frontend` 分册）/ 活跃文档引用的代码路径必须可达 | `scripts/check_doc_paths.py` | `python scripts/check_doc_paths.py` |
| `lint.yml` | Tutorial mirror gate | 教程中的 manage.py 命令 / CLI 参数 / demo 路径与代码一致 | `scripts/check_tutorial_mirror.py` | `python scripts/check_tutorial_mirror.py` |
| `lint.yml` | ADR status gate | ADR 生命周期：状态行归一且取值落闭集 / supersede 链（目标存在·无环·双向一致）/ 触发制项登记 / `adr/README.md` 状态列锁步 | `scripts/check_adr_status.py` | `python scripts/check_adr_status.py` |
| `lint.yml` | Doc size gate | 文档体积预算（单篇 >40 KB 新增即失败；`plans/archive/` 前缀豁免——当前归档区已清空，规则保留；单篇豁免只减不增） | `scripts/check_doc_size.py` | `python scripts/check_doc_size.py` |
| `test.yml` | pytest（真 PG + 真 Redis） | 全量单测 + 集成测试 + 守护测试；覆盖率阈值门禁（`--cov-fail-under`，值见 workflow 注释） | `tests/` 全树 | `pytest -n auto`（先 `docker compose -f compose.test.yml up -d`） |
| `test.yml` | Django system check | 数据库标记检查（索引名超长 / GinIndex 前置条件等仅 `check --database` 可见的模型缺陷） | `manage.py check --database default` | 同左（需真 PG） |
| `security.yml` | pip-audit | 生产依赖已知漏洞（requirements.txt 口径，PR + 每周定时） | `pip-audit -r requirements.txt` | 同左 |
| `docs-build.yml`（xadmin-docs） | VitePress build | 对外站点构建：**页级**死链与构建错误（vitepress 不校验 `#fragment`，锚点级由下一行守护） | `xadmin-docs` 全站 markdown | `cd ../xadmin-docs && pnpm docs:build` |
| `docs-build.yml`（xadmin-docs） | Site links & references | 站点内链接**锚点级**可达性（对构建产物的 `id=` 核对，slug 以产物为准）+ 正文反引号里的跨仓路径引用（`docs/` `ops/` 服务端、`scripts/` 安装器） | `scripts/check_docs_site_links.py` | `cd ../xadmin-docs && pnpm docs:build && python ../xadmin-server/scripts/check_docs_site_links.py` |
| `docs-build.yml`（xadmin-docs） | Doc facts / Site nav | 跨仓"当前事实"防漂移（含安装器 `scripts/7_upgrade.sh` 的升级基线 `required_version`）+ 站点导航覆盖（防孤儿页面） | `scripts/check_doc_facts.py` · `scripts/check_docs_site_nav.py` | `python scripts/check_doc_facts.py` · `python scripts/check_docs_site_nav.py` |

## 随 pytest 全量进 CI 的守护测试（专项产出的结构性回归点）

以下守护测试以 pytest 用例形态存在，由 `test.yml` 的全量 pytest 天然纳入（新增守护测试
放对目录即自动生效，无需改 workflow）；在此登记其守护语义，供新增同类时对齐形态。

| 守护测试 | 守护语义 | 位置 |
|---|---|---|
| 配置转发表驱动守护 | `server/settings/setting.py` 的 `FORWARD_KEYS` 声明式转发：CONFIG 新增键必须落在「转发清单 ∪ 装配模块直接引用 ∪ 豁免登记」三处之一，新键漏接线即红 | `tests/unit/server/test_settings_forwarding.py` |
| 审批回写注册制 | 业务 app 以 `APPROVAL_BIZ_SYNCERS` 声明回写同步器、注册表声明优先内置兜底——新增审批回写零核心文件改动，注册表可解析即绿 | `tests/unit/approval/test_biz_sync.py` |
| 门禁脚本自测 | 上述门禁脚本自身的违例识别 / allowlist / 方向规则 / 契约缝漂移（CI 裁判也要被裁判） | `tests/unit/scripts/test_gate_scripts.py` |
| 覆盖率阈值门禁 | `--cov-fail-under` 汇入全量 pytest（`tests/unit/test_coverage_gate.py` 守护其配置存在性） | `tests/unit/test_coverage_gate.py` |
| 依赖清单一致性 | 依赖清单（requirements ↔ uv.lock ↔ Dockerfile 安装面）漂移即红；含工作区骨架（内核成员 editable 锁定 / wheel 只收内核包本体 / 内核依赖落在运行产物中） | `tests/unit/test_dependency_manifest.py` |
| 内核 settings 契约锁步 | 框架内核读取的 settings 键 ↔ 契约面（`packages/xadmin-common/common/settings_contract.py`）双向一致：缺省表达式 / 消费方清单 / 文档表（[kernel-package.md](architecture/kernel-package.md) §三）逐项锁步 | `tests/unit/common/test_settings_contract.py` |
| mypy 严格度试点配置 | 内核与业务 app 覆盖块逐项 flag 齐备且同口径、**未改用 `strict = true`**（mypy 2.x per-module strict 会全局生效）、已推广 app 清单（`PROMOTED_APPS`）与配置块双向一致、全局段不含试点 flag | `tests/unit/test_mypy_strict_pilot.py` |
| 二开插件三通道装配 | 示例插件（entry point / `AppConfig.ready()` / 应用级扩展点）三条通道真实驱动：契约面解析到插件实现、模块声明进清单并随预设开关、停用后路由 404 / WS 4404 / 权限点前缀拦截（清理纪律：注册表与 app registry 复原） | `tests/integration/plugins/test_plugin_demo.py` |
| 数据权限菜单范围与种子表名 | 显式 action 授权覆盖同资源 list 回退（可编译才算已配置）；list 未配置时详情/预览回退 list；字段权限保持精确隔离；规则表名必须是当前 app registry 中的有效模型 label | `tests/unit/common/test_data_scope_menu.py` · `tests/unit/common/test_data_permission_filter.py` · `tests/unit/common/test_serializer_field_permission.py` · `tests/unit/demo_seed/test_demo_permissions.py` |

## 非阻塞 / 定时档

| workflow | 内容 | 触发 |
|---|---|---|
| `test-nightly-pg.yml` | PG 容器测试档（每周六 UTC 18:30）：方言 / 连接池 / pk 序列类缺陷分诊 | schedule（红不阻断 PR） |
| `perf.yml` | k6 性能基线对比（CI 断崖档） | workflow_dispatch / schedule |
| `build-base-image.yml` / `build-image.yml` | 镜像构建（多架构推送 + 镜像构建溯源 attestation + SBOM 随 Release 分发） | push / release |
| `publish-kernel.yml` | 内核分发包发布：SBOM + 构建溯源 + 私有源上传 + Release（`prerelease`；dry-run 默认） | workflow_dispatch |
| `renovate.yml` | 依赖升级 PR | schedule |

## 新增门禁的登记约定

1. 守护脚本放 `scripts/check_*.py`（可被 `tests/unit/scripts/test_gate_scripts.py` 反向自测），
   守护测试放 `tests/` 对应目录（pytest 自动收集）；
2. 挂到 `lint.yml`（静态）或 `test.yml`（需依赖）对应 job；
3. 在本文件「门禁总览」或「守护测试」表登记一行——含守护语义与本地复跑命令；
4. 门禁豁免（allowlist / 基线）只减不增，新增必须注明理由与去向。
