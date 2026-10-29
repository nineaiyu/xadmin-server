# 变更日志（xadmin-server）

> 形态遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，平台版本遵循
> [语义化版本](https://semver.org/lang/zh-CN/)：前后端同版本号，发布 tag 门禁校验
> `vX.Y.Z` ↔ `server/const.py` 的 `VERSION` ↔ `xadmin-client/package.json` 三方一致。
>
> **版本单一事实源是 `server/const.py` 的 `VERSION`**；每个版本的「升级注意」在此登记，
> 逐条细节以 [docs/adr/](docs/adr/README.md) 的决策记录为准。前端变更见
> [xadmin-client](https://github.com/nineaiyu/xadmin-client)。

## [Unreleased]

### 变更

- **迁移基线重建（不兼容既有库）**：全部业务 app 迁移重新生成为全新 `0001/0002`
  初始迁移，删除历史迁移与旧数据兼容数据迁移；4.x / 5.0.x 既有库不再支持原地升级，
  需清库重建 + `legacy_export` / `legacy_import` 回灌（见
  [docs/ops/upgrade-stock.md](docs/ops/upgrade-stock.md)）。新基线保留 pgvector /
  pg_trgm 扩展创建与验证码规则种子。


- **AI 动作声明按应用自动发现**：各应用可在根目录提供 `ai_declarations.py`
  （`AI_ACTIONS`，兼容 `AI_READ_ACTIONS` / `AI_WRITE_ACTIONS`），由
  `ai/services/actions/ai_registry.py` 在首次构建工具目录时合并；key 全局唯一、重复 fail-fast。
  二开应用随安装自动接入，无需改动 ai 应用。
- **AI 动作面补全**：动作由 60 个扩至 105 个（102 声明式 + 3 内置），补充岗位 / 登录策略 /
  账号巡检 / 开放平台 / 任务中心 / 审批流实例与定义 / 报表与大屏 / 表单提交 / 通知模板 /
  文件访问记录 / 安全策略读取等只读能力；新增 `settings/ai_declarations.py`。
- **AI 动作跟随模块裁剪**：声明可标注 `modules`，停用模块后动作从工具目录移除且拒绝执行
  （内部 dispatch 绕过 ASGI 模块网关，故在目录/草稿/执行三处统一判定）。
- **代码生成器**：产物改用 `AI_ACTIONS`；`--with-module` 时自动标注 `modules`；
  同一应用多模型按生成块合并（重复生成幂等），修复多模型场景共享文件多空行导致的
  ruff format 失败。
- **字段类型补全**：元数据词表新增 `url` / `decimal` / `rich_text` / `password`；
  新增 `RichTextField`（入库 bleach 白名单净化）与 `PasswordField`；`DecimalField`
  随元数据下发 `decimal_places`，`ColorField` 支持 `predefine` 预设色板。

### 变更

- **`PhoneField` 写入口径收紧**：默认捆绑格式校验；解析失败不再静默补 `+86`，改为
  抛出校验错误；空值统一按 `allow_blank` / `allow_null` 收口。依赖历史「解析失败补
  地区码」行为的二开字段须显式声明 `country="CN"` 并关闭默认校验（`validate=False`）。
- **`ColorField` 增加服务端格式校验**：仅接受 `#rgb/#rgba/#rrggbb/#rrggbbaa`、
  `rgb()/rgba()/hsl()/hsla()` 与 CSS 颜色名，与水印颜色校验共用同一常量。
- **AI 动作注册表位置变更**：集中声明文件 `ai/utils/ai_api_registry.py` /
  `ai_registry_ops.py` / `ai_registry_infra.py` / `ai_registry_org.py` /
  `ai_builtin_actions.py` 移除，声明下沉到所属应用（请假→`approval`、动态表单→`dataset`、
  首页统计→`system`）；`ai/services/actions/ai_actions.py` 只保留目录构建 / 草稿解析 / 权限双门 /
  执行收口 / 审计。`ai.services.api_action_specs()` 与 OpenAPI 元数据派生仍为同一入口。
- 新增 90 条 AI 动作文案的中文翻译（`locale/zh`），en 目录同步登记。

## [5.0.2] - 2026-10-19

### 新增

- **公告正文支持 Markdown**：`Notice.message` 之外新增 `message_format` 字段（`html` /
  `markdown`），详情 / 我的通知 / 铃铛 / 桌面与应用内预览按格式分流；Markdown 分支不加载
  WangEditor，统一经 `MarkdownView` 渲染（净化 + 高亮）；通知 store 的列表预览将 Markdown
  转纯文本。存量 HTML 公告可用 `python manage.py convert_notice_markdown` 有损转换
  （默认 dry-run，`--apply` 才写库）。
- **字段权限种子同步命令**：`python manage.py sync_field_permissions --update-seed`
  按归属把字段树与角色白名单一次写回种子（不动历史裁剪），与 `sync_menu_permissions`
  同口径。
- **存量 4.x 库的数据回灌**：`upgrade_ledger` 之外新增回灌工具（保留原主键、支持二开表
  整表搬运、系统设置 / 字段权限 / 消息订阅补齐），升级后可用 `INPLACE_ALIGN=1` 走
  原地结构对齐与字段白名单自愈。

### 变更

- **内置种子按应用分层**：种子拆到各 `<app>/seeds/`（应用级）与 `loadjson/`（平台级），
  导入 / 导出 / 工具链按归属收口；`doctor` 自检由八项扩为十项（新增内置种子、AI 声明）。
- **种子装载行为修正**：装载前剥离角色菜单 m2m，避免重灌时清零存量授权；字段权限补齐的
  写入侧改为批量收敛，消除种子收尾阶段的放大；种子导出与收尾的批量读取收敛，消除尾部 N+1。
- **AI 助手**：修复指令执行追问丢失与双层 JSON 解析；AI 配置字段补充表单帮助文案；
  服务端元数据配合前端表单收敛。
- **测试基建**：全量 pytest 默认并行度降至 8；pytest 改用 PG 会话锁串行化，消除跨轮次
  共享 Redis 锁键导致的假失败。

### 说明

- 本版本**无表结构变更**（公告 `message_format` 字段随 5.0 的初始迁移一并表达），升级沿用
  5.0 通道：`./xadmin.sh upgrade v5.0.2`（全新安装 `./xadmin.sh install`）。
- 前端同步发布公告 Markdown 编辑器（编辑 / 分栏 / 预览三态、工具栏、按钮 / 拖拽 / 粘贴
  附件上传），变更见 [xadmin-client](https://github.com/nineaiyu/xadmin-client)。
- 安装器侧：升级链路接入存量库原地结构对齐，升级流程内置 PostgreSQL 大版本数据目录迁移，
  并修复升级路径的 v 前缀、备份开关与基线门禁三处缺陷。
- 发布顺序：server / client 镜像构建成功 → web（依赖 client 镜像）→ docs → installer
  （其镜像同步会等待 server / web / docs 镜像产出）。

## [5.0.1] - 2026-10-16

### 修复

- **首次安装被拦下（安装器侧，共 5 处）**：`ALLOWED_HOSTS` 从未写入配置，服务端在
  `DEBUG=false` 下拒绝启动；bind 挂载的数据目录属主未适配非 root 运行（镜像 `USER 1001`），
  应用无权创建日志目录；全新空库被升级前体检判成 `schema-drift`；`$1` 未绑定导致
  `./xadmin.sh start/stop/status` 直接中止；celery 容器健康检查脚本路径错误（恒 unhealthy）。
- **升级前体检的空库判定**：`upgrade_check` 的表结构体检缺少「有无迁移记录」前置判断，
  全新空库（首次安装）因缺表被判 `schema-drift` 并阻断流程（文档口径本是 `needs-migrate`）；
  现仅在已有迁移记录时才做漂移判定。
- **镜像分发链路**：构建侧关闭镜像内构建溯源附件（下游阿里云镜像源不支持 OCI 1.1
  artifact 形态的附件，会导致镜像无法同步）；镜像同步改为按平台 manifest 重建 index，
  同步前等待源镜像产出，并提供按版本补跑入口；web 镜像构建前等待 client 镜像，消除同批
  发版的顺序敏感。

### 说明

- 本版本**无表结构变更、无接口变更**，升级沿用 5.0 通道：`./xadmin.sh upgrade v5.0.1`
  （全新安装 `./xadmin.sh install`）。
- 发布顺序：server / client 镜像构建成功 → web（依赖 client 镜像）→ docs → installer
  （其镜像同步会等待 server / web / docs 镜像产出）。

## [5.0.0] - 2026-10-14

5.0 是相对 4.2.5 的**大版本**：迁移链收敛为初始迁移并把存量库升级路径从「清库重建」
升级为「原地认领」，同时把 4.2.5 以来累积的能力整批发布。

### 升级注意

- **迁移链合并 + 账本认领**：各业务 app 的分散迁移收敛为初始迁移（每 app `0001_initial`，
  跨 app 循环依赖处 `0001_initial` + `0002_initial`），**表结构与 4.2.5 逐行等价**。
  4.2.5 起的库升级先跑 `python manage.py upgrade_ledger --apply` 认领迁移记录（只写
  `django_migrations`，不执行 DDL），再 `migrate`（预期 `No migrations to apply`）。
  更早的库（2026-10 迁移链重建之前）仍需清库重建。完整步骤见
  [docs/ops/upgrade-5.0.md](docs/ops/upgrade-5.0.md)；决策见
  [ADR-093](docs/adr/ADR-093-migration-consolidation-and-ledger-claim.md)。
- **站点级外观配置改为全局生效**：站点标题 / Logo 品牌位 / 登录页布局写系统配置
  （仅超管可改），个人配置里的同名历史值不再生效（[ADR-092](docs/adr/ADR-092-site-wide-appearance-config.md)）。
- **媒体文件鉴权**：`/media/` 不再有匿名直链，统一走应用鉴权 + `X-Accel-Redirect` 内转
  （nginx 配置需同步更新）。
- **权限点粒度调整**：执行历史「取消 / 重跑」改名 `cancel|rerun:SystemTaskExecution`；
  水印页签拆出 `retrieve|partialUpdate:SettingWatermark`——存量自定义角色需在角色管理里
  重新勾选新权限点（SystemAdmin 自动获得）。
- **升级后必跑**：`python manage.py post_upgrade`（账本认领 → API 前缀平移 → 种子导入 →
  语言包编译 → 配置缓存失效；安装器升级流程已自动调用）。

### 新增

- **迁移与升级**：`manage.py upgrade_ledger`（账本对账 / 认领）、`upgrade_check` 新增
  `legacy-chain-reconcilable` 判定；安装器升级流程串联体检 → 认领 → 迁移，升级基线 `v4.2.5`。
- **演示模式**：安装器 `WITH_DEMO_DATA=1` 一键灌演示数据；演示账号 `admin / admin123`
  挂「演示模式」角色（破坏性写操作已裁剪），`seed_demo_all --reset` 可随时重置。
- **开放平台**：开放 API 应用与作用域授权、出站 Webhook（事件目录 / 签名 / 投递重试与审计）、
  事件契约文档（[ADR-030](docs/adr/ADR-030-open-platform.md) / [ADR-039](docs/adr/ADR-039-open-platform-phase2.md) /
  [docs/open-platform/](docs/open-platform/README.md)）。
- **AI 能力**：AI 助手（文档问答 / NL 查数 / 受限动作执行三通道）、多档案模型配置与能力探测、
  用量与成本核算、知识库管理（含向量检索与词频回退双通道）、AI 控制台
  （[ADR-023](docs/adr/ADR-023-ai-assistant-phase1.md) / [ADR-024](docs/adr/ADR-024-nl-query-phase2.md) /
  [ADR-033](docs/adr/ADR-033-knowledge-base-management.md) / [ADR-038](docs/adr/ADR-038-ai-actions.md) /
  [ADR-065](docs/adr/ADR-065-knowledge-vector-retrieval.md) / [ADR-076](docs/adr/ADR-076-ai-cost-accounting.md)）。
- **审批流引擎**：多级 / 条件分支 / 会签与比例会签 / 加签转交 / 撤回重提 / 催办 /
  讨论区与抄送人 / 在途版本化 / 与表单采集双向集成
  （[ADR-012](docs/adr/ADR-012-approval-flow-engine.md) / [ADR-040](docs/adr/ADR-040-approval-flow-phase3.md) /
  [ADR-073](docs/adr/ADR-073-in-flight-flow-versioning.md)）。
- **动态表单与表单数据**：设计器（控件扩展 / 公式 / 联动 / 模板）、草稿与提交、
  数据管理与导出、筛选物化、字段可见性
  （[ADR-025](docs/adr/ADR-025-dynamic-form-phase1.md) / [ADR-064](docs/adr/ADR-064-dform-designer-upgrade.md) /
  [ADR-068](docs/adr/ADR-068-form-data-management.md)）。
- **数据分析**：数据集（SQL 口径 / JSON 列 / 日期趋势）、仪表盘与卡片级权限、定时报表、
  图表 / 大屏图片导出（[ADR-020](docs/adr/ADR-020-dataset-dashboard-phase1.md) /
  [ADR-042](docs/adr/ADR-042-dashboard-card-permission.md) / [ADR-069](docs/adr/ADR-069-dataset-json-columns.md)）。
- **大屏自由画布**：像素级布局 + 参考线吸附 + 多选对齐 + 模板与云端剪贴板 + 富文本 /
  视频 / iframe 窗格（五期，[ADR-086](docs/adr/ADR-086-screen-free-canvas.md)~[ADR-090](docs/adr/ADR-090-screen-canvas-advanced-4.md)）。
- **聊天室**：三表模型 + WebSocket 通道 + 附件消息 + AI 双形态（私聊 / 群聊）+ 表情面板 +
  桌面通知 + 历史清理策略（[ADR-034](docs/adr/ADR-034-chat-room-rebuild.md) / [ADR-060](docs/adr/ADR-060-chat-attachment-messages.md)）。
- **通知与消息**：多渠道后端（站内信 / 邮件 / 短信 / IM 扫码 / 企业微信等）+ 模板沙箱渲染 +
  样例消息预览（[ADR-019](docs/adr/ADR-019-im-notify-channels.md)）。
- **安全与合规**：AES 传输协议 v2（WebCrypto）、数据脱敏（超管豁免口径）、字段级与数据级权限、
  PAT 个人访问令牌、页面水印、CSP 页面层强制、登录策略（时段 / 网段 / 并发会话上限）、
  账号巡检、Passkey、MFA、OIDC / LDAP 目录同步、审计日志冷归档
  （[ADR-009](docs/adr/ADR-009-data-mask-exemption.md) / [ADR-011](docs/adr/ADR-011-aes-protocol-v2.md) /
  [ADR-029](docs/adr/ADR-029-page-watermark.md) / [ADR-072](docs/adr/ADR-072-observability-hardening.md)）。
- **平台工程**：模块化与六层功能裁剪（发行预设 / WebSocket 通道裁剪）、内核独立分发包
  `xadmin-common`、代码生成器 `generate_crud`、契约治理（`docs/schema/` 为真源）、
  元数据驱动列表页协议、缓存与索引体系
  （[ADR-027](docs/adr/ADR-027-code-generator.md) / [ADR-035](docs/adr/ADR-035-api-contract-governance.md) /
  [ADR-045](docs/adr/ADR-045-modular-trimmable-architecture.md) / [ADR-078](docs/adr/ADR-078-asgi-async-middleware.md)）。
- **运维能力**：健康检查与可观测（指标 / 告警分级 / 演练台账）、备份与 PITR、横向扩展与
  蓝绿发布 runbook、SLO 快照、工作区跨仓一致性健康报告
  （[docs/ops/](docs/ops/observability.md)）。

### 变更

- 版本号口径：`server/const.py` 与 `xadmin-client/package.json` 同步为 `5.0.0`
  （发布 tag 门禁三方校验）。
- 迁移文件由 26 个（14 个业务 app）收敛为 21 个（含 7 个跨 app 循环依赖拆分的 `0002_initial`）。
- 默认数据（种子）重生成：权限点 ↔ 路由 0 缺口、模型字段树补齐 157 条（新增模型的
  ROLE / DATA 字段节点）、清理菜单种子中的软删历史行、移除含真实口令哈希的孤儿种子文件。
- 多域拆分后的 URL 前缀：身份 / 文件 / 审计 / 任务四域迁至 `/api/identity|file|audit|task/`
  （存量库由 `migrate_api_prefixes --apply` 平移）。
- 语言包按当前代码重新提取（POT 与位置注释刷新，中文目录保持零缺口、零 fuzzy）。

### 修复

- 4.2.5 以来累积的缺陷修复以各 ADR 的「后果 / 边界」段落与
  [docs/plans/](docs/plans/README.md) 台账为准（含审批人解析引导文案、SSE 实时性、
  列表页 CLS、高级筛选与「我的视图」、上传关联写路径空值等）。

### 已知边界

- 5.0 不含表结构变更（迁移合并的前提）；5.x 若有结构变更按常规新增迁移表达。
- 白名单之外的旧链库（2026-10 四轮重建之前）不支持自动认领，走清库重建 + 数据回灌。
