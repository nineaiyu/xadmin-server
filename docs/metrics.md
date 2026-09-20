# xadmin 基线指标看板（metrics.md）

> 建立日期：2026-09-04（半年规划 T1.8 交付物）
> 用途：半年规划 §八 KPI 的基线登记处，每阶段结束时回填实测值。
> 统一口径：any 统计正则 `: any|as any`（src/ 内）；跨 app 坏味道 = 非 tests/migrations 的跨 app `models/serializers/views/notifications/backends/signal` 直接 import。

## 一、基线实测值（2026-09-04）

| 指标 | xadmin-server | xadmin-client |
|------|--------------|---------------|
| 单元测试用例数 | 184（本次实测，含 T1.1 新增 6 例） | 62 |
| 覆盖率 | **61%**（9,425 语句 / 3,681 未覆盖，实测） | 仅统计 api+utils，无门禁 |
| 覆盖率门禁 | CI `--cov-fail-under=55`（历史基线；当前门禁值见 `.github/workflows/test.yml`，2026-09-18 起为 85%） | 无 |
| E2E 用例 | 34（双浏览器 chromium+webkit，retries 1，本地全绿 2026-09-06） | 34（CI 实跑：68 passed / 7.1m，dev push 触发，2026-09-06） |
| any 存量 | — | 226 处 / 91 文件 → **0 处**（T1.7 五期/TD-23 后，2026-09-05 实测；grep 口径 `: any|as any` 全仓归零） |
| any 豁免清单 | — | **0（四期全部清零摘除，2026-09-05）**；历史路径 71→40→36→34→…→14→0；注意 eslint 规则块仅挂 *.ts/tsx，*.vue 不受 no-explicit-any 约束（缺口登记 TD-23） |
| 跨 app 坏味道 import | **10 处**（T1.5 后，原 21）→ **4 处**（T2.2 后，2026-09-05 实测：管理命令×3 + demo FK×1，均为规划允许保留，CI 门禁已接入） | — |
| 巨型文件（>500 行，非数据/迁移） | modelset.py 749 → **0**（T2.1 拆分为 10 模块包，最大 metadata.py 287 行） | lay-tag 695 / lay-setting 680 / user hook.tsx 603 / handle.tsx 789 → **0**（T2.5 + N3 五期 F5 拆分：handle.tsx → handle-dialog 319 / handle-record 209 / 本体 273，导出签名不变） |
| TODO/FIXME 标记 | 0 | 0 |

## 二、构建与体积（client）

### T3.4 分包后（2026-09-05 实测，gzip9 口径）

| 指标 | 基线（09-04） | T3.4 后 | 备注 |
|------|------|------|------|
| **首屏 JS（静态依赖闭包合计）** | 31 chunks / **1,020.5 KB** | 12 chunks / **905.0 KB（-11.3%）** | 旧口径 698.8KB 仅统计入口单文件，漏算 26 个静态依赖块，已修正 |
| 主 chunk `index-*.js` | 2,089 KB raw / 698.8 KB gzip | 459.9 KB raw / **440 KB gzip（-37%）** | 应用代码；vendor 经 advancedChunks 分离 |
| `vue-core` / `element-plus` / `plus-pro` / `echarts` | 混在主 chunk | 87 / 279 / 20 / **懒加载** KB | echarts 改启动预热+动态加载，仅仪表盘消费 |
| 移出首屏的大块 | — | descriptions 110K、RePureTableBar 39K、checkbox 30K 等 6 个 | |
| `vanilla-jsoneditor-*.js` | 1,185 KB raw / 347.8 KB gzip | 不变（懒加载） | 已知懒加载大件，仅 JSON 编辑表单触发 |
| `index-*.css` / `element-plus-*.css` | 508 KB raw / 73.3 KB gzip | 拆分缓存 | 全量 EP CSS 仍在首屏（element-plus 按需引入为 -20% 后续主路径） |
| chunkSizeWarningLimit | 4000（掩盖膨胀） | **1000** | vanilla-jsoneditor ~1.2MB 会告警，为有意保留的信号 |
| 构建耗时 | 见 CI（本地未单测） | 同左 | |

## 三、回填记录

