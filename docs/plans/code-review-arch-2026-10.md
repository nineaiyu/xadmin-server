# 2026-10 架构与扩展性审查 · 主册

> 系列第四册（前序：[全栈功能审查](code-review-2026-10.md) / [前端专项审查](code-review-frontend-2026-10.md) / 交付链路审查）。只读审查产物，不改源码。
> 问题编号 `arch-<级别>-<序号>`；证据等级 A=代码直接确证、B=跨文件静态推断、C=待运行时验证。
> 执行方式：主会话（重构候选，基于前两轮审查上下文）+ 3 子代理并行采集（依赖图/三方治理/二开矩阵），主会话汇编与抽核。

## 0. 审查说明与审查基线

- **范围**：后端 app 边界与跨 app 依赖、内核包职责、视图/领域/任务分层、前端五层分层、部署拓扑与裁剪边界、重构候选与路线、三方组件治理、二开能力矩阵、目标架构演进。
- **方法**：跨 app import 矩阵（口径：14 app 顶层 `from X.y`/`import X.y`，排除 migrations/tests，共 **84 条有向边/403 条语句**）；行数/函数门禁基线复跑；只读审计实跑（`pnpm audit --prod` 官方源成功、`check-color-literals` 等门禁复跑）；文档逐篇核对。
- **审查基线**：server `539b9194`、client `d015b6db`（两仓均有暂存与未暂存并行改动，结论以读取时点为准）。
- **总体判断**：架构底盘**不需要推倒型重构**——后端行数/函数门禁基线双 0（962 文件全 ≤500 行、0 个 ≥100 行函数；前端 1944 文件同口径）、内核→业务反向依赖 0、前端初等环 0、横向依赖已收口到 contracts/services 且有活门禁。需要处理的是 **4 项「必须」级结构性收口 + 6 项可选优化**，多数是「把已证明正确的模式从个案推广为机制」。

## 1. 现状架构与依赖

### 1.1 后端 app 边界与跨 app 依赖

**依赖矩阵**（≥5 条语句的边；行=importer，列=importee）：

| ↓ \ → | identity | system | audit | ai | message | task | settings | approval | integrations | mfa | dataset | file |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| system | **37** | - | 7 | 5 | 2 | 10 | 8 | 4 | 1 | 2 | 1 | 9 |
| identity | - | **19** | **22** | 1 | 4 | 8 | **14** | 1 | - | **13** | - | - |
| ai | 5 | 5 | 8 | - | 4 | - | 1 | 2 | **16** | - | 5 | - |
| message | 13 | 2 | 2 | **17** | - | - | - | - | 3 | - | - | 5 |
| settings | 12 | 2 | 1 | 1 | - | 1 | - | - | 6 | - | - | - |
| approval | 14 | 2 | - | 2 | - | 2 | - | - | - | 2 | 1 | - |
| notifications | 8 | - | 1 | 1 | 2 | 2 | - | 1 | 4 | - | - | 1 |
| dataset | 5 | 4 | - | 2 | 4 | 5 | - | **9** | - | - | - | 1 |
| audit | 7 | 2 | - | 1 | 2 | 1 | - | - | - | - | 3 | - |
| task | 3 | - | - | 1 | 2 | - | - | - | - | - | 2 | 2 |

- **双中心形态**：identity/system 是入向双中心（112/41 条语句入边）；典型用途均为正当场景（system→identity 菜单缓存失效与用户搜索；identity→audit 审计写入；message→ai 聊天 AI 助手；dataset→approval 动态表单接审批流 `dataset/utils/dform_flow.py:169`；ai→integrations AI SDK `ai/views/assistant.py:33`）。
- **方向治理有活门禁**：`scripts/check_cross_app_imports.py`（294 行，实测通过）——跨 app 引用必须走 `<app>.contracts`（`.services` 过渡兼容），smell 只盯 models/serializers/views 等契约层坏味道；ALLOWLIST 18 条全部为管理命令入口（demo/models FK + system 管理命令 7 + demo_seed 11），非运行期链路。
- **内核方向单缝**：common→业务 app 只允许经 `common/contracts.py`（PEP 562 惰性提供方表），8 条契约缝登记于 CONTRACT_SEAMS 双向漂移校验；**common 模块级业务 import 实测 0**。9 个 app 各有 `ai_declarations.py` 向 AI 动作注册表反向注册，是唯一的系统性反向注册面（有守护测试，见 §4）。

