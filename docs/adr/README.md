# 架构决策记录（ADR）索引

> 本目录是**历史决策记录**（"当时为什么这样选"），描述**现状**的文档请读
> [docs/README.md](../README.md)。新增决策按编号顺延并登记本索引。
>
> 二开常用速查：[ADR-027 代码生成器](ADR-027-code-generator.md)（`generate_crud`）、
> [ADR-045 模块化与裁剪](ADR-045-modular-trimmable-architecture.md)、
> [ADR-007 FormData 协议](ADR-007-multipart-form-data-v1-protocol.md)、
> [ADR-043 远程联想](ADR-043-remote-suggestions.md)、
> [ADR-035 契约治理](ADR-035-api-contract-governance.md)。

| ADR | 主题 |
|-----|------|
| [ADR-001](ADR-001-csrf-jwt-only.md)      | CSRF 中间件不启用（JWT-only 架构）           |
| [ADR-002](ADR-002-demo-app.md)           | demo app 去留：保留但默认关闭                |
| [ADR-003](ADR-003-websocket-protocol.md) | WebSocket 协议保持自定义格式并补类型约束          |
| [ADR-004](ADR-004-django-60-upgrade.md)  | Django 升级线：当前运行 6.0.8；6.1 被 beat 声明阻断（6.2 LTS 发布后按复审口径复核） |
| [ADR-005](ADR-005-redis-split.md)        | Redis 拆分（缓存/队列/会话分实例）              |
| [ADR-006](ADR-006-asgi-db-connection-pool.md) | ASGI 形态启用 Django server 端 DB 连接池      |
| [ADR-007](ADR-007-multipart-form-data-v1-protocol.md) | FormData 上传协议 v1（点分键序列化契约）       |
| [ADR-008](ADR-008-pat-auth.md)           | 个人访问令牌（PAT）：scope + 精确审计           |
| [ADR-009](ADR-009-data-mask-exemption.md) | 数据脱敏豁免清单机制                          |
| [ADR-010](ADR-010-crypto-es.md)          | crypto-js 弃用处置：替换为 crypto-es        |
| [ADR-011](ADR-011-aes-protocol-v2.md)    | 凭证加密协议升级 v2（WebCrypto PBKDF2+AES-GCM 双格式过渡） |
| [ADR-012](ADR-012-approval-flow-engine.md) | 审批流引擎（模板/实例/任务/加签/催办，含触发器与数据权限） |
| [ADR-013](ADR-013-office-online-preview.md) | Office 在线预览选型：LibreOffice headless 转 PDF（重队列 + 缓存回收） |
| [ADR-014](ADR-014-typescript-7-evaluation.md) | TypeScript 7 升级评估：暂不升级（vue-tsc 与 TS 7 不兼容，附实测数据） |
| [ADR-015](ADR-015-reference-project-adoption.md) | 参考项目借鉴决策：vue-pure-admin 点状移植边界 / jumpserver 机制借鉴矩阵 / 审批流可视化不引入 |
| [ADR-016](ADR-016-approval-flow-phase2.md) | 审批流引擎二期：节点出口路由（排他网关）/ 版本快照与回滚 / RATIO 比例会签 / @vue-flow 画布 |
| [ADR-017](ADR-017-ldap-directory-sync.md) | LDAP/AD 目录同步：bind 认证接入认证链（优先级可配、降级不阻断本地）/ OU→部门树 + 用户定时同步 / 冲突审计 / 凭据值级加密 |
| [ADR-018](ADR-018-im-scan-login.md) | 企业 IM 扫码登录：钉钉/企微/飞书 flavor 适配器（官方端点预设、企微 corp token 缓存）/ 绑定唯一与 MFA 回归沿用 / OAUTH_PROVIDERS 写侧校验接线 |
| [ADR-019](ADR-019-im-notify-channels.md) | 企业 IM 消息渠道：三家发送 SDK（token 缓存/unionId 换 userid）/ 收件账号按 flavor 复用 OAuth 绑定 / notify_im 配置值级加密 |
| [ADR-020](ADR-020-dataset-dashboard-phase1.md) | 数据集 + 仪表盘一期：模型/字段/op 白名单受控查询（行级数据权限 fail-closed）/ 布局 JSON + 四种图表卡片 / 个人·共享两档 / 评估出口条款 |
| [ADR-021](ADR-021-dashboard-display-and-reports.md) | 仪表盘二期 + 报表轻量版：Screen 大屏模板与全屏轮播（后端极薄）/ Report 定时报表（复用下载中心产物 + 邮件附件，创建者权限上下文）/ 明示不做边界 |
| [ADR-022](ADR-022-outbound-webhooks.md) | 出站 Webhook：事件目录 + 唯一发射口（吞异常）/ HMAC-SHA256 时间戳签名（secret 值级加密）/ 指数退避 5 次 + 耗尽告警 / 投递审计与重试 |
| [ADR-023](ADR-023-ai-assistant-phase1.md) | AI 一期（使用/二开助手）：OpenAI 兼容供应商中立接入层 / docs/ 分块入库 + 词频检索（向量升级候选池）/ ask 引用出处 / 权限门控 + 密钥值级加密（G12 模式先行） |
| [ADR-024](ADR-024-nl-query-phase2.md) | AI 二期 NL 查数：LLM 只产出受限数据集 DSL（LLM 输出按不可信输入处理）/ 服务端白名单重校验 + 数据权限 fail-closed / 试算预览 + 限幅 + 语义审计（AuthType.AI） / 灰度默认关 |
| [ADR-025](ADR-025-dynamic-form-phase1.md) | 动态表单一期：8 种收敛控件集 JSON Schema（写入/提交双侧校验）/ 通用 JSON 存储 + creator 隔离 / 零新依赖自定义动态表格（RePlusPage 动态列登记二期） |
| [ADR-026](ADR-026-dynamic-form-approval.md) | 动态表单二期（G5b）：表单定义开关 `approval_required` + 提交复用敏感操作审批协议（412 一次性令牌重放）/ 校验在前审批在后 / 超管直提（单管理员部署防死锁）/ 前端设计器开关与填报标记 |
| [ADR-027](ADR-027-code-generator.md) | 代码生成器（G7）：`generate_crud` 管理命令（Model → 序列化器/视图/路由/配置 + 前端页面 + 菜单种子）/ 生成块幂等合并 + import 去重 / 输出即过 ruff 门禁（生成器单测含 ruff 校验） |
| [ADR-028](ADR-028-global-search.md) | 全局搜索（G9）：顶栏搜索弹窗内跨实体分组结果（用户/部门/文件/审批单/日志）/ 逐实体两道门（页面权限门 + 数据权限编译器 fail-closed）/ 检索基线 icontains（转义反破坏匹配已实证，Postgres 全文化为评估出口）/ 权限码 retrieve:SystemGlobalSearch 入种子 |
| [ADR-029](ADR-029-page-watermark.md) | 敏感页面水印（G10）：基本设置三项配置（开关 / 文案 / 生效页面路由前缀）/ 文案含时间并分钟级刷新 / 挂载与清除收敛到 App.vue（移除 store 里的水印 hack），菜单级开关与防篡改列为评估出口 |
| [ADR-030](ADR-030-open-platform.md) | 开放平台雏形（G11）：`ApiApplication` 应用发卡机复用 PAT 认证链（sha256 口径/三层权限/审计）/ client-credentials 换发端点（明文仅一次、轮换即失效）/ 按应用限流（认证处计数 429）/ 回调注册 + HMAC 测试投递；不做应用级权限体系与 OAuth 授权码 |
| [ADR-031](ADR-031-multi-tenant-evaluation.md) | 多租户 go/no-go 评估（2027-09）：**结论 no-go（暂不做）**——一租户一实例为物理隔离、改造面 ≥6 窗口且与数据权限编译器高风险耦合；登记重开条件与 schema-per-tenant 预研要点 |
| [ADR-032](ADR-032-approval-business-integration.md) | 审批接入业务系统：通用业务绑定 `ApprovalInstance.biz_type/biz_id`（不引 ContentType）+ 终态回调 `approval_instance_finished` 信号（终态走 update 不触发 post_save）/ 首个真实业务「请假」（新增即提交、fail-closed 退化草稿、状态由终态回写、审批动作只在流程审批中心）/ 敏感操作审批挂载点扩到角色·部门删除（默认仍休眠）/ 字典·菜单·流程定义随种子下发 |
| [ADR-033](ADR-033-knowledge-base-management.md) | AI 知识库文档管理：`AiKnowledgeDocument` 双来源（repo/upload）统一登记 + 既有分块表即检索面（retrieve 零改动）/ 上传=文本入库（浏览器读文件，不落文件系统；同名覆盖更新）/ 预览=详情全文 + 分块摘要（列表轻量；原文展示不引 md 渲染依赖）/ 停用=移除分块、删除仅 upload / sync 只维护 repo（upload 前缀隔离 + 孤儿块清理，守护测试钉死） |
| [ADR-034](ADR-034-chat-room-rebuild.md) | 聊天室重构（微信式两栏）：`ChatRoom/ChatRoomMember/ChatMessage` 三表（room_key 幂等 + 未读游标 + client_msg_id 幂等 + 2 分钟撤回）/ 新通道 `ws/chat/`（显式组名、不登记会话、心跳不污染在线索引）/ `/api/chat/` 六接口 + 6 权限点 / 私聊与 AI 双形态（多轮 + `/kb` RAG 带引用）/ 前端两栏骨架（气泡/时间分组/游标加载/未读红点/@联想） |
| [ADR-035](ADR-035-api-contract-governance.md) | API 契约治理：**不做 URL 版本化（no-go，登记 3 条重开条件）**——消费者以同仓前端为主；契约唯一真源 `docs/schema/` + 前端镜像 `check:contract` + 服务端守护测试兜底；新路由 basename 统一 kebab-case、存量不改名（不影响权限链，仅监控 label 断档的纯 churn） |
| [ADR-036](ADR-036-import-export-replay-decision.md) | 导入导出 WSGIRequest 重放：**保留现状不重构**（与同步路径 100% 同源是既定意图，重构需先补装配契约测试）——装配点补 5 个隐式契约注释清单 + 登记重构步骤与重开条件 |
| [ADR-037](ADR-037-ai-retrieval-evaluation.md) | AI 检索升级评估（评测驱动）：**暂不引入向量**——36 问评测集入 CI 实测 hit@5 97.2%（535 块 / 30ms），远高于 75% 门控；登记评估出口（hit@5<75% / 分块>1000 / P95>300ms）与升级预研要点（OpenAI 兼容 embedding + Python 余弦 + RRF） |
| [ADR-038](ADR-038-ai-actions.md) | AI 助手受限动作（A2）：白名单动作注册表（请假/动态表单）+ 聊天 `/do` 草稿 + 确认卡片 + 权限双门 + 412 审批协议复用 + auth_type=ai 审计 + 灰度默认关；随项修复 SSE 端点浏览器 406 不可用的既有缺陷（EventStreamRenderer） |
| [ADR-039](ADR-039-open-platform-phase2.md) | 开放平台二期（B1–B4 全量）：应用级四级授权（模型×动作×字段×行，只收敛不提权）+ OAuth 授权码（PKCE/refresh/revoke/同意页）+ 用量报表与每日配额软告警 + Webhook 事件契约（schema_version + 自动文档 + 守护测试）；接入指南与示例客户端见 [open-platform/](../open-platform/README.md) |
| [ADR-040](ADR-040-approval-flow-phase3.md) | 审批流三期：**动作 MFA 二次确认已交付**（`APPROVAL_MFA_REQUIRED_ACTIONS` 逐动作灰度 + 412 `user_confirm_required` 复用 + 未验证不推进业务状态守护）；**委托代理已交付**（2026-09-15：委托表 + `resolve_assignees` 出口改造 + 不递归防环 + 审计标注代审） |
| [ADR-041](ADR-041-report-cron-expression.md) | 定时报表 cron 表达式：引入 `croniter`（纯 Python，pin 6.0.0）+ `Report.cron_expression`（非空覆盖三档频次）+ 分钟级判定（非法 fail-closed）+ 新增每分钟分发任务（与原每小时任务职责互斥，存量零变化） |
| [ADR-042](ADR-042-dashboard-card-permission.md) | 仪表盘卡片级权限（一二期全交付）：一期 `layout[].allowed_roles`（内嵌授权面，未知角色 code 拒绝）+ 读取侧按浏览者角色过滤（超管全量 / 匿名 fail-closed，只收敛不提权）；二期字段权限叠加到执行/聚合输出（无字段配置=全量的显式授权口径）+ 卡片弹窗「可见角色」授权 UI + 越权矩阵补强（18 例测试） |
| [ADR-043](ADR-043-remote-suggestions.md) | 远程联想（suggestions）：引用方 `SuggestionsAction`（`{prefix}/suggestions?field=`，候选集与写入校验同源，权限回落 list 权限点，零新权限点）+ ViewSet 级 `suggestion_fields` 字段白名单（元数据 `suggest_url` 与端点校验共用声明，含 with_meta=1 内联路径）+ 前端 `SuggestSelect`（remote/防抖/pks 回显）。首个消费方=审批委托「代理人」（委托人保持弹窗；部门管理经用户决策不采用）；菜单管理「自动添加API权限」登记为不适用场景 |
| [ADR-044](ADR-044-dform-approval-integration.md) | 动态表单与审批流集成（走查五项）：表单绑定审批流程（`approval_flow` + 提交状态/实例 + 终态回写 + 驳回重提）、审批通过自动完成提交（请求体快照 + 通过后动作注册表，multipart 仍走手动重放）、控件扩到 11 种（附件/日期范围/明细子表，禁嵌套）、部门授权写入修复（原静默丢弃）+ 数据权限 fail-closed 可诊断报错、`seed_demo_org` 开箱模板（组织+四层权限+场景模板） |
| [ADR-045](ADR-045-modular-trimmable-architecture.md) | 功能模块化与可裁剪架构（二开友好）：三级分层（core/standard/optional）+ 发行预设（**不做插件市场**）+ 模块声明单一事实源（内置 `MODULES` + app 侧 `{app}/modules.py` 扩展点）+ 六层裁剪（路由 404 / **WS 通道准入**（2026-09-18 增量）/ 菜单权限隐藏 / 周期任务不注册 / 种子裁剪 / 缓存清理）+ CLI（`modules` 清单预演、`module remove` 硬裁剪归档回滚、`generate_module` 脚手架）+ 只读「模块管理」页；默认 `full` 零行为差异；P2b/P4b 已评估关闭并登记触发条件 |
| [ADR-046](ADR-046-module-depth-completion.md) | 三大模块深度完善：表单草稿（DRAFT 轻校验 + `submit` 端点 + 操作审批自动落库与重放保序）与模板复用（`is_template` 同表 + `kind=templates`，不新增权限点）、数据字典驱动选项（schema `dict` 与内联 options 互斥，提交校验 fail-closed）、设计器字段排序与完整属性、提交详情与审批轨迹抽屉；数据分析修「可选但必失败」的度量字段（`numeric_columns`）、卡片错误可见化、看板刷新/设置、伪模型过滤与预览 CSV 导出；审批中心通过意见、人工催办（10 分钟节流）+ 流转时间线 + 代理标注（`delegate_from`）+ 驳回重提预填 + 分支路由 target 恒禁用缺陷修复 |
| [ADR-047](ADR-047-ai-console-persistence-and-tools.md) | AI 助手对话页改版：左右分栏（三入口导航按权限组装）、对话持久化（`AiChatMessage` 按 (creator, feature) 组织，流式 done/error 携带服务端载荷）、指令执行入口（`action/interpret/stream` 草稿流式 + 确认卡片 + 执行结果落库）、统一工具目录（`tools` 端点，MCP tools/list 等价 inputSchema）与五条声明式动作（user.search / notice.list / user.update / role.create / role.grant，配套 IN_QUERY / role / menu 参数类型与 `target` 改名）、思考面板滚动跟随与「思考中」去重 |
| [ADR-048](ADR-048-ai-unified-tool-layer.md) | AI 统一工具层：声明式动作注册表全量扩容（9 → 60 个动作，覆盖用户/组织/权限/公告/监控/数据集/任务/配置/日志/审批，按域拆分 registry 文件 + 路径可解析守护测试）、高危动作 AI 层强制审批与业务层审批穿透（`_approval_pre_authorized` 防双重审批死循环，超管豁免与 dform 同口径）、MCP 协议端点（`POST /api/system/ai/mcp`，JSON-RPC 2.0 无状态：initialize/tools/list/tools/call，PAT 认证，高危动作拒绝 + 全量审计）、多动作草稿串联（1~3 个/请求，`actions` 数组契约兼容旧单对象，前端 AiActionCard 复用 + testid 前缀保 E2E 选择器）；密码二次确认类端点（删除用户/重置 MFA/重置密码）明确排除 |
| [ADR-050](ADR-050-jumpserver-batch1-security-and-decision.md) | JumpServer 对标批一（安全与决策信息完备）：AI 安全护栏（引用数据隔离 + 注入标记 + 输出脱敏含流式 + 动作目标行级复核 + 审计 guard 摘要）、凭据治理（敏感键注册表 + 值内加密读写收口 + 明文守护 + `rotate_credential` 轮换命令 + 凭据只读页）、审批决策信息（`target_snapshot` 变更对照 + 关联业务对象卡片 + 处理人显示名快照）、影响面预检与引用保护（8 类计算器 + `/impact` 动作 + `IMPACT_GUARD_MODELS` fail-closed 开关 + 删除前弹窗）、表单错误定位与随机密码按钮 |
| [ADR-049](ADR-049-approval-concurrency-and-ai-permission-parity.md) | 审批动作并发安全与 AI 工具层权限口径对齐（上线前缺陷修复 + 二批能力补全）：权限预检与运行时白名单同源（`match_permission_white_url`）、声明式动作「method 一致性 + 权限点种子覆盖」双守护、审批动作统一实例行锁 + 终态 CAS、审批包拆 `extra_actions.py`、演示角色补 AI 章节授权（排除 MCP）、前端 i18n 词条门禁入 CI；**二批**：转交（transfer，原任务作废留痕 + 新任务来源标注）、管理视角「全部在途」（`ongoing:SystemApprovalInstance` 功能授权 + `current_assignees` 巡看列）、加签按节点类型收口（OR 明确拒绝）、AI 结构化输出客户端统一（`structured_chat_client`）、前端 SearchUser 载荷/事件缺陷修复；**三批**：批量转交（逐条独立 + 失败明细）、审批导出（轻量序列化器 + 修复全站 type=csv 被协商忽略的既有缺陷）、比例会签达标线预览（`node_progress_for` 与 engine 同源，加签抬升）、loadjson 追加条目 pk 唯一性教训 |
| [ADR-051](ADR-051-jumpserver-batch2-ai-platform-and-ux.md) | JumpServer 对标批二（AI 深化与平台底座）：能力画像与探测（`capabilities`/`probed_at`/`purpose` 用途级档案分流 + `probe` 端点四能力探测 + 判据收口）、原生 function calling 双轨（`chat_tools` + 工具定义转换层 + `AI_NATIVE_TOOLS_ENABLED` 开关 + 对照日志 + prompt-JSON 回落）、工具面巡检与 OpenAPI AI 元数据（`ai_tool_audit` + `x-ai-*` 注入）、执行幂等（`draft_id` + TTL 10min + `force` 通道 + MCP 同口径）、用量账本与三级配额（`AiUsageRecord` + `tracked_*` 收口 + 并发流式信号量 + `usage` 端点）、通用标签中心（Tag/TaggedItem + 3 对象白名单 + 打标回落业务权限 + `?tag=` 过滤 + 管理页/打标弹窗）、任务中心（三类记录只读聚合 + 协作式取消 + 白名单重跑 + 新页面）、命令面板 Cmd+K（与顶栏搜索合流 + 快捷动作 + 跨区键盘导航）、表格偏好持久化（`useTablePrefs` 本地 + 跨设备双层；表头排序登记遗留）、SQL 计数基线门禁（10 端点入 CI）、生成器「生成即接入」（`ai_declarations.py` 骨架 + `--with-tags/--with-tests` + doctor 检查） |
| [ADR-052](ADR-052-jumpserver-batch3-usability-and-security.md) | JumpServer 对标批三（好用功能补齐 F 线八项）：批量更新（helper + 白名单 5 类模型 + 前端通用弹窗）、通知消息模板可配置（覆盖表 + 注册表 + 沙箱渲染 + 全渠道渲染收口）、列表「我的视图」（个人取值域 CRUD + 默认互斥 + 共享；前端下拉登记遗留）、审批协作（讨论区评论 + @提醒 + 抄送人可见域与终态知会；前端抄送人选择登记遗留）、账号安全巡检（六类风险 + 幂等 + 处置留痕 + 强制改密引导）、登录访问策略（时段 / 网段 × 对象 + 命中预演 + 并发会话上限）、文件访问审计（四动作留痕 + 受鉴权下载端点 + 上传扩展名策略）、Passkey（自实现 WebAuthn 验签 + 绑定管理 + 登录 MFA 双链路 + 认证方式三层策略） |
| [ADR-053](ADR-053-jumpserver-batch4-triggered-and-engineering.md) | JumpServer 对标批四（触发制与工程改造）：依赖工程化（pyproject 单源 + uv.lock + requirements 导出产物 + 三方一致性守护）、审计归档与冷热分层（整月归档 + **归档水位驱动清理**「删必已归档」+ 离线恢复查询 + 校验）、关联计数声明式（`RelationCountMixin` + 角色/数据集/部门试点 + 计数可点击跳转）、API 查询能力（受控 lookup：字段面不扩张 / 禁跨关系 / 字段可见性 fail-closed；`?fields=` 字段子集）、邀请开户与账号有效期（不可用密码待激活 + 一次性令牌 + 邮件邀请 + 到期拦截/提醒/自动停用）、图表与大屏图片导出（SVG→PNG + 手写 store ZIP，零新依赖）；P-4 存储后端 / F-10 OIDC 触发未命中登记不实施 |
| [ADR-054](ADR-054-remaining-u3-storage-oidc-and-cleanup.md) | JumpServer 对标剩余项收口：**U-3 表头排序**（元数据 `sortable` 契约扩展 + `useTableSort` 与搜索区 ordering 同源 + 初始化不回显）、**P-4 文件存储后端可插拔**（`SwitchableStorage` 委托 local/s3 + 9 个 SysConfig 键（密钥加密）+ 可选依赖缺失回退本地 + 适配层统一访问 + `storage_migrate` 搬迁校验 + health 探针 + 文档）、**F-10 标准 OIDC**（discovery + id_token JWKS 验签（算法白名单/kid 轮换刷新）+ claims 映射 + 组角色映射（同 LDAP 口径）+ nonce 与 state 绑定）、**遗留清账**（`ai_tool_audit` 资源域 triage 缺口清零入 CI、登录日志自动归档清理、F-12 存量两处迁移声明式、F-13 前端高级筛选 opt-in、冷归档恢复演练落台账、approval-chain E2E 根因修复） |
| [ADR-055](ADR-055-jumpserver-legacy-cleanup-and-evaluations.md) | JumpServer 对标遗留零余量收口与评估出口评估：**技术遗留清零**（F-11 创建即邀请（邮件渠道 + 邀请权限双前置，fail-closed 不产生死号）/ AI-2 双轨对照统计（`AiUsageRecord.track` + `by_track` 成功率 + 前端展示）/ AI-1 vision 探测入口（默认仍三项）/ P-2 统一进度助手（`task_progress.update_progress` + `stage` 字段 + 报表中间里程碑 + 任务中心展示）/ P-4 预签名直连（`?direct=1`，鉴权与审计先于签发）与 mirror 双写搬迁窗口 / E-1 依赖显式声明（pyjwt·cryptography·cbor2）+ storage extras + 守护 / F-13 高级筛选第三页 / celery 注释清账）；**评估出口逐项结案**（独立 AI worker · 审计外部后端 · 服务端无头 PDF · Vault·KMS · ClamAV · CAS·SAML · prompt-JSON 下线：结论 + 依据 + 重开条件）；边界登记（预签名上传直传不做、字典/流程计数无跳转目标） |