| 日期 | 阶段 | 变更摘要 |
|------|------|----------|
| 2026-09-17 | 长期优化方案批二（P1/P2 收口：SLO 采集 / 体验基线 / 时长预算 / 裁剪演练 / 依赖清账） | **A2** SLO 采集机制上线（`slo_snapshot.py --append` + `utils/slo_snapshot_cron.sh` 每日 JSONL；首采 可用性 100.000% / P95 0.05s / 任务 99.89%，≥3 个月后校准）；**U1** 前端体验基线首测（登录页 TTFB 332ms / FCP 1172ms / LCP 1224ms / CLS 0.016；列表页 CLS 0.05~0.24 波动，登记改进候选）——`pnpm test:e2e:perf` + `e2e/perf-baseline.json`；**Q4** E2E 跑批时长预算（各 shard/总用时记录 + 同环境基线 +20% 校验，`e2e/duration-budget.json`）；**F1** 模块裁剪五层矩阵演练 9 例（路由/菜单/周期任务/缓存/声明一致性）；**T5** docs 站依赖清账 **18 → 0** |\r\n| 2026-09-17 | 依赖 audit 季度例行（T1） | server `pip_audit` **0**；client 官方源 `pnpm audit` **0**；xadmin-docs 官方源 **18 → 0**（移除无补丁依赖 `markdown-it-custom-attrs` → 内联 fancybox 属性实现；overrides 修复 vite 6.4.3 / postcss / rollup / nanoid / preact / esbuild / mdast-util-to-hast；`docs:build` 5.8s 通过）。口径：pnpm 11 起 overrides 只读 `pnpm-workspace.yaml`（package.json 的 `pnpm` 字段被忽略） |\r\n| 2026-09-17 | 长期优化方案首批（质量门禁与文档复核） | **Q1**：pytest 全量实测 **2534 passed / 1 skipped / 覆盖率 87%**（25015 语句），CI 覆盖率门禁 78 → **82**；**Q2**：strict 全仓收口——e2e 存量 **10 处**清零，`tsconfig.strict.json` 纳入 `e2e/**`，`typecheck:strict` 改全仓判定（scripts 的 `.mjs` 待评估 allowJs）；**Q3**：巨型文件（>500 行）门禁进两端 CI——实测存量 **后端 13 / 前端 4**（另 1 数据文件豁免），基线登记「只减不增」；**Q5**：两端 PR 模板补断言环境无关项与行数勾选；**T5**：xadmin-docs 新增 `docs-build.yml`（本地 `pnpm docs:build` 5.24s 通过）。详录见 [plans/长期优化方案-2034.10-2039.09.md](plans/长期优化方案-2034.10-2039.09.md) §十 |
| 2026-09-17 | 存量巨型文件拆分第一批（纯搬迁） | **后端** `system/utils/permission_sync.py`（511 行）→ 包（constants / types / scan / apply / audit / seed，`__init__` 统一再导出，`sync.*` 消费方零改动，专项测试 10 例通过）；**前端** `src/router/utils.ts`（525 行）→ 包（route-tree / async-routes / auth / nav，index 统一再导出，88 个消费方零改动）；存量基线 **13/4 → 12/3**。验证：pytest 2534 + 覆盖率 87% + 两项静态门禁、vitest 250、typecheck/strict 全仓、E2E smoke+auth+locale 双浏览器 30 passed |
| 2026-09-17 | 存量巨型文件拆分第二批 | **后端** `system/utils/permission_preview.py`（985 行，最大存量）→ 包（constants / labels / decode / queries / trial_data / trial_field / previews，`__init__` 统一再导出，消费方零改动，专项集成测试 38 例通过）；**前端** `TrialPanel.vue`（561 行）→ `useTrialPanel.ts`（237 行）+ 纯工具 `utils/trial.ts`，SFC 降至 **326 行**，新增 6 例纯函数单测；存量基线 **12/3 → 11/2**。验证：pytest 2534 + 覆盖率 87% + 三项门禁、vitest **256**、typecheck/strict、E2E permissions+a11y 双浏览器 20 passed |
| 2026-09-17 | 存量巨型文件拆分第三批（后端 5 个） | `generate_crud.py`（900）→ 命令模块 + `_generate_crud` 实现包（Django 命令发现跳过包目录，入口单文件再导出）；`approval_flow.py`（781）→ 包（constants / conditions / engine / queries / periodic）；`modules.py`（717）→ 包（specs / registry / gate / seeding，`reset_module_state` 跨模块清缓存）；`preview.py`（522）→ 包（constants / media / office）；`modelset/import_export.py`（579）→ 包（celery_utils / export_actions / import_actions / actions）。存量基线 **11/2 → 6/2**（17 → 8）。验证：pytest **2534 passed / 覆盖率 87%** + 行数/跨 app/ruff 全绿；4 处测试补丁点与 2 处私有名再导出随拆分迁移。**剩余**：后端 6（config / data_scope / server-conf / tasks / approval / views.ai）+ 前端 2（bar.tsx / ReCropper） |
| 2026-09-17 | **存量巨型文件清零（第四/五批收官）** | **后端**：`data_scope.py`（585）→ 包（constants / values / compiler）；`tasks.py`（683）→ 包（任务壳留在 `__init__` 保 celery 任务名 + `_export` / `_import` 实现体）；`views/ai.py`（648）→ 包（assistant / knowledge / profiles）；`approval.py`（659）→ 包（constants / approved_actions / payload / approvers / notify / lifecycle / queries / periodic）；`config.py`（670）→ 包（base / system_conf / user_conf）；`server/conf.py`（648）→ 包（defaults / settings_defaults / config / manager，PROJECT_DIR 层级修正）。**前端**：`RePureTableBar/bar.tsx`（578→**494** + utils.ts 111）；`ReCropper/index.tsx`（519→**430** + utils.ts 132）。两端 BASELINE **清空（历史 17 处 → 0）**。验证：pytest 2534 / 覆盖率 87% / 行数与跨 app 门禁、vitest **256**、typecheck+strict、E2E notice+permissions 双浏览器 16 passed、ruff/prettier 全绿 |
| 2026-09-13 | 年度计划 2026.10-2027.09 前六个主项（提前执行） | **G1 LDAP/AD 目录同步**（ADR-017：bind 认证接入认证链、OU→部门树定时同步、冲突审计、值级加密配置）；**G2a 企业 IM 扫码登录**（ADR-018：钉钉/企微/飞书 flavor 适配器 + 官方端点预设，顺带补齐 OAUTH_PROVIDERS 写侧校验接线与默认登录页死 UI 替换）；**G2b IM 消息渠道**（ADR-019：common/sdk/im 三客户端、收件账号按 flavor 复用 OAuth 绑定，顺带修复测试基座 settings pub/sub 跨测试污染的历史 flaky 根因）；**G3a 数据集 + 仪表盘一期**（ADR-020：三层白名单受控查询 + get_filter_queryset fail-closed、四种图表卡片，前端零新依赖）；**G3b 仪表盘二期 + G8 报表轻量版**（ADR-021：Screen 大屏轮播、Report 定时邮件复用下载中心产物）；**G6 出站 Webhook**（ADR-022：9 事件目录、HMAC-SHA256 时间戳签名、指数退避 5 次、耗尽告警、投递审计页）。实测：后端用例 **184 → 1802**（新增约 220 例：LDAP 69 / IM 55 / 分析 50 / Webhook 21 等），覆盖率 **61% → 86.4%**（门禁 78%）；前端 vitest **62 → 185**；E2E **34 → 212**（新增 dashboard/analysis/webhook/ldap/oauth-im 主链路，4 分片并行全量 ~4min）；ADR **16 → 22**；各窗口依赖复审/演练类时点任务按窗口另行执行 |
| 2026-09-11 | 09 中下旬质量尾巴（Q1/Q2 完成、Q3 部分完成） | **Q1 pytest 并行 flaky 治理销项**：复核确认单测库自始 `:memory:`（`b2e1516` 注记防复发），`database table is locked` 文件库口径不可复现；实修 `test_decorators.py`——`django_db` 标记保留（pytest-django DB 屏蔽进程级含后台线程，无标记时 `open_db_connection` 被拦、`_run_func` 静默吞任务，实测复现）+ 固定 sleep 改 `wait_until` 轮询（并发高负载下 0.8s 盲等被打穿实锤 1 例）；`pytest -n auto` 连续 3 轮全绿。**Q2 webkit flaky 排查销项**：全量 fresh 连续 6 轮，config-persist/preview/smoke 全绿；修复 2 例瞬态 flaky——① a11y 扫描撞 el-tree 入场 opacity 中间态 → axe 混色出瞬态 color-contrast（`scanBlockingViolations` 注入 `transition/animation: none` 再扫）；② 并行分片高负载下登录后路由跳转超 15s（`login()` 断言上限放宽 30s）；均登记 e2e/README.md 教训表。E2E 并行跑批落地：工作区新增 `scripts/e2e-parallel.mjs`（4 shard × 独立端口 + `E2E_DB_FILENAME` 独立 sqlite，settings_e2e 已支持），全量 fresh 双浏览器 11.7min → **~4min**，3 轮并行验证 190 例全过。**Q3 Renovate 部分完成**：`renovate-config-validator` 双仓通过；本地 dry-run（`--dry-run=full`、真实仓库）链路验证成功（server 提取 13 文件/81 依赖）；**阻塞**：renovate.yml+renovate.json 仅在 dev，main 缺失 → dispatch 404/cron 不生效（随例行合入 main 解决）+ `RENOVATE_TOKEN` secret 待配置；就绪后 dispatch 补验收 |
| 2026-09-10 | N3 第三/四期交付收口（脱敏闭环 / PAT 精确审计 / 审批运营 / 文件中心二期 / 质量门禁） | server **1018 例**（实测覆盖率 **81%**，CI 门禁 70→**75**）/ client vitest **138 例** / E2E **27 spec（83 unique × chromium+webkit = 166 passed / 0 failed / 0 flaky）**，全量 10.2 分钟。本期主体：① **字段级脱敏闭环**（编辑态走 `?mask=false` 原文通道 + 端到端断言；修门禁「按菜单主键比对」导致的通道恒关缺陷，另修 E2E 种子字段白名单未授详情菜单）；② **PAT 精确审计**（`OperationLog.auth_type/token_pk` + 索引，`logs/stats` 改按凭证精确归集；IP 白名单；scope 支持 `METHOD /path`；ADR-008 销项两项演进，含 token_pk 类型对齐 bigint 的计划纠偏）；③ **审批中心运营增强**（批量驳回/待办角标/统计/超时提醒；修「批量按钮因缺 selection 列恒不可用」「待办计数缓存无写失效」「待办口径含本人发起」三处）；④ **文件中心二期**（磁盘引用删除守护 → md5 前置 + 上传去重 → `FILE_KEEP_DAYS` 保留期清理）；⑤ **质量门禁**：服务端 po 全译硬门禁（700 条 0 漏翻）、列表 ViewSet 默认排序扫描门禁（修 `SettingViewSet` 真实缺口）、prettier 覆盖扩到 `e2e/scripts/根配置`、E2E 复用口径默认关闭。期间修复 1 例真实 flaky（登出后二次登录时站点配置瞬态为空 → 登录页渲染「当前服务器不允许登录」，`login()` 改为等不到账号框时重载一次重试） |
| 2026-09-11 | N3 五期 F5 质量收尾 | **覆盖率门禁 75→78**（实测 82%；ws.py 66→95%、ws_monitor.py 49→95%，+25 例 `test_ws_consumers.py`）；**handle.tsx 789→273 行**（拆 handle-dialog 319 / handle-record 209，导出签名不变，typecheck/eslint/vitest 138 例全绿）；**越权矩阵 +14 例**（M18-M29：OAuth 绑定 / 导入模板 / 文件预览 / 记录 stats / 导出下载与错误报告越权）；**缓存键审计** `scripts/check_cache_keys.py --strict`（R1/R2/R4/R5 通过、R3 冲突 0）+ [cache-keys-audit.md](cache-keys-audit.md)（含 JWT 审计口径）；字段权限「详情菜单白名单」部署前提：前端详情空白提示（`plus.detailBlankTip`）+ 文档章节；**ADR-010**（crypto-es 停留决策，TD-28 收口）；审计复查 pnpm audit --prod（官方源）= **0** / pip-audit = **0**；备份演练记录已在 `ops/backup-drill-2027-03.md`（2026-09-08 执行：异地副本 + 媒体入包 + RPO 6h，RTO 0.89s，53 表一致） |
| 2026-09-11 | N3 五期 F2/F3 交付（文件预览 + 身份联邦） | **server +45 例**：`test_upload_preview.py` 17（类型分派/越权/按需生成幂等/缓存三条回收路径）+ `test_oauth.py` 28（登录后置链路单一入口 MFA 回归、state 一次性、绑定唯一、auto_create、解绑自锁防护、配置校验、stub IdP 异常分支）；client vitest 138 例；E2E **31 spec = 190 passed / 0 failed（11.9m）**。F2：预览缓存目录 `MEDIA_ROOT/preview_cache/<pk>/<size>.jpg`（与源文件一对一可推导）+ 硬删联动/孤儿/保留期三条回收 + `auto_clean_preview_cache_job`（beat `4 3 * * *`），并发生成用 cache 锁 + 原子替换，预览类型由后端单一判定并随序列化器下发 `preview_kind`，配置四件套（种子 `7b19`~`7b1c`）。F3：`complete_login` 登录后置链路唯一收口（本地链路改造零行为变化）、`OAUTH_PROVIDERS` 三件套（种子 `7b1d`）、`UserOAuthBinding` + 迁移 0006、authorize/callback/绑定解绑全套 + 前端登录入口/回调页/个人中心页签。**两处坑**：新增模型未导出到 `system/models/__init__.py` 会诱导出 `DeleteModel` 迁移（已修）；回调地址必须携带 provider 且与换 token 用同一份 redirect_uri |
| 2026-09-10 | N3 五期 F1 导入列映射与模板管理 | **server +38 例**（`test_import_mapping.py` 21：列映射纯函数与解析器接入；`test_import_template.py` 17：模板 CRUD/权限/三入口 template_id 与 mapping）/ client vitest **138 例** / E2E **28 spec（新增 `import-mapping.e2e.ts` 3 例）= 全量 175 passed / 1 flaky（既有 webkit）/ 0 failed（11.0m）**。主体：① `common/core/import_mapping.py` 纯函数（归一化等名候选 / `apply_column_mapping` / `resolve_headers`）作为**三入口唯一映射实现**，在文件解析器解析表头处统一生效，空字段名行数据在 `generate_data` 即丢弃；② `system.ImportTemplate`（迁移 0005，`(model,name,creator)` 唯一 + 共享/个人两档取值域，共享仅超管可维护）+ `import-headers` 动作（资源级权限 fallback 补 `headers`）+ 4 条菜单/权限种子；③ 前端上传弹窗内联列映射面板（等名候选提示 / 忽略该列 / 保留未映射列开关 / 模板下拉与另存为），改动映射即解绑模板，`template_id` 与 `mapping` 互斥下发；④ `import-validate` 响应补 `field_titles`（字段名→原表头）与 `unmatched_columns`，错误行按源文件列名展示。已知边界与口径调整见阶段计划「执行记录」 |
| 2026-09-08 | 下期 N5 提前（压测回归基线） | **性能防退化门禁落地**：`loadtest/baseline.json` 基线快照（六用例 P95/RPS + 环境元数据 + 容差）+ `loadtest/check_baseline.py` 比对脚本（P95 1.2x / RPS 0.8x / 错误率 1% 三项判定，支持 `--update` 刷新快照与 `--format md` 报告）+ k6 结果结构增强（rps/duration_ms/http_reqs + 自定义 Trend 分档，修 04 三变体混跑被 `_all` 拉平漏报）+ `.github/workflows/perf.yml`（夜间/手动，PG/Redis service + gunicorn 生产同参，CI 断崖档跳过 RPS、P95 容差 3x）+ 17 例单测；server 覆盖率 78.03%（门禁 75%）/ client vitest 121 例 / E2E smoke 7 例 24s + a11y 双浏览器 4 例全绿。**附带发现（既有，非本次引入）**：`pytest -n auto` 全量并行下 `test_mfa_api` / `test_decorators` 偶发失败（sqlite 文件库 `database table is locked`，后台 delay 线程与测试争锁），暂存改动复跑同样失败、串行全绿，待独立处理 |
| 2026-09-08 | 下期 N2 收尾（a11y 门禁） | client `e2e/a11y.e2e.ts`（axe-core，登录页 + 用户管理页，wcag2a/2aa 的 critical/serious 阻断）+ `@axe-core/playwright` 依赖 + 三处修复（登录页记住天数 select 标签、第三方登录图标 aria-hidden、用户列表头像 alt 兜底）+ `docs/accessibility-audit.md` 豁免登记明细；首次双浏览器运行暴露 `color-contrast` 命中 `.el-link--primary`（与 el-button 同取主题色），白名单补 `el-link` 后转绿 |
| 2026-09-07 | TD-25 根因修复（ADR-006） | **ASGI 每请求新建 DB 连接销项**：psycopg2-binary→psycopg[binary,pool] 3.2.13 + Django server 端连接池（OPTIONS.pool 默认开启，DB_POOL/MIN/MAX_SIZE 可配；池模式 CONN_MAX_AGE 归零 + CONN_HEALTH_CHECKS）；pgbouncer 否决（新增组件 + 语义陷阱，见 ADR-006）。**before/after 压测**（k6 routes 20VU/1m，专用隔离环境，gunicorn UvicornWorker×4 生产同参）：失败率 20.33%（28,732/36,066 @599.5rps，PG 连接 3↔289 震荡）→ **0%（62,780/62,780 @1,042.6rps，PG 连接 13↔25 稳定）**；P50 31.3→20.2ms（-35%）、P95 53.7→32.5ms（-39%）。门禁：pytest 670 + ruff 全绿（sqlite 不受影响）；T3.1 基线表中 routes 数字为 before 口径，TD-25 修复后整体改善 |
| 2026-09-07 | P5 T5.1 二期（client 依赖升级） | **官方源 pnpm audit 59（32 high）→ 0**：直接依赖升级（vue3-ts-jsoneditor 3.3.0→3.4.1、@playwright/test 1.63.0、eslint 10.10.0、eslint-plugin-vue 10.11.0、lint-staged 17.5.0、stylelint 17.15.0、@types/node 20→24 对齐 CI node 24）；21 个传递依赖漏洞经 pnpm-workspace.yaml `overrides` 范围选择器强制修复线（pnpm v11 已不读 package.json 的 pnpm.overrides 字段；同包多条目仅一条生效，esbuild 收敛为全局单条 →0.28.2）。crypto-js 评估结论：**保留 4.2.0**——`AesEncrypted` 是前后端线上协议（server AESCipherV2 解密 "Salted__" OpenSSL 格式），替换须两仓联动协议版本化，且 4.2.0 无 CVE；弃用风险登记待办。门禁全绿：vitest 108 / typecheck / eslint（升级暴露 ReSendVerifyCode modelValue default 工厂 1 例已修）/ stylelint（playwright-report 产物入 ignoreFiles）/ build / E2E 74 例双浏览器 3.1 分钟。**附带发现：主 chunk 790KB gzip（T3.4 基线 440KB）**——归因为 09-05 后 MFA/回收站/412 拦截器等功能增量（53 文件 +2829 行），非依赖所致，登记 TD-27 优化项 |
| 2026-09-07 | 优化升级文档遗留收口 | 后端 pytest 653→**668**（user/menu 回收站 11 例 + 路由缓存种子适配修复，迁移 0009/0010）；前端 vitest 105→**108**（serverErrors 3 例）；**E2E chromium 全量 36 例 1.7 分钟本地全绿**——根因修复：E2E 默认端口 8896 与 compose nginx 冲突致 reuseExistingServer 误挂（改 18896）；附带修复 UX-3 强刷白屏（getAsyncRoutes 豁免路由取消 + 首次导航不取消）与 FEAT-3 密码规则误校密文（改校验明文）；FEAT-1 补 E2E 2 例（含启停确认链路） |
| 2026-09-06 | P3 T3.1 实测回填 | 六接口三轮中位数入 §五（全部 0 错误率）；k6 v2.0.0 适配与三条方法论坑记入 performance-baseline.md：① macOS zsh `USERNAME` 魔法参数须 `env` 注入；② k6 v2 移除 group 子指标，元数据三变体改自定义 Trend 分档；③ **发现 TD-25**（ASGI 形态每请求新建 DB 连接、CONN_MAX_AGE 失效、psycopg2 无池，~600rps 端口耗尽 13-27% 500），压测容器 `tcp_tw_reuse=1` 缓解后 0% 失败完成测量。server 用例数 579→609（MFA 30 例，aefb7ce CI 绿）；E2E 双浏览器 68 例全绿（本地 2.8m + CI 8m54s） |
| 2026-09-06 | P4 T4.3/T4.4/T4.5 验收 | **E2E 双浏览器全绿：34 用例 × chromium+webkit = 68 passed / 0 failed（retries 1），2.8 分钟**，T4.3/T4.4 销项，T4.5 待 CI 首跑。验收期修复（详录见规划 §六任务表）：① settings_e2e sqlite 开 WAL+busy_timeout+IMMEDIATE（修 database is locked）；② 补 CELERY_* eager+memory broker（原缺失致 apply_async 连 rabbitmq 拖死 daphne）+ Inspect 探针返回无 worker（memory broker control 命令无限阻塞会拖死 thread-sensitive 单线程）；③ 种子：FieldPermission 改绑 GET 菜单、e2e_dp 补字段白名单、e2e_fp 补 value.all 数据授权（数据权限/字段权限均默认拒绝，白名单为空=全裁剪为既定语义）；④ 用例：用户配置 JSON 编辑器键盘输入、导入 CSV 补 password 列、受限用户菜单 #/system 导航、WS 推送场景改聊天 @提及 |
| 2026-09-06 | P4 T4.3/T4.4/T4.5 开发完成待验证 | E2E 5→**29 用例**（新增 lockout 3 / permissions 6 / import-export 3 / notice-push 1 / config-persist 1，双浏览器 chromium+webkit）；环境固化 sqlite+FakeRedis+eager celery 零外部依赖，端口/路径全参数化（E2E_API_PORT/E2E_FRONT_PORT/E2E_SERVER_DIR），vite 代理支持独立后端端口；种子脚本产出数据/字段权限场景（shell 验证：e2e_dp 仅见本人、e2e_fp 无 email 列）；e2e.yml 修复双仓检出布局（E2E_SERVER_DIR）；按用户指示本轮未执行 E2E，验收待跑通后回填 ✅ |
| 2026-09-06 | P5 前置（T5.1 一期/T5.3/T5.5/T6.1-T6.4） | server pip-audit 45→**0**：django 5.2.9→5.2.17、DRF 3.16.1→3.17.2、daphne 4.2.1→4.2.3、pyzipper 0.3.6→0.4.0、requests 2.32.5→2.33.0、openpyxl 3.2.0b1→3.1.5（TD-18）；client pnpm audit 实测 40（20 high，全传递依赖，留窗口）；Flower 认证配置化收尾（T5.3）；trivy 阻断 + SBOM 随 release（T5.5）；docs/ 知识库成型（T6.1/T6.2）；openapi.json 随 release（T6.3）；CONTRIBUTING+PR 模板两仓（T6.4）；T4.6 复核验收通过；误删的 exception-handling.md 自 git 历史恢复 |
| 2026-09-05 | P4 T4.1 | server 门禁 65→75（实测 75.01%）；用例 412→579：新增 test_conf/test_decorators/test_parsers/test_celery_utils/test_modelfield/test_pending/test_common_utils/test_cache_redis/test_db_utils/test_media_serve 共 10 模块；附带修复操作日志中间件动词 getattr 无兜底缺陷（TD-24） |
| 2026-09-05 | P4 T4.2 | client 用例 86→97：新增 RePlusPage registry.spec（7 例，registry.ts 覆盖率 100%）+ renders.spec（4 例）；vitest.config 接入 vueJsx/Icons/i18n 插件；coverage include 扩到 registry/renders；CI lint-code.yml 切换 test:coverage 强制阈值（statements 50/branches 42/functions 38/lines 50）；修复 user.spec SET_LOGINDAY 类型错误 |
|------|------|----------|
| 2026-09-06 | P3 T3.1 开发侧准备 | django-silk 接入 dev（`SILK_ENABLED` 配置开关，仅 DEBUG/DEBUG_DEV 生效默认关闭，requirements-dev 新增 django-silk，/silk 面板登录+员工权限）；k6 六接口压测脚本（loadtest/k6：登录/路由/列表/元数据×3/导出/导入，全参数环境变量化，按 group 拆分 P50/P95 落盘 results/）+ 种子脚本 seed_users.py + 流程文档 docs/ops/performance-baseline.md；基线实测待运行环境，回填 §五占位表 |
| 2026-09-04 | P1 建立 | 全部基线首次实测登记；server 覆盖率 55%门禁→实测 61%；契约层收口 21→10 |
| 2026-09-04 | P1 T1.7 一期 | any 豁免清单 71→40（目标 ≤40 达成）；受治理文件 32 个全部通过 vue-tsc/eslint/vitest |
| 2026-09-04 | P1 T1.7 二期 | 豁免清单 40→36；tree.ts/tree.spec.ts/localforage/types.d.ts/handle.tsx 根因类型化（泛型化树工具、弹层列契约 RawColumn/EditableColumn、ApiResult 请求契约、ExposedFormInstance 表单实例契约）；any 存量 226→139 处 / 91→62 文件 |
| 2026-09-05 | P1 T1.7 三期启动 | 豁免清单 36→34；renderers-detail.tsx 消除最后 2 处 any（unknown + ChoiceOptionItem 契约），renderers-form.tsx 经核查已清零摘除 |
| 2026-09-05 | P1 T1.7 三期续 | 豁免清单 34→33；RePureTableBar bar.tsx 表格实例契约化（ExpandableTableInstance/TableRowLike，$refs 断言改实例形状） |
| 2026-09-05 | P1 T1.7 三期续 | 豁免清单 33→32；RePlusSearch hooks.ts 选择值类型守卫化（isArray/typeof+in 守卫替代 as any 断言，新增 SelectionRow 领域类型） |
| 2026-09-05 | P1 T1.7 三期续 | 豁免清单 views 系 14 文件全清（32→18）：小型文件 8 个（onGoDetail 行契约/autocomplete cb/user-info 选项形状）、system hooks + dept（OptionsItem/RolePermissionItem/FormStateProps/DeptRow）、notice + menu 系（SelectOption/NoticeReadRow/OwnerRow/NoticeRow/ChoicesOptionItem/MenuUrlItem/ModelTreeItem）；views 系合计消除显式 any 33 处 |
| 2026-09-05 | P1 T1.7 三期续 | 豁免清单 18→16；api/auth.ts 新增 PasswordRule/ChoiceEntry 契约（修正 userinfo choices_dict 的数组语义）；router/index.ts glob 泛型化 + remainingRouter 边界断言（vue-router 5 联合判定限制），remaining.ts 补 RouteConfigsTable 注解 |
| 2026-09-05 | P1 T1.7 三期续 | 豁免清单 16→14；utils/index.ts 新增 MenuTreeNode 树行契约（getMenuOrderPk 入参 unknown 保留非数组受测行为）；websocket.ts onMessage 回调收敛为 (data: unknown) => void，chat 消费侧单点收窄到 MessageProps |
| 2026-09-05 | P1 T1.7 四期（收官） | 豁免清单 14→0 全部清零摘除（ReDialog/ReDrawer 弹层契约化、http 参数袋、表格行动态边界等，见 client eslint.config.js 注释）；any 存量 139→64 处 / 62→34 文件；.ts/.tsx 清零，剩余 64 处全部位于 .vue —— 发现规则覆盖缺口：no-explicit-any 规则块仅挂 *.ts/tsx，*.vue 不受约束 → 登记 TD-23，随 P4/T4.6 清零 |
| 2026-09-05 | P3 T3.4 | vite advancedChunks 四组分包 + echarts 启动预热懒加载；首屏 1,020.5→905 KB（-11.3%），主 chunk 699→440 KB（-37%）；修正基线口径（旧值漏算静态依赖闭包）；告警阈值 4000→1000 |
| 2026-09-05 | P2 T2.5 | 三个巨型文件拆分：lay-setting 683→44+9 文件、lay-tag 711→257+3 hook、user hook 604→107+5 composable；全部 ≤400 行、纯搬迁零行为变更；另修复 client CI regen 门禁路径（schema 镜像入库 contract/schema）与 build-image.yml 幽灵 job 引用 |
| 2026-09-05 | P2 T2.3/TD-23 | 元数据 Schema 固化 + 服务端契约测试 6 例 + client 类型生成/CI regen 门禁；any 治理五期：eslint 规则覆盖 *.vue，存量 64 处清零，全仓显式 any 归零（vue-tsc/eslint 0 警告/vitest 全绿） |
| 2026-09-05 | P2 T2.1/T2.2/T2.4/T2.6 | modelset.py 749 行拆为 10 模块包（各 ≤300 行）+ 22 例边界护航测试；契约层收口 10→4 处 + scripts/check_cross_app_imports.py CI 门禁（lint.yml）；通知系统 @register_message 显式注册 + 后端渲染集中注册 + 6 例单测；三层权限架构文档落地；server 用例数 350→378 |