### 1.2 内核包（xadmin-common）职责

约 26k 行：core 12,158/75 文件（permission 486、url_inventory 339、modelset 组合 2,000、modules 裁剪 1,343、config 1,198、auth 964、data_scope 行级权限编译 1,001、permission_meta 107）、utils 2,571、drf 1,311、celery 694、storage 650、cache 719、api 545、decorators 816；根级 contracts.py 218 / settings_contract.py 471（内核读 settings 的唯一事实源，REQUIRED 键 fail-fast + 静态守护）。发布：`scripts/release_kernel.py` + `docs/architecture/kernel-package.md`（私有源 prerelease 渠道）。**职责边界清晰**：宿主 app 不做权限/元数据/裁剪的重复实现，内核不做业务。

### 1.3 视图/领域/任务分层

- views/serializers 分层全仓一致；**services vs utils 归属不完全统一**：identity（services 8 文件 1,299 行 + utils 19/3,807）与 ai（services 7/6,771，无 utils）以 services 为领域中心，dataset 的领域逻辑主要落 utils/（14/3,533，services 仅 61 行）——同一概念两处归属，新人需要猜（arch-P2-02，可选收敛）。
- Celery：任务文件约定 app 根 `tasks.py` + 子域文件（identity/ldap/tasks.py、dataset/analysis_tasks.py、task/webhook_tasks.py）；路由 `CELERY_TASK_ROUTES`（`server/settings/libs.py:215`）→ `common/celery/routing.py`：BUILTIN 2 条入 heavy 队列，应用可在 `config.py::TASK_ROUTES` 声明改队列——**机制就绪但当前 0 个 app 使用**（不是缺陷，是尚未需要）。容器分工：worker（轻）/heavy（导出/报表/Office 转换，grace 300s）/beat（DatabaseScheduler 单例 replicas:1）。

### 1.4 前端分层

- 量化：views 1,009 文件/112,749 行、components 401/44,126、api 78/66,771（openapi.d.ts 占大头）、layout 154/17,506、utils 179/16,763、hooks 52/4,592、store 26/2,716、router 19/1,647。
- **方向验证**（grep `from "@/…"`）：views→api 465 / →components 356 / →hooks 88 / →store 25；store→components 0；api→上层 0；store→api 仅 5 文件且全为 type/叶子模块（isSuccess）——主方向干净。
- **3 处反向边（无门禁覆盖）**：components→views 2（`ReSendVerifyCode/src/index.vue`→views/login/utils/motion；`RePermissionPreview/src/presets.ts`→views/system/{role,user,post,dept} 预览组件）、layout→views 1（`lay-notice/index.vue`）——不构成环，属架构债（arch-P2-01）。
- **循环依赖：0**——`check-module-cycles` 静态图求 SCC+初等环，EXEMPT/ALLOWED 全空；FORBIDDEN_EDGES 1 条（store 顶层静态引用 api，notice/watermark/siteConfig 均已 `await import()` 化）；首屏闭包另有 entry-closure 白名单门禁。
- **枢纽 Top5**（import 语句数）：`@/api/types` 263、`@/utils/message` 198、`@/components/RePlusPage` 104、`@/router/utils` 103、`@/api/dataset/analysis` 80——扇入集中但单向，属健康枢纽；RePlusPage 是事实上的框架层（见 §2 arch-P1-01）。
- **契约链**：唯一真源 `docs/schema/`（5 schema+openapi.json）→ `sync:contract` 拷贝镜像+再生 TS 类型 → `check:contract` 逐名比对；WS 帧由 `gen_ws_frame_schema.py` 从 `message/ws/ws_schema.py::render()` 生成，`--check` + 逐字节守护测试。

### 1.5 部署拓扑与模块裁剪边界

