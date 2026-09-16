# xadmin-server 文档中心

> 半年规划 T6.1 建立的 docs/ 知识库索引。目标：新人按本目录可完成环境搭建与第一个功能开发，无需依赖外站文档。
> 外站文档 https://docs.dvcloud.xin/ 降级为补充资料，逐步以本目录为准。

## 目录结构

```
docs/
├── README.md            本索引
├── adr/                 架构决策记录（ADR）
├── architecture/        架构设计文档（含数据权限重构设计与行为对照）
├── ops/                 部署与运维（deployment.md + runbook + 演练/基线记录）
├── plans/               项目规划与治理文档（跨仓库排期/台账，见 plans/README.md）
├── history/             历史归档（XADMIN_FRAMEWORK_ANALYSIS.md）
├── schema/              前后端契约 JSON Schema
├── imgs/                文档配图
├── metrics.md           基线指标看板（覆盖率/体积/性能 KPI 登记与回填）
├── 框架开发遵循准则.md    服务端 + 前端开发统一约定与检查清单
├── exception-handling.md  异常处理与错误码规范
└── security-review.md   安全自查清单（按轮次追加归档）
```

## 新人上手路径

1. **环境搭建**：根 [README](../README.md)（快速启动命令）→ [ops/deployment.md](ops/deployment.md)（配置项详解 / Docker /
   生产部署 / 升级回滚）；
2. **理解架构**：[architecture/overview.md](architecture/overview.md)
   （总览导航）→ [architecture/permission.md](architecture/permission.md)（三层权限，本项目核心）；
3. **动手开发**：参考 demo app（Book 四件套 + 菜单初始化；前端 `BaseApi` + `RePlusPage`
   两行代码一个页面），契约与错误码遵循 [schema/](schema/README.md) 与 [exception-handling.md](exception-handling.md)；
4. **修改核心框架前**：读 [architecture/cache.md](architecture/cache.md)
   （缓存红线）与 [architecture/indexes.md](architecture/indexes.md)（索引规范），确保 pytest 全绿。

## 架构设计（architecture/）

| 文档                                                      | 内容                                           |
|---------------------------------------------------------|----------------------------------------------|
| [overview.md](architecture/overview.md)                 | 架构总览：分层、元数据驱动、子系统速览、协作时序（1289 行深度分析文档的精炼导航版） |
| [framework-cookbook.md](architecture/framework-cookbook.md) | 框架能力速查（二开 CookBook）：ViewSet 选型、Action↔BaseApi 对照、覆写点、前端契约、约定红线 |
| [permission.md](architecture/permission.md)             | 三层权限体系设计：生效顺序、14 种数据规则速查、缓存/信号失效链路、调试指引与测试地图 |
| [data-permission.md](architecture/data-permission.md)   | 数据权限配置操作教程（配图）                               |
| [field-permission.md](architecture/field-permission.md) | 字段权限配置操作教程（配图）                               |
| [cache.md](architecture/cache.md)                       | 缓存策略统一审计：四套缓存键规范/TTL/失效矩阵/绕过 ORM 红线          |
| [indexes.md](architecture/indexes.md)                   | 索引评审记录：清单、不加索引的理由、EXPLAIN 回归                 |
| [mfa.md](architecture/mfa.md)                           | MFA 敏感操作二次验证设计：四后端 / 412 协议 / 权限工厂           |
| [scim.md](architecture/scim.md)                         | SCIM 2.0 用户目录同步：启用步骤 / 字段与组映射 / Okta、Entra 配置示例 / 排错 |
| [notification-channels.md](architecture/notification-channels.md) | 通知渠道体系：三件套模型、新增渠道步骤、两层可达性过滤、短信通知模板配置与排错 |
| [数据权限与字段权限重构方案-2026.09.md](architecture/数据权限与字段权限重构方案-2026.09.md) | 数据权限重构设计（规则编译器四段管线 + ScopeResult 布尔代数）：问题清单 / 语义决策 D1–D11 / 实施批次与测试计划 |
| [权限行为新旧对比-2026.09.md](architecture/权限行为新旧对比-2026.09.md) | 上篇的配套交付物：同一份配置在旧/新实现下的逐场景结果对照、升级操作清单（迁移 0014 + 巡检命令） |
| [菜单权限与字段同步补全方案-2026.09.md](architecture/菜单权限与字段同步补全方案-2026.09.md) | 调研报告 + 整改方案：权限点覆盖缺口 91 条（方法级扫描 + 运行时复现）、字段同步缺 `system.aiprofile` 等；归一映射 / 漂移守护 / 自动同步三项机制与实施批次 |