## 四、与半年规划 KPI 的对照

| KPI（§八） | 基线 | 目标（2027-02） |
|------------|------|-----------------|
| server 覆盖率门禁 | 55%（实测 61%） | 75% |
| server 用例数 | 184 | 260+ |
| client 用例数 | 62 | 120+ |
| E2E 入 CI | 0 | 15+ |
| 跨 app 坏味道 | 10 | ≤5 |
| any 豁免文件 | 71 | 0 |
| 首屏主 chunk gzip | 698.8 KB（旧口径）→ 真实首屏 1,020.5 KB | -20% | **主 chunk 440 KB（-37%）✓；真实首屏 905 KB（-11.3%）**，剩余路径：element-plus 按需引入 |
| pip/pnpm audit 高危 | 周检中 | 0 |

## 五、性能基线（P3/T3.1，2026-09-06 首测完成回填）

> 流程与口径见 [ops/performance-baseline.md](ops/performance-baseline.md)；三轮中位数、k6 客户端口径（含本机回环）。

**环境元数据**：Apple Silicon macOS + OrbStack Docker；被测服务 gunicorn + UvicornWorker×4（生产同参，容器化，代码 = aefb7ce）；PostgreSQL 16.8 / Redis 7.4.3 专用一次性容器；种子 1000 个 `perf_` 用户 + 默认数据；k6 v2.0.0；压测容器加 `tcp_tw_reuse=1` + 扩大临时端口段以规避 **TD-25（ASGI 每请求新建 DB 连接）** 引发的端口耗尽——每请求连接开销保留在下列数据中，符合生产现状，TD-25 修复后全体数字将进一步改善。