| 服务 | 镜像 | 要点 |
|---|---|---|
| nginx 1.31.5-alpine | nineaiyu | 纯 L4 stream 代理（变量式 proxy_pass 规避启动期 DNS），生产前端静态由 xadmin-web 镜像承担 |
| postgres | nineaiyu/pgvector:pg17 | max_connections=200；PITR（archive_mode+gzip 归档）；凭据 `${DB_PASSWORD:?}` 缺失拒启 |
| redis 8.10.2 | nineaiyu | broker + cache + channels 层 |
| db-backup | pg17 | 6h 间隔；BACKUP_REMOTE_REQUIRED 生产强制 |
| server | 本地构建 | `manage.py services gunicorn` → **gunicorn + UvicornWorker（ASGI）**（`common/services/gunicorn.py:18-30`）；grace 30s < stop_grace_period 40s；healthcheck /api/common/api/health；USER 1001（`Dockerfile:71`） |
| celery ×3 | 同 image | worker / heavy（300s grace）/ beat 单例 |
- **文档-代码差异（P2 级）**：工作区 `AGENTS.md` §二仍写「daphne / celery 不热加载」，实际 server 运行形态是 gunicorn+UvicornWorker（`common/services/gunicorn.py`）——「改后端必重启」的结论不变，工具名已漂移（arch-P2-05，S）。
- **裁剪边界＝声明面（架构亮点）**：一个 `ModuleSpec`（frozen dataclass：menus/routes/ws_routes/permissions + level/depends，`common/core/modules/specs.py:29`）被三层通道同时消费——HTTP 404（`server/middleware.py:100` ModuleGateMiddleware，404+module 标识+i18n）、菜单/权限点隐藏（`filter_menu_queryset`/`permission_prefixes_of` 进权限查询）、WS 4404（`common/core/modules/gate.py:85-100` ModuleTrimWebsocketMiddleware，close code=4404 语义=通道不存在）。core⊂standard⊂full 预设，full 时零开销旁路。插件停用面有集成测试全量断言（§4.10）。

## 2. 重构候选与路线

**必须重构（4 项）**——判断标准：语义漂移风险（安全/权限）、影响面随时间放大、或有既有机制可收敛：

| # | 编号 | 候选 | 目标形态 | 迁移步骤 | 可回滚性 | 回归风险 | 工作量 |
|---|---|---|---|---|---|---|---|
| 1 | arch-P1-01 | **RePlusPage 四态一等公民**：无 list 权限整页空白（`RePlusPage/src/index.vue:261`）、失败停在空态无重试（`utils/plusPageRequest.ts:119-126`），ReStateContainer 已存在但仅 4 页使用 | 框架统一管理 loading/empty/error/denied；页面只声明不实现 | **绞杀者**：ReStateContainer 进 RePlusPage 渲染链，新增 prop `statePolicy`（默认 framework，存量页可 opt-out），先迁移 4 个显式 denied 页与错误页验证，再分批删 opt-out | prop 一键回退；渲染链改动在单组件内 | 中（全站约 37 列表页路径，需双浏览器 E2E + 视觉基线重录） | M |
| 2 | arch-P1-02 | **字段可见性解析器二合一**：内核 `_role_dept_q`（`common/core/permission.py:35-50`）与数据集 `viewer_visible_fields`（`dataset/utils/dataset.py:391-405`）手工镜像同一「角色∪部门→白名单」安全谓词 | 内核单一 resolver（`_role_dept_q` 已按 role_field 参数化，两表关系名差异已解决），数据集链委托内核 | **适配层**：dataset 侧删镜像改 import 内核函数；行为由既有字段权限测试 + 数据集 fail-closed 测试锁定；随内核 prerelease 发包 | 宿主侧单文件 revert；内核语义不变 | 小-中（权限面，靠守护测试兜底） | S-M |
| 3 | arch-P1-03 | **共享端点权限语义声明式收口**：同一问题四套机制并存——path 字典 + `SHARED_PERMISSION_PRIORITY` 硬编码表（`permission.py:87-96`）+ `permission_meta` 装饰器 + 视图层 `get_permission_field_groups`（`settings.py:91-116`） | action 声明处用装饰器一并声明「共享 path/优先级/字段面分组」，引擎统一消费；硬编码表转声明 | **双读**：新增 `shared_permission` 装饰器登记注册表 → `select_shared_permission` 先查注册表再落硬编码表（兼容期）→ 迁移现存 4 组 → 删表 | 硬编码表保留为 fallback，整个兼容期可回退 | 中（权限主链，有 test_shared_permission_endpoints 守护） | M |
| 4 | arch-P1-04 | **弹层选型收口**：声明式 `el-dialog/el-drawer` 视图层 14 处 + 豁免 5 处绕过 addDialog/addDrawer（overlay 内核的滚动锁/页脚/尺寸被旁路），无门禁防扩散（Phase 2 fe-C-01） | ①立即：建「声明式弹层基线」门禁（仿 bare-tables 只减不增）+ component-handbook 补弹窗选型判定；②远期（触发制）：常驻面板类场景走 overlay 内核 | **冻结→绞杀**：先登记基线冻结存量（S），新违例即红；内核统一按触发条件另立项 | 删门禁即回退；基线文件即清单 | 小 | S（①）/ M（②） |