## 部署与运维（ops/）

| 文档                                                     | 内容                                              |
|--------------------------------------------------------|-------------------------------------------------|
| [deployment.md](ops/deployment.md)                     | 配置项详解、Docker 部署、备份恢复、升级回滚、监控告警                  |
| [runbook.md](ops/runbook.md)                           | 常见故障 → 处置步骤（≥10 个场景）                            |
| [release-checklist.md](ops/release-checklist.md)       | 发布窗口 checklist：基线门禁、CSP enforce 与 AES v1 关闭硬门禁、挂起项与执行记录 |
| [performance-baseline.md](ops/performance-baseline.md) | 性能基线测定流程（T3.1）：silk 剖析接入 + k6 六接口压测 + 登记口径与回归判定 |
| [backup-drill-2027-03.md](ops/backup-drill-2027-03.md) | 备份演练（异地副本/媒体目录/RPO 6h 收口）：`utils/backup_drill.sh` 一键闭环与结果 |
| [backup-drill-2026-Q4.md](ops/backup-drill-2026-Q4.md) | 季度演练（Q4，提前执行）：67 表逐表 0 不一致、RTO 0.28s，一并验收备份失败告警（S2） |
| [backup-drill-2026-09.md](ops/backup-drill-2026-09.md) | 首次备份演练记录（RTO 0.88s、52 表一致）与当时遗留缺口                 |

## 架构决策记录（adr/）