| 接口 | 档位（默认） | RPS | P50 | P95 | 错误率 | 实测日期 | 环境 |
|------|-------------|-----|-----|-----|--------|---------|------|
| 1 登录（login/basic） | 5 VU / 30s | 31.3 | 159.1ms | 173.9ms | 0 | 2026-09-06 | 首测环境① |
| 2 菜单/路由（routes） | 20 VU / 1m | 682.0 | 26.8ms | 61.8ms | 0 | 2026-09-06 | 首测环境① |
| 3 列表页（user list） | 20 VU / 1m | 267.4 | 66.2ms | 147.5ms | 0 | 2026-09-06 | 首测环境① |
| 4 元数据（search-columns） | 20 VU / 1m | ≈34.8* | 46.4ms | 81.5ms | 0 | 2026-09-06 | 首测环境①② |
| 4 元数据（search-fields） | 20 VU / 1m | ≈34.8* | 43.4ms | 78.3ms | 0 | 2026-09-06 | 首测环境①② |
| 4 元数据（list?with_meta=1，T3.2 内联） | 20 VU / 1m | ≈34.8* | 97.6ms | 203.8ms | 0 | 2026-09-06 | 首测环境①② |
| 5 导出（export-data xlsx，username=perf_ 过滤） | 5 VU / 1m | 15.4 | 253.0ms | 632.6ms | 0 | 2026-09-06 | 首测环境① |
| 6 导入（import-data update 模式） | 5 VU / 1m | 130.8 | 39.1ms | 48.5ms | 0 | 2026-09-06 | 首测环境① |