**可选优化（6 项）**——收益真实但不构成债务压力：

| # | 编号 | 候选 | 目标形态 | 迁移/回滚 | 工作量 |
|---|---|---|---|---|---|
| 5 | arch-P2-01 | 前端 3 处反向边（components→views ×2、layout→views ×1，§1.4） | 预览 preset 组件下沉 components 层或反转依赖；module-cycles 增 FORBIDDEN_EDGES 3 条防复发 | 移动文件 + 加门禁边；revert 即回 | S |
| 6 | arch-P2-02 | dataset 领域逻辑落 utils/ 与 ai 的 services 中心形态不一致 | 轻方案：在 handbook-backend 登记「领域逻辑归属」约定（services 优先、utils 收纯函数），存量不动 | 纯文档 | S |
| 7 | arch-P2-03 | 仪表盘联动=「丢帧重拉」补丁式（ChartCard.vue:345-353），彻底解法是卡片聚合携带 filters 服务端算 | 服务端聚合升格（旧审查同建议） | 触发制：实时联动需求复杂化时立项 | M |
| 8 | arch-P2-04 | 多标签 keep-alive 按 name 粒度（同名多开互清、关后台签 100ms delete→re-add 窗口，`router/utils/nav.ts:27-38`） | 登记已知限制；产品出现强多开需求再改路由命名方案 | 文档登记 | S |
| 9 | arch-P2-05 | 文档漂移：AGENTS.md §二 daphne→实际 gunicorn+UvicornWorker | 改 AGENTS.md 一句话 | 纯文档 | S |
| 10 | arch-P2-06 | `generate_crud --verify` 只报告不阻断（`_generate_crud/__init__.py:153-185`），生成器产物可带未登记权限点离场 | 加 `--strict` 开关（教程默认建议开启），失败即非零退出 | 新开关向后兼容 | S |

**隐式契约盘点**（用户重点关注项）：已收口的隐式契约均有机械守护——菜单组件名↔defineOptions（check:menu-components）、种子 path↔路由（permission-audit 端点+sync --dry-run）、docstring 首行↔菜单标题（check_menu_titles）、settings 转发键（test_settings_forwarding）、WS 帧契约（gen_ws_frame_schema --check+逐字节测试）、事件文档↔EVENT_CATALOG（test_webhook_contract）、依赖清单三方一致（test_dependency_manifest）、契约镜像（check:contract）、AI 声明注册表（test_ai_api_registry_guard，拼错即红）。**尚未收口的 4 处**：SHARED_PERMISSION_PRIORITY↔种子（arch-P1-03 收口）、声明式弹层（arch-P1-04 收口）、components→views 反向边（arch-P2-01 收口）、数据集白名单↔ModelLabelField（守护偏薄，见 §4.6）。

## 3. 三方组件治理

### 3.1 版本与升级策略（总体健康）

双仓 lockfile 精确锁（uv.lock 596 包 / pnpm-lock）+ renovate `rangeStrategy: pin` 每周排期 + patch automerge + OSV alerts + 月度 lock 维护；major 升级（Django/vue/vite/element-plus/typescript/vitest）强制 ADR；后端另有 pyproject `==` 钉版 + `requirements*.txt` 导出产物 + `test_dependency_manifest.py` 三重对齐。**基础镜像四件（python/node/pgvector/redis/nginx 阿里云版）是唯一手动 bump 面**——renovate ignoreDeps 显式排除，属有意取舍。审计：`security.yml` 每周一 pip-audit + pnpm audit（官方源，audit-level high）+ PR 门控，双仓产 cyclonedx SBOM。

### 3.2 风险与例外台账