| ADR                                          | 主题                                 |
|----------------------------------------------|------------------------------------|
| [ADR-001](adr/ADR-001-csrf-jwt-only.md)      | CSRF 中间件不启用（JWT-only 架构）           |
| [ADR-002](adr/ADR-002-demo-app.md)           | demo app 去留：保留但默认关闭                |
| [ADR-003](adr/ADR-003-websocket-protocol.md) | WebSocket 协议保持自定义格式并补类型约束          |
| [ADR-004](adr/ADR-004-django-60-upgrade.md)  | Django 升级：停留 5.2 LTS（6.2 升级已取消：celery 未支持） |
| [ADR-005](adr/ADR-005-redis-split.md)        | Redis 拆分（缓存/队列/会话分实例）              |
| [ADR-006](adr/ADR-006-asgi-db-connection-pool.md) | ASGI 形态启用 Django server 端 DB 连接池      |
| [ADR-007](adr/ADR-007-multipart-form-data-v1-protocol.md) | FormData 上传协议 v1（点分键序列化契约）       |
| [ADR-008](adr/ADR-008-pat-auth.md)           | 个人访问令牌（PAT）：scope + 精确审计           |
| [ADR-009](adr/ADR-009-data-mask-exemption.md) | 数据脱敏豁免清单机制                          |
| [ADR-010](adr/ADR-010-crypto-es.md)          | crypto-js 弃用处置：替换为 crypto-es        |
| [ADR-011](adr/ADR-011-aes-protocol-v2.md)    | 凭证加密协议升级 v2（WebCrypto PBKDF2+AES-GCM 双格式过渡） |
| [ADR-012](adr/ADR-012-approval-flow-engine.md) | 审批流引擎（模板/实例/任务/加签/催办，含触发器与数据权限） |
| [ADR-013](adr/ADR-013-office-online-preview.md) | Office 在线预览选型：LibreOffice headless 转 PDF（重队列 + 缓存回收） |
| [ADR-014](adr/ADR-014-typescript-7-evaluation.md) | TypeScript 7 升级评估：暂不升级（vue-tsc 与 TS 7 不兼容，附实测数据） |
| [ADR-015](adr/ADR-015-reference-project-adoption.md) | 参考项目借鉴决策：vue-pure-admin 点状移植边界 / jumpserver 机制借鉴矩阵 / 审批流可视化不引入 |
| [ADR-016](adr/ADR-016-approval-flow-phase2.md) | 审批流引擎二期：节点出口路由（排他网关）/ 版本快照与回滚 / RATIO 比例会签 / @vue-flow 画布 |
| [ADR-017](adr/ADR-017-ldap-directory-sync.md) | LDAP/AD 目录同步：bind 认证接入认证链（优先级可配、降级不阻断本地）/ OU→部门树 + 用户定时同步 / 冲突审计 / 凭据值级加密 |
| [ADR-018](adr/ADR-018-im-scan-login.md) | 企业 IM 扫码登录：钉钉/企微/飞书 flavor 适配器（官方端点预设、企微 corp token 缓存）/ 绑定唯一与 MFA 回归沿用 / OAUTH_PROVIDERS 写侧校验接线 |
| [ADR-019](adr/ADR-019-im-notify-channels.md) | 企业 IM 消息渠道：三家发送 SDK（token 缓存/unionId 换 userid）/ 收件账号按 flavor 复用 OAuth 绑定 / notify_im 配置值级加密 |
| [ADR-020](adr/ADR-020-dataset-dashboard-phase1.md) | 数据集 + 仪表盘一期：模型/字段/op 白名单受控查询（行级数据权限 fail-closed）/ 布局 JSON + 四种图表卡片 / 个人·共享两档 / 评估出口条款 |
| [ADR-021](adr/ADR-021-dashboard-display-and-reports.md) | 仪表盘二期 + 报表轻量版：Screen 大屏模板与全屏轮播（后端极薄）/ Report 定时报表（复用下载中心产物 + 邮件附件，创建者权限上下文）/ 明示不做边界 |
| [ADR-022](adr/ADR-022-outbound-webhooks.md) | 出站 Webhook：事件目录 + 唯一发射口（吞异常）/ HMAC-SHA256 时间戳签名（secret 值级加密）/ 指数退避 5 次 + 耗尽告警 / 投递审计与重试 |
| [ADR-023](adr/ADR-023-ai-assistant-phase1.md) | AI 一期（使用/二开助手）：OpenAI 兼容供应商中立接入层 / docs/ 分块入库 + 词频检索（向量升级候选池）/ ask 引用出处 / 权限门控 + 密钥值级加密（G12 模式先行） |
| [ADR-024](adr/ADR-024-nl-query-phase2.md) | AI 二期 NL 查数：LLM 只产出受限数据集 DSL（LLM 输出按不可信输入处理）/ 服务端白名单重校验 + 数据权限 fail-closed / 试算预览 + 限幅 + 语义审计（AuthType.AI） / 灰度默认关 |
| [ADR-025](adr/ADR-025-dynamic-form-phase1.md) | 动态表单一期：8 种收敛控件集 JSON Schema（写入/提交双侧校验）/ 通用 JSON 存储 + creator 隔离 / 零新依赖自定义动态表格（RePlusPage 动态列登记二期） |
| [ADR-026](adr/ADR-026-dynamic-form-approval.md) | 动态表单二期（G5b）：表单定义开关 `approval_required` + 提交复用敏感操作审批协议（412 一次性令牌重放）/ 校验在前审批在后 / 超管直提（单管理员部署防死锁）/ 前端设计器开关与填报标记 |
| [ADR-027](adr/ADR-027-code-generator.md) | 代码生成器（G7）：`generate_crud` 管理命令（Model → 序列化器/视图/路由/配置 + 前端页面 + 菜单种子）/ 生成块幂等合并 + import 去重 / 输出即过 ruff 门禁（生成器单测含 ruff 校验） |
| [ADR-028](adr/ADR-028-global-search.md) | 全局搜索（G9）：顶栏搜索弹窗内跨实体分组结果（用户/部门/文件/审批单/日志）/ 逐实体两道门（页面权限门 + 数据权限编译器 fail-closed）/ 检索基线 icontains（转义反破坏匹配已实证，Postgres 全文化为评估出口）/ 权限码 retrieve:SystemGlobalSearch 入种子 |
| [ADR-029](adr/ADR-029-page-watermark.md) | 敏感页面水印（G10）：基本设置三项配置（开关 / 文案 / 生效页面路由前缀）/ 文案含时间并分钟级刷新 / 挂载与清除收敛到 App.vue（移除 store 里的水印 hack），菜单级开关与防篡改列为评估出口 |
| [ADR-031](adr/ADR-031-multi-tenant-evaluation.md) | 多租户 go/no-go 评估（2027-09）：**结论 no-go（暂不做）**——一租户一实例为物理隔离、改造面 ≥6 窗口且与数据权限编译器高风险耦合；登记重开条件与 schema-per-tenant 预研要点 |
| [ADR-030](adr/ADR-030-open-platform.md) | 开放平台雏形（G11）：`ApiApplication` 应用发卡机复用 PAT 认证链（sha256 口径/三层权限/审计）/ client-credentials 换发端点（明文仅一次、轮换即失效）/ 按应用限流（认证处计数 429）/ 回调注册 + HMAC 测试投递；不做应用级权限体系与 OAuth 授权码 |
| [ADR-032](adr/ADR-032-approval-business-integration.md) | 审批接入业务系统：通用业务绑定 `ApprovalInstance.biz_type/biz_id`（不引 ContentType）+ 终态回调 `approval_instance_finished` 信号（终态走 update 不触发 post_save）/ 首个真实业务「请假」（新增即提交、fail-closed 退化草稿、状态由终态回写、审批动作只在流程审批中心）/ 敏感操作审批挂载点扩到角色·部门删除（默认仍休眠）/ 字典·菜单·流程定义随种子下发 |
| [ADR-033](adr/ADR-033-knowledge-base-management.md) | AI 知识库文档管理：`AiKnowledgeDocument` 双来源（repo/upload）统一登记 + 既有分块表即检索面（retrieve 零改动）/ 上传=文本入库（浏览器读文件，不落文件系统；同名覆盖更新）/ 预览=详情全文 + 分块摘要（列表轻量；原文展示不引 md 渲染依赖）/ 停用=移除分块、删除仅 upload / sync 只维护 repo（upload 前缀隔离 + 孤儿块清理，守护测试钉死） |
| [ADR-034](adr/ADR-034-chat-room-rebuild.md) | 聊天室重构（微信式两栏）：`ChatRoom/ChatRoomMember/ChatMessage` 三表（room_key 幂等 + 未读游标 + client_msg_id 幂等 + 2 分钟撤回）/ 新通道 `ws/chat/`（显式组名、不登记会话、心跳不污染在线索引）/ `/api/chat/` 六接口 + 6 权限点 / 私聊与 AI 双形态（多轮 + `/kb` RAG 带引用）/ 前端两栏骨架（气泡/时间分组/游标加载/未读红点/@联想） |
| [ADR-035](adr/ADR-035-api-contract-governance.md) | API 契约治理：**不做 URL 版本化（no-go，登记 3 条重开条件）**——消费者以同仓前端为主；契约唯一真源 `docs/schema/` + 前端镜像 `check:contract` + 服务端守护测试兜底；新路由 basename 统一 kebab-case、存量不改名（不影响权限链，仅监控 label 断档的纯 churn） |
| [ADR-036](adr/ADR-036-import-export-replay-decision.md) | 导入导出 WSGIRequest 重放：**保留现状不重构**（与同步路径 100% 同源是既定意图，重构需先补装配契约测试）——装配点补 5 个隐式契约注释清单 + 登记重构步骤与重开条件 |
| [ADR-037](adr/ADR-037-ai-retrieval-evaluation.md) | AI 检索升级评估（评测驱动）：**暂不引入向量**——36 问评测集入 CI 实测 hit@5 97.2%（535 块 / 30ms），远高于 75% 门控；登记评估出口（hit@5<75% / 分块>1000 / P95>300ms）与升级预研要点（OpenAI 兼容 embedding + Python 余弦 + RRF） |
| [ADR-038](adr/ADR-038-ai-actions.md) | AI 助手受限动作（A2）：白名单动作注册表（请假/动态表单）+ 聊天 `/do` 草稿 + 确认卡片 + 权限双门 + 412 审批协议复用 + auth_type=ai 审计 + 灰度默认关；随项修复 SSE 端点浏览器 406 不可用的既有缺陷（EventStreamRenderer） |
| [ADR-039](adr/ADR-039-open-platform-phase2.md) | 开放平台二期（B1–B4 全量）：应用级四级授权（模型×动作×字段×行，只收敛不提权）+ OAuth 授权码（PKCE/refresh/revoke/同意页）+ 用量报表与每日配额软告警 + Webhook 事件契约（schema_version + 自动文档 + 守护测试）；接入指南与示例客户端见 [open-platform/](open-platform/README.md) |
| [ADR-040](adr/ADR-040-approval-flow-phase3.md) | 审批流三期：**动作 MFA 二次确认已交付**（`APPROVAL_MFA_REQUIRED_ACTIONS` 逐动作灰度 + 412 `user_confirm_required` 复用 + 未验证不推进业务状态守护）；**委托代理设计已定待实施**（委托表 + `resolve_assignees` 出口改造 + 不递归防环 + 审计标注代审） |
| [ADR-041](adr/ADR-041-report-cron-expression.md) | 定时报表 cron 表达式：引入 `croniter`（纯 Python，pin 6.0.0）+ `Report.cron_expression`（非空覆盖三档频次）+ 分钟级判定（非法 fail-closed）+ 新增每分钟分发任务（与原每小时任务职责互斥，存量零变化） |
| [ADR-042](adr/ADR-042-dashboard-card-permission.md) | 仪表盘卡片级权限（一二期全交付）：一期 `layout[].allowed_roles`（内嵌授权面，未知角色 code 拒绝）+ 读取侧按浏览者角色过滤（超管全量 / 匿名 fail-closed，只收敛不提权）；二期字段权限叠加到执行/聚合输出（无字段配置=全量的显式授权口径）+ 卡片弹窗「可见角色」授权 UI + 越权矩阵补强（18 例测试） |
| [ADR-043](adr/ADR-043-remote-suggestions.md) | 远程联想（suggestions）：引用方 `SuggestionsAction`（`{prefix}/suggestions?field=`，候选集与写入校验同源，权限回落 list 权限点，零新权限点）+ ViewSet 级 `suggestion_fields` 字段白名单（元数据 `suggest_url` 与端点校验共用声明，含 with_meta=1 内联路径）+ 前端 `SuggestSelect`（remote/防抖/pks 回显）。首个消费方=审批委托「代理人」（委托人保持弹窗；部门管理经用户决策不采用）；菜单管理「自动添加API权限」登记为不适用场景 |