\* 三变体同循环等比混跑（04 合计 104.5 RPS）；P50/P95 为分档 Trend 指标三轮中位数（k6 v2 移除 group 子指标，改用自定义 Trend 分档）。

**T3.2 对照结论**：内联单请求（P50 97.6 / P95 203.8ms）明显优于「分离列表+columns+fields 三请求串联」（P50 简单相加 ≈156ms、P95 ≈226ms，另有两次额外往返），配合首开请求数 -2，T3.2 优化收益得到基线数据支撑。

回填要求：记录环境元数据（机器规格 / gunicorn worker 数 / DB 引擎与版本 / 种子规模）；元数据 P95 需同时
登记分离请求与 with_meta=1 内联两组，用于对照 T3.2 目标（较基线 -50%，目标 <150ms）。

### 2026-09-14 AI 检索评测（A1，评测驱动门控）

评测集 `tests/data/ai_retrieval_eval.json`（36 问 + 期望出处，含 6 条改写式难题）随
`pytest tests/integration/system/test_ai_retrieval_eval.py` 入 CI（含「期望出处有效性」守护与耗时护栏）。

| 指标 | 实测 | 门控 | 判定 |
|------|------|------|------|
| hit@5 | **35/36 = 97.2%** | < 75% 才升级向量 | 不升级（[ADR-037](adr/ADR-037-ai-retrieval-evaluation.md)） |
| 知识库分块数 | **535** | > 500 触发评估 | 略超（+7%），质量与时延均达标 → 维持 |
| 单次检索耗时 | 平均 30.3ms / 最差 31.6ms | P95 > 300ms 重估 | 达标 |