| 项 | 现状 | 处置 |
|---|---|---|
| twisted（经 daphne 传递）CVE-2026-106454 | 上游无修复版，security.yml 显式豁免 | 复查日 2027-01 已登记（A，以 CI 配置为准） |
| 前端 fast-uri ≥4.0.0,<4.2.3 | 实跑 `pnpm audit --prod --registry=registry.npmjs.org`：4 条（2 low+2 moderate），经 vue3-ts-jsoneditor→vanilla-jsoneditor→ajv 传递 | 走既有 pnpm overrides 地板机制跳版即可（S） |
| 镜像源差异 | huaweicloud pypi / npmmirror 无 audit 端点——本地不显式指官方源会假红/假绿 | CI 已显式官方源；文档提醒开发者（B） |
| pnpm `minimumReleaseAge:0` + `shamefullyHoist:true` | 供应链面略放松 | 有 allowBuilds 审批 + overrides 地板对冲；可评估 minimumReleaseAge 设 1-3 天（E 建议） |
| 出站 HTTP 最后一处直用 | 非测试直 import requests 仅 3 处，`common/utils/file.py` 未经 pinned_request 待复核 | S：确认或收口到 outbound（arch 行动项） |

### 3.3 封装边界与替换成本（关键项）

| 组件 | 封装点 | 替换成本 |
|---|---|---|
| 出站 HTTP | `common/utils/outbound.py::pinned_request`（SSRF/DNS 钉扎单网关，webhook/MCP/AI/IM 全复用） | 低（网关即抽象） |
| AI LLM | **自研 OpenAI 兼容轻客户端**（`integrations/sdk/ai/{chat,async_chat,embeddings}.py`，无 openai/anthropic SDK）；同步/异步共用 parse_chat_message 防漂移；E2E 桩 LLM `scripts/stub_llm.py` | 换 provider 改配置不改码（M：换协议才动 SDK 层） |
| 存储 | `common/storage/backend.py` 三态 local/s3/**mirror 双写搬迁**，运行期按配置指纹热切换，boto3 为可选 extra | M（S3 协议面已隔离） |
| 消息渠道 | notifications/backends 8 渠道 + IM 统一 SDK `integrations/sdk/im/`（BaseImClient：token 缓存+http 注入）；webhook 出站复用 pinned_request | 新增渠道 S / 换 IM 协议 M |
| 短信 | `integrations/sdk/sms/alibaba.py` 单点 | S |
| JWT/Passkey/LDAP/OIDC | simplejwt（5 文件）/ cbor2+argon2（webauthn.py 单文件）/ ldap3（ldap/client.py 单文件）/ pyjwt+cryptography（oidc.py 单文件） | 均 S（单文件边界） |
| 前端 axios/echarts | `utils/http/` 中心化（直用散点仅 5 处，均为上传/启动特例）/ `plugins/echarts.ts` 单点按需懒加载 | S |
| 数据库 | psycopg3（PG 主）+ mysqlclient（可选适配）；pgvector 镜像承载 AI 向量 | L（主库，不动） |

## 4. 二开能力矩阵

评级标准：S=守护测试+文档+可运行示例齐备；M=文档+部分守护；L=薄弱。**11 个扩展点 9 个 S、2 个 M，无 L**——矩阵总体成熟。

| 扩展点 | 扩展方式 | 稳定性守护 | 文档/示例 | 评级 | 缺口 |
|---|---|---|---|---|---|
| 新增 app（generate_crud） | CLI 20+ 参数，14 模块 2,264 行引擎；GUI 同源引擎不落盘（`devtools/services/codegen/gui.py:3-14`） | test_generate_crud / test_codegen_plan / test_codegen_gui | first-module-30min.md 含 2026-10-07 实跑复核记录；demo app 即样板 | **S** | `--verify` 只报告不阻断（arch-P2-06） |
| 菜单/权限种子 | `<app>/seeds/manifest.json` 自声明（depends/url_prefixes/config_keys/dict_codes）；sync_menu_permissions 按归属回写 | check-menu-permissions 双向对账 + load_init_json --check 归属校验 | menu-maintenance.md 例外逐条登记；各 app seeds 即示例 | **S** | 对账门禁在 client 仓为脚本非 pytest，后端 CI 不感知 |
| 元数据协议/字段类型 | 新 input_type 四通道：后端 metadata.py isinstance 分支 + 前端 registry 三注册器（`RePlusPage/src/utils/registry.ts:20-39`） | renderers-pairing.spec（成对登记）+ DECLARED_INPUT_TYPES 全集约束 | metadata-protocol.md + field-types.md 逐类型矩阵 + recipes 处方 | **S** | — |
| 动态表单 | schema_version 递增 + history 快照 + 提交引用提交时版本；回滚=生成新版本 | test_dform_flow/history/linkage 等 6+ 单测 | dform_flow.py 完整接入样例 | **S** | — |
| 审批流 | 双机制正交（ApprovalRequired+路径清单 / flow 引擎）；**终态回写零核心改动**：业务 app `APPROVAL_BIZ_SYNCERS` 自声明（`approval/biz_sync.py:42-73` 惰性 import+裁剪豁免） | test_biz_sync（159 行：未知 biz_type 吞掉、异常不传播） | approval/README 两机制矩阵 + leave/dform_flow 双先例 | **S** | — |
| 数据集白名单 | 白名单=ModelLabelField DATA 根节点（`dataset/utils/dataset.py:85-124`）；新增模型=跑 `sync_model_field` | 仅 test_dataset_request_memo（缓存/复用断言）；NL 查数同闸（nl_query.py:93,153,157） | **无专篇文档** | **M** | 「新模型未登记即 403」无显式守护用例；文档需拼代码才得出 |
| 通知渠道 | 「一个模块文件+一行注册」（backends/<name>.py 暴露 backend 实例）；插件运行期走 register_channel_spec | test_channel_specs / test_registry | notification-channels.md 81 行含示例代码 | **M** | 插件侧无可运行通道样板（examples 未含）；运行期通道注册无独立测试样例 |
| AI 工具/动作 | 业务 app `ai_declarations.py` 自声明，ai_registry 发现合并；唯一执行收口 execute_action（白名单+重校验+权限双门+裁剪+审计） | test_ai_api_registry_guard（声明拼错即红）+ MCP 两守护 | ai_actions.py docstring 红线 + plugin-development §4.1 | **S** | — |
| Webhook 事件 | EVENT_CATALOG 加一条+信号接线；payload 外壳固定；破坏性变更=xxx.v2 平行发布一个窗口；HMAC 时间戳签名 | **文档自动生成**（gen_event_docs.py）+ test_webhook_contract 锁一致 | events.md 勿手编，含签名/重试/版本策略 | **S** | — |
| 插件三通道 | entry point（pyproject 声明宿主零接线）/ AppConfig.ready / 应用级扩展点；demo 插件 13 文件 419 行全 ≤70 行 | **test_plugin_demo 11 断言**：契约注入/时序/重复注册 fail-fast/停用后 REST 404+WS 4404+权限前缀停用/发行预设覆盖/周期任务归属 | plugin-development.md 327 行含验证清单；可 pip install 直装 | **S** | — |
| 模块声明 | 内置改 catalog.py；第三方 `{app}/modules.py` MODULES 自声明自动发现（routes 可由 URLPATTERNS 推导） | 同 test_plugin_demo + 模块 id 重复 fail-fast + depends 校验 | 模块化与功能裁剪.md + specs.py 字段级 docstring | **S** | — |

## 5. 目标架构与演进路线

### 5.1 依赖方向约束（目标态 = 现状 + 3 条补强）

```
内核 xadmin-common ←── 各业务 app（唯一向上依赖；内核永不反向，common→app=0 已门禁）
业务 app 横向 ──→ 仅经 <app>.contracts / *.services 声明缝（check_cross_app_imports 活门禁，18 条 allowlist 全为管理命令）
反向注册面 ──→ 声明式自注册（ai_declarations / APPROVAL_BIZ_SYNCERS / MODULES / TASK_ROUTES），惰性 import + 裁剪自然豁免
前端 ──→ views→{components,hooks,api,store}；store→api 仅动态 import（FORBIDDEN_EDGES）；components/layout ↛ views（本轮补 3 条边）
```

### 5.2 分阶段重构计划

| 阶段 | 内容 | 性质 | 验证 |
|---|---|---|---|
| 阶段 0（立即，S） | 三个门禁补齐：①声明式弹层基线（arch-P1-04①）②module-cycles 补 3 条反向边（arch-P2-01）③出站 requests 直用复核收口（§3.2） | 纯冻结/防扩散，零行为变更 | 门禁跑通 + 存量清点 |
| 阶段 1（S-M） | 内核字段可见性 resolver 二合一（arch-P1-02，走内核 prerelease 发包）+ 共享端点声明式收口（arch-P1-03，双读迁移 4 组） | 安全语义去重 | 字段权限/数据集 fail-closed 守护测试 + test_shared_permission_endpoints |
| 阶段 2（M） | RePlusPage 四态框架化（arch-P1-01，绞杀者+opt-out） | 框架能力升格 | 双浏览器 E2E + 视觉基线重录 + 分批迁移 |
| 阶段 3（触发制） | 仪表盘服务端聚合（arch-P2-03）/ keep-alive 粒度（arch-P2-04）——触发条件未命中不立项；弹层内核统一（arch-P1-04②）已提前交付 | 按台账纪律 | 各自独立验证 |
| 随手项（S） | arch-P2-05 AGENTS daphne→gunicorn 文档修正、arch-P2-02 领域归属约定入 handbook、arch-P2-06 generate_crud --strict、二开两处 M 级缺口（白名单文档专篇 + 插件通知渠道样板） | 文档/小工具 | 文档门禁 + 教程实跑 |

### 5.3 DEV-TASKS.md 候选条目（按其叙事格式预写，可直接并入）

1. **架构收口第一批（门禁三件，零行为变更）**：声明式弹层基线门禁（client，登记 14+5 存量）；module-cycles FORBIDDEN_EDGES 补 components→views ×2 与 layout→views ×1（存量 3 处先归位再锁）；复核 `common/utils/file.py` 的 requests 直用并收口 pinned_request。验证：三门禁绿 + grep 复算；回归风险低。
2. **内核字段可见性 resolver 二合一**：dataset `viewer_visible_fields` 删手工镜像改委托内核 `_role_dept_q`（role_field="role"），随内核 prerelease 发包+宿主升级注记；验证：字段权限守护测试 + 数据集 fail-closed 测试全绿；回滚=宿主侧 revert。
3. **共享端点权限声明式收口**：新增 shared_permission 装饰器（声明优先级+字段面分组），select_shared_permission 双读（注册表优先、SHARED_PERMISSION_PRIORITY 兜底），迁移 4 组后删表；验证：test_shared_permission_endpoints + 权限链全量。
4. **RePlusPage 四态框架化**：ReStateContainer 进渲染链 + statePolicy prop（默认 framework、存量 opt-out），迁移系统域 4 个显式 denied 页作为样板后分批清 opt-out；验证：双浏览器 E2E（无权限账号+断网场景）+ 视觉基线；回归风险中，单独立项独立 commit。
5. **二开补短两件**：数据集白名单文档专篇（含「未登记即 403」守护用例）+ examples/plugins 通知渠道样板（含 register_channel_spec 运行期通道测试样例）。

## 6. 未验证事项

1. pnpm overrides 地板跳版后 fast-uri 4.2.3+ 与 vanilla-jsoneditor 的兼容性（需实际 bump 验证）。
2. PG17/Redis8 数据卷跨版本兼容细节未逐条核（指针 `docs/ops/upgrade-stock.md`，属交付链路审查范围）。
3. email 通知渠道具体实现未核（C）；各扩展点 E2E 双浏览器覆盖面未逐一核对。
4. `字段类型全量自动渲染方案-2026.10.md`（39KB）落地进度未读，仅确认存在——元数据协议演进以该方案与 ADR 为准。
5. 本册三次方审计数字（pip-audit/pnpm audit）为采集时点快照，CVE 现状以 CI 周期审计为准。

## 7. 整改执行状态（2026-10-26）

| 编号 | 状态 | 落地内容 / 证据 |
|---|---|---|
| 阶段 0 门禁三件 | ✅ | ①声明式弹层门禁 `xadmin-client/scripts/check-declarative-overlays.mjs` + 基线 21 文件（只减不增）+ CI/AGENTS/ci-gates/handbook 登记；②`check-module-cycles` 增 components→views、layout→views 两条禁止边；③`download_file` 改走 `pinned_request`（固定解析连接，补 2 例单测） |
| arch-P1-01 RePlusPage 四态 | ✅ | 新增 `PageState.vue` + `usePageState`（denied/error/ready，`statePolicy` 默认 framework、legacy 可 opt-out）；`plusPageRequest` 记录 `loadError`，错误态内联重试；单测 `pageState.spec` / `plusPageRequest.spec`；`index.vue` 拆分后 493 行 |
| arch-P1-02 字段可见性 resolver | ✅ | 内核 `_role_dept_q` 公开为 `role_dept_q`（docstring 明确业务侧复用）；dataset `viewer_visible_fields` 删除手工镜像改委托内核；数据集/字段权限测试全绿 |
| arch-P1-03 共享端点声明式 | ✅ | `shared_permissions`（类装饰器）与 `shared_permission`（方法装饰器）；4 组共享端点迁移；注册表优先 + 兜底表兼容；种子/声明/兜底表三方一致性守护 |
| arch-P1-04① 弹层基线 | ✅ | 门禁 + 21 条存量登记（14 视图 + 7 封装/全局件）；`handbook-frontend` §2.2 补「声明式弹层判定」 |
| arch-P1-04② 弹层内核统一 | ✅ | 新增声明式壳 `OverlayDialog` / `OverlayDrawer`（与命令式弹层共用滚动锁）；视图层与封装层 19 处内联弹层全部迁移，声明式弹层基线 21 → 4（仅剩 ReDialog / ReDrawer / 两个壳组件）；`check:overlays` 提示文案与 `handbook-frontend` §2.2 同步 |
| arch-P2-01 前端反向边 | ✅ | `Motion` 下沉 `components/ReMotion`；站内信详情弹窗下沉 `components/ReNoticeShow`；权限预览组件改由页面层注入（presets 不再 import views）；反向边 0 条，门禁锁 2 条前缀边 |
| arch-P2-02 领域归属约定 | ✅ | `handbook-backend.md` §1.15（services vs utils 判定与反例） |
| arch-P2-03 仪表盘服务端聚合 | ⏸ 触发制 | 触发条件未命中，不立项 |
| arch-P2-04 keep-alive 粒度 | ✅ | `xadmin-client/docs/layout-modes.md` §八「已知限制」登记 |
| arch-P2-05 AGENTS 漂移 | ✅ | 工作区 `AGENTS.md` §二 daphne → gunicorn（UvicornWorker） |
| arch-P2-06 generate_crud --strict | ✅ | `--strict`（隐含 --verify）失败非零退出；3 例单测（缺口/漂移/全绿） |
| §3.2 fast-uri | ✅ | `pnpm-workspace.yaml` override `fast-uri@>=4.0.0 <4.2.3 → >=4.2.3`；lock 4.1.5→4.2.3；`pnpm audit --prod` 0 漏洞 |
| §4 数据集白名单 | ✅ | 新增 `docs/architecture/dataset-field-whitelist.md`（登记 docs/README）+ `tests/unit/dataset/test_dataset_model_whitelist.py` |
| §4 插件通知渠道样板 | ⏳ 待决策 | `register_channel_spec` 现状要求 code 已在 `BACKEND` 枚举中，插件无法新增渠道码；补样板前需先决定「运行期扩枚举」方案，本轮未改 |

> 验证：后端 `7112 passed / 2 skipped`、ruff/mypy/脚本门禁/迁移漂移/种子校验全绿；前端 vitest 2824 例、typecheck/lint、23 项 `check:*` + 行数与 hook 门禁全绿；开发栈容器已重启，`sync_menu_permissions --dry-run` 缺口 0。

## 8. 第二轮收口补充（2026-10-26）

- §4「插件通知渠道样板」：**约束已登记，样板待决策**。复核确认 `register_channel_spec` 要求 code 已在 `BACKEND` 枚举，插件无法新增渠道码（枚举是序列化契约）；已在 `docs/architecture/notification-channels.md` §七登记运行期扩展边界与替代路径（复用 webhook 转发），既有 `TestNotificationChannelRegistration` 覆盖枚举外/重复/缺渲染方法三类 fail-fast。样板无法在不扩枚举的前提下落地；是否扩展枚举（或仅保留 webhook 复用路径）需产品/架构决策。
- 前端逐页剩余 P2/E 已在 [code-review-frontend-2026-10.md](code-review-frontend-2026-10.md) §9 收口（菜单树/代码生成失败态、离开确认、令牌化、@ 键盘、字段删除确认、摘要列、playground i18n、welcome 空态等）。