## 项目规划与治理（plans/）

跨仓库（server + client）的项目规划文档，2026-09-12 自工作区根目录 `docs/` 迁入并二次清理，
2026-09-14 三次清理（已完成职能的规划文档删除，去向登记于 [plans/README.md](plans/README.md)）：

- [下一年度规划建议-2027.10-2028.09.md](plans/下一年度规划建议-2027.10-2028.09.md) —— **下一年度排期建议稿（待评审）**：现状复核 + 立即收口 + 方向选择 + 里程碑与验证矩阵
- [年度开发计划-2026.10-2027.09.md](plans/年度开发计划-2026.10-2027.09.md) —— 2026.10–2027.09 排期台账（**已完成，12/12 窗口**；含缺口对标分析与逐窗口验收记录）
- [年度回顾-2026.10-2027.09.md](plans/年度回顾-2026.10-2027.09.md) —— 年度回顾：窗口完成度、能力盘点、KPI 对照、遗留交接与下一年度方向

## 契约与规范（schema/ + 根级）

| 文档                                                                     | 内容                                  |
|------------------------------------------------------------------------|-------------------------------------|
| [schema/search-columns.schema.json](schema/search-columns.schema.json) | search-columns 响应契约                 |
| [schema/search-fields.schema.json](schema/search-fields.schema.json)   | search-fields 响应契约                  |
| [exception-handling.md](exception-handling.md)                         | 错误脱敏原则 + 错误码登记表（新增错误码必须先登记）         |
| [security-review.md](security-review.md)                               | 安全自查归档（Flower/XFrame/Referer/上传校验/JWT 审计等） |
| [cache-keys-audit.md](cache-keys-audit.md)                             | 缓存键审计（N5）：`scripts/check_cache_keys.py --strict` 冲突清零记录 |
| [metrics.md](metrics.md)                                               | 基线指标看板（半年规划 T1.8）：测试/体积/性能 KPI 基线与各阶段实测回填 |
| [框架开发遵循准则.md](框架开发遵循准则.md)                               | 服务端 + 前端开发统一约定与检查清单：响应/Model/Serializer/ViewSet、RePlusPage 模式、i18n、常见坑速查 |

## 维护约定

- 新增文档先在本索引登记；架构类文档入 `architecture/`，决策类入 `adr/`（新建 ADR 编号顺延），部署运维入 `ops/`；
- ADR 状态变更需同步更新本索引表格；
- API 文档随版本固化（T6.3）：每次 release 自动附带静态 `openapi.json`（drf-spectacular 导出，见 `build-image.yml`），并可在部署环境访问
  `/api-docs/` 交互查阅；
- `history/XADMIN_FRAMEWORK_ANALYSIS.md` 为历史深度分析（2026-09-12 自仓库根目录归档），内容已由
  [architecture/overview.md](architecture/overview.md) 导航收录，以代码与 overview 为准；
- 跨仓库规划/排期文档入 `plans/`（先登记 plans/README.md），架构类文档入 `architecture/`，决策类入 `adr/`。