结论：词频重叠检索在现语料规模下质量充分，**不引入向量嵌入**；重开条件（hit@5<75% / 分块>1000 /
检索 P95>300ms / 明确语义检索需求）见 ADR-037。

## 依赖安全审计（W4，2026-09-12）

| 仓库 | 工具 | 结果 | 备注 |
|------|------|------|------|
| xadmin-client | pnpm audit（registry.npmjs.org） | 0 known vulnerabilities | 国内镜像不支持 audit endpoint，需 --registry 指官方源 |
| xadmin-server | pip-audit 2.10.1 | 0 known vulnerabilities | 全量依赖（含 venv）扫描 |

### 2026-09-12 首屏基线复测（下期规划 W3 收口）

| 指标 | 09-05 基线（T3.4 后） | 09-12 实测 | 变化 |
|------|------|------|------|
| **首屏 JS 静态闭包合计（gzip9，dist/index.html modulepreload 闭包）** | 12 chunks / 905.0 KB | 8 chunks / **861 KB** | **-4.9%**（-44 KB） |
| 主 chunk `index-*.js` | 440 KB | 466 KB | +26 KB（审批流二期 @vue-flow 画布/版本回滚等新应用代码入包） |
| `element-plus` | 279 KB | 234 KB | -45 KB |
| `echarts` | 懒加载 | 懒加载（346 KB 独立 chunk，不在闭包） | 维持 |
| 闭包其余构成 | — | vue-core 86 / i18n 51 / plus-pro 13 / 杂项 7 KB | — |

**结论（W3 收口）**：-10% 目标（815 KB）未达成。echarts 懒加载已生效、element-plus 已按需注册，闭包唯一实质杠杆是 index 应用代码（466 KB，含 ReIcon/data.ts 3,869 行图标数据）——**ReIcon 瘦身立项价值确认**，但预期收益需先按 data.ts 在闭包中的实际压缩占比评估后再决策（候选池口径不变）。

### 2026-09-12 ReIcon 瘦身实测（候选池触发条件核验，结论：不立项）

`src/components/ReIcon/data.ts` 实测 3,869 行 / 79.7 KB raw / **15 KB gzip9**，占首屏闭包（861 KB）的 3.2%；
唯一消费点为 `ReIcon/src/Select.vue`（菜单表单图标选择器）。整块剥离的收益上限为首屏 -1.7%，
且需把图标选择器异步化——**数据不支持立项，候选池该项关闭**（首屏优化的剩余空间不在数据文件）。

### 2026-09-14 首屏 KPI 改「增长预算制」+ wangeditor 拆包（W0 收口）

**KPI 新口径（替代绝对 -10%）**：每个窗口首屏闭包（gzip9，`dist/index.html` 静态依赖闭包）增长 **≤15 KB**；
超预算需在 PR 说明理由并刷新基线。执行器 = `scripts/check-bundle-size.mjs` + `scripts/bundle-size-baseline.json`，
已进 CI `.github/workflows/lint-code.yml`（`pnpm build && pnpm check:bundle-size`）。

| 指标 | 09-12 基线 | 09-14 拆包前复测 | 09-14 拆包后 | 变化 |
|------|-----------|-----------------|-------------|------|
| **首屏 JS 静态闭包（gzip9，9 chunks）** | 861 KB | 881.7 KB | **557.5 KB** | **-324.2 KB（-36.8%）** |
| 主 chunk `index-*.js` | 466 KB | 518.8 KB | **194.6 KB** | -62.5% |
| element-plus / vue-core | 234 / 86 KB | 235.6 / 86.4 KB | 235.6 / 86.4 KB | 维持 |

**拆包决策（数据来源：`pnpm analyze:bundle`，rollup-plugin-visualizer raw-data，renderedLength 口径）**：

| 候选 | 实测结论 | 动作 |
|------|----------|------|
| `@wangeditor`（编辑器栈） | **1044.8 KB rendered 落在主 chunk**（App.vue 静态 `Boot.registerModule` 把插件+核心拖入入口闭包，与 renderers-form 注释「编辑器全懒加载」的设计意图相反） | **立项并交付**：注册迁至 `src/utils/wangEditorBoot.ts`（幂等懒注册，含 rolldown UMD 解包兼容），WangEditor.vue / NoticeShow.vue 改「先 await 注册、再加载编辑器组件」的异步组件 |
| `version-rocket`（版本更新提示） | 129.7 KB rendered 在主 chunk（含 119.4 KB 主题） | **立项并交付**：App.vue 改动态 `import()`，5 分钟轮询行为不受影响 |
| `@vue-flow` | 已在独立懒加载 chunk（FlowCanvas 50.2 KB gzip，不在闭包） | 确认无需动作 |
| `plus-pro-components` | 闭包内仅 13.2 KB gzip（97 KB rendered） | 收益低于预算量级，不立项 |
| ReIcon/data.ts | 15 KB gzip，占闭包 3.2% | 维持既有「不立项」结论 |

**剩余杠杆（登记为后续候选，均未达立项阈值）**：i18n zh/en 语言包 ~49 KB gzip（需 i18n 懒加载改造，影响面大）、
`@zxcvbn-ts` 26 KB、`vue-tippy` 24 KB、`sortablejs` 17 KB（3 处消费需动态导入）、`vue-json-pretty` 9.6 KB。

**验证**：拆包后主链路 E2E（notice 创建/编辑、NoticeShow 只读）双浏览器通过；`pnpm analyze:bundle` 复测确认
闭包内不再含 wangeditor chunk。

### 2026-09-15 首屏三期余量（i18n / sortablejs / vue-json-pretty，W7–W10 收口后的候选池项）

基线口径：560.6 KB（W7–W8 收口后）→ 本轮实测 **519.6 KB（-41.0 KB / -7.3%）**，主 chunk 151 → 130.5 KB。

| 项 | 改造 | 结果 |
|------|------|------|
| `sortablejs`（列拖拽排序） | `RePureTableBar/bar.tsx` 静态 import 改为「列排序」交互触发时动态 `import()`（类型经 `import type` 保留） | 移出闭包 |
| `vue-json-pretty`（JSON 展开 + 样式） | `RePlusPage/utils/renderers-detail.tsx` 改 `defineAsyncComponent` + 动态 import CSS（Vite 产出独立 css chunk，加载时注入） | 移出闭包（JS + CSS 双份） |
| i18n 语言包（en.yaml + element-plus en locale） | `plugins/i18n.ts`：zh-CN 保持 eager（默认语言/源语言，`flatI18n` 同步探测依赖）；en 由 `ensureLocale()` 按需加载（`app.mount` 前按初始语言 + 语言切换处 + `watch` 兜底防止 key 泄漏），新增 `e2e/locale.e2e.ts` 双浏览器守护（切换往返 / 落库后刷新首屏英文） | **-20.8 KB**（540.4 → 519.6） |
| `@zxcvbn-ts` | 复测：已在独立懒 chunk（3 处均为懒视图消费），**与候选池登记值（在闭包）不符 → 无需动作** | 无变化 |
| `vue-tippy`（~24 KB gz） | 评估：`app.use(VueTippy)` + 指令式用法散布布局层（侧栏 tooltip 属首屏交互），移出闭包需重写指令为异步实现或降级 tooltip | **评估后维持**（收益低于风险，登记同 ReIcon 范式） |

已核实为懒加载 / 不在闭包（无需动作）：echarts、wangeditor、version-rocket、`@vue-flow`。

### 2026-09-17 前端体验基线首测（U1，chromium + dev 链路）

| 页面 | TTFB | FCP | LCP | CLS |
|------|------|-----|-----|-----|
| 登录页（冷加载） | 332 ms | 1172 ms | 1224 ms | 0.016 |
| 首页（登录后整页重载） | 3 ms | 284 ms | 284 ms | 0.020 |
| 用户列表（登录后整页重载） | 3 ms | 268 ms | 268 ms | 0.048 ~ 0.240（波动） |

- 采集：`pnpm test:e2e:perf`（刷新基线加 `:update`）；基线文件 client `e2e/perf-baseline.json`，
  按「平台-CI」分组存放（数字随机器变化，**仅同环境趋势可比**）；
- 口径：dev 链路（vite）+ 本地机器；现阶段只做「离谱回归」兜底（TTFB ≤ 2s / LCP ≤ 5s / CLS ≤ 0.5），
  正式预算待数据积累后评审（U1 阶段一目标：先立基线后定预算）；
- 发现：用户列表页 CLS 在 0.048 ~ 0.240 间波动（异步数据填充引起），高于 0.1「良好」线
  → 登记体验改进候选（待稳定口径复测后再评估）。

### 2026-09-18 列表页 CLS 收口（U1，修复后复测）

| 页面 | TTFB | LCP | CLS（修复前 → 修复后） |
|------|------|-----|----------------------|
| 用户列表（登录后整页重载） | 33 ms | 276 ms | 0.2403（波动 0.048~0.240）→ **0.0214** |
| 登录页（冷加载） | 327 ms | 1320 ms | 0.0153（页面级小位移，维持） |
| 首页（登录后整页重载） | 72 ms | 308 ms | 0.0141（页面级小位移，维持） |

- 根因（`cls_top` 几何明细定位，2026-09-18）：①搜索列元数据（列表首包 `with_meta=1`）
  到达前搜索卡片只渲染按钮行（56px），到达后叠加字段行（+50px），把下方表格/分页整体推移；
  ②el-table 列宽在 `requestAnimationFrame(doLayout)` 内才落位，列挂载后第一帧仍是浏览器
  均分宽度（长表头换行，实测表头 155px → 41px、行高 86px → 53px）——两件事同帧，
  中间帧被真实绘制产生大位移（单次 shift 占 CLS 的 88%）；
- 修复（client `RePlusPage`）：搜索卡片 `min-height` 高度占位（元数据到达前后卡片高度一致）
  + 列集合变化时隐藏表格两帧（`visibility: hidden` 不参与 layout-shift 统计）；
- 剩余位移为登录页表单/布局页脚等页面级小项（各 ≤ 0.008），登记后续候选。
